"""Single-process rooms and server-authoritative, five-round games."""

import asyncio
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import HTTPException

from backend.models import User
from backend.mother_client import InsufficientTickets, MotherClient

if TYPE_CHECKING:
    from backend.realtime import RoomRealtime


@dataclass
class RoomPlayer:
    user_id: str
    display_name: str
    ready: bool = False
    connected: bool = False


@dataclass
class Round:
    number: int
    box_values: list[int]
    deadline: float  # Monotonic; never serialized for a client countdown.
    deadline_at: float  # Unix timestamp in seconds for clients.
    phase: str = "CHOOSING"
    choices: dict[str, int] = field(default_factory=dict)


@dataclass
class Game:
    participant_ids: tuple[str, ...]
    scores: dict[str, int]
    round_number: int = 0
    current_round: Round | None = None
    winner_ids: list[str] = field(default_factory=list)


@dataclass
class Room:
    code: str
    players: dict[str, RoomPlayer]
    status: str = "WAITING"
    game: Game | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    start_key: str = field(default_factory=lambda: f"room-start-{uuid4().hex}")
    start_participants: tuple[str, ...] = ()
    start_in_flight: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    revision: int = 0


class RoomManager:
    def __init__(
        self,
        *,
        round_duration: float = 10.0,
        reveal_duration: float = 2.0,
        rng: random.Random | None = None,
    ) -> None:
        if round_duration <= 0 or reveal_duration < 0:
            raise ValueError("Invalid round durations")
        self.round_duration = round_duration
        self.reveal_duration = reveal_duration
        self.rng = rng if rng is not None else random.SystemRandom()
        self.rooms: dict[str, Room] = {}
        self.creation_lock = asyncio.Lock()
        self.realtime: RoomRealtime | None = None

    def _changed(self, room: Room) -> None:
        # Called under the room lock: capture each transition before another
        # mutation can run. Socket delivery happens separately, without the lock.
        room.revision += 1
        if self.realtime is not None:
            self.realtime.room_changed(room)

    def _room(self, code: str) -> Room:
        room = self.rooms.get(code.upper())
        if room is None:
            raise HTTPException(404, "Room not found")
        return room

    @staticmethod
    def _member(room: Room, user_id: str) -> RoomPlayer:
        player = room.players.get(user_id)
        if player is None:
            raise HTTPException(403, "Room membership required")
        return player

    @staticmethod
    def _waiting(room: Room) -> None:
        if room.status != "WAITING":
            raise HTTPException(409, "Room is not waiting")

    @staticmethod
    def _snapshot(room: Room, user_id: str) -> dict:
        # Explicit serialization keeps locks, start keys, and hidden box values
        # private. Returned collections are copies, not live mutable state.
        game = None
        if room.game is not None:
            current = room.game.current_round
            round_state = None
            if current is not None:
                round_state = {
                    "number": current.number,
                    "phase": current.phase,
                    "deadline": current.deadline_at,
                    "choices": (
                        dict(current.choices) if current.phase == "REVEAL" else
                        {user_id: current.choices[user_id]} if user_id in current.choices else {}
                    ),
                    "my_choice": current.choices.get(user_id),
                }
                if current.phase == "REVEAL":
                    round_state["box_values"] = list(current.box_values)
            game = {
                "participant_ids": list(room.game.participant_ids),
                "scores": dict(room.game.scores),
                "round_number": room.game.round_number,
                "current_round": round_state,
                "winner_ids": list(room.game.winner_ids),
            }
        return {
            "code": room.code,
            "revision": room.revision,
            "status": room.status,
            "players": [
                {
                    "user_id": player.user_id,
                    "display_name": player.display_name,
                    "ready": player.ready,
                    "connected": player.connected,
                }
                for player in room.players.values()
            ],
            "game": game,
            "server_time": time.time(),
        }

    async def create(self, user: User) -> dict:
        async with self.creation_lock:
            while True:
                code = "".join(
                    secrets.choice(string.ascii_uppercase + string.digits)
                    for _ in range(6)
                )
                if code not in self.rooms:
                    break
            room = Room(code, {user.user_id: RoomPlayer(user.user_id, user.display_name)})
            self.rooms[code] = room
            self._changed(room)
            return self._snapshot(room, user.user_id)

    async def join(self, code: str, user: User) -> dict:
        room = self._room(code)
        async with room.lock:
            self._waiting(room)
            if user.user_id not in room.players:
                if len(room.players) >= 4:
                    raise HTTPException(409, "Room is full")
                room.players[user.user_id] = RoomPlayer(user.user_id, user.display_name)
                self._changed(room)
            return self._snapshot(room, user.user_id)

    async def get(self, code: str, user_id: str) -> dict:
        room = self._room(code)
        async with room.lock:
            self._member(room, user_id)
            return self._snapshot(room, user_id)

    async def set_ready(self, code: str, user_id: str, ready: bool) -> dict:
        room = self._room(code)
        async with room.lock:
            player = self._member(room, user_id)
            self._waiting(room)
            if player.ready != ready:
                player.ready = ready
                self._changed(room)
            return self._snapshot(room, user_id)

    async def start(self, code: str, user_id: str, mother: MotherClient) -> dict:
        room = self._room(code)
        async with room.lock:
            player = self._member(room, user_id)
            if not player.ready:
                raise HTTPException(403, "Only a Ready player may start")
            if room.status not in ("WAITING", "STARTING"):
                raise HTTPException(409, "Room cannot start another game")
            if room.start_in_flight:
                raise HTTPException(409, "A Start request is already in progress")
            if room.status == "WAITING":
                participants = tuple(
                    player.user_id for player in room.players.values() if player.ready
                )
                if len(participants) < 2:
                    raise HTTPException(409, "At least two Ready players are required")
                room.start_participants = participants
                room.status = "STARTING"
            # A retry from STARTING must use this exact frozen list and key.
            room.start_in_flight = True
            self._changed(room)

        try:
            # No room lock is held while the Mother Server performs I/O.
            await mother.consume_tickets(room.start_participants, room.start_key)
            async with room.lock:
                room.game = Game(
                    participant_ids=room.start_participants,
                    scores={participant: 0 for participant in room.start_participants},
                )
                room.players = {
                    participant: room.players[participant]
                    for participant in room.start_participants
                }
                self._begin_round(room.game)
                room.status = "PLAYING"
                self._changed(room)
                room.task = asyncio.create_task(self._run_game(room), name=f"game-{room.code}")
                return self._snapshot(room, user_id)
        except InsufficientTickets:
            async with room.lock:
                room.status = "WAITING"
                room.start_participants = ()
            raise
        finally:
            # Timeouts, malformed replies, network errors, and cancellation leave
            # STARTING intact. A subsequent Start safely replays the same operation.
            async with room.lock:
                room.start_in_flight = False
                if room.status in ("WAITING", "STARTING"):
                    self._changed(room)

    async def choose(self, code: str, user_id: str, round_number: int, box: int) -> dict:
        room = self._room(code)
        async with room.lock:
            self._member(room, user_id)
            game = room.game
            if game is None or user_id not in game.participant_ids:
                raise HTTPException(403, "Active game participation required")
            current = game.current_round
            if room.status != "PLAYING" or current is None or current.phase != "CHOOSING":
                raise HTTPException(409, "Round is not accepting choices")
            if type(round_number) is not int or round_number != current.number:
                raise HTTPException(409, "Choice is for the wrong round")
            if type(box) is not int or box not in (1, 2, 3):
                raise HTTPException(422, "Box must be 1, 2, or 3")
            if time.monotonic() >= current.deadline:
                raise HTTPException(409, "Selection deadline has passed")
            previous = current.choices.get(user_id)
            if previous is not None and previous != box:
                raise HTTPException(409, "The first selection is final")
            if previous is None:
                current.choices[user_id] = box
                self._changed(room)
            return self._snapshot(room, user_id)

    def _begin_round(self, game: Game) -> None:
        values = [10, 0, -5]
        self.rng.shuffle(values)
        game.round_number += 1
        game.current_round = Round(
            number=game.round_number,
            box_values=values,
            deadline=time.monotonic() + self.round_duration,
            deadline_at=time.time() + self.round_duration,
        )

    @staticmethod
    async def _sleep_until(deadline: float) -> None:
        # Recheck the monotonic deadline even if a timer wakes slightly early.
        while (remaining := deadline - time.monotonic()) > 0:
            await asyncio.sleep(remaining)

    async def _run_game(self, room: Room) -> None:
        game = room.game
        assert game is not None
        for _ in range(5):
            current = game.current_round
            assert current is not None
            await self._sleep_until(current.deadline)
            async with room.lock:
                # Only this task advances rounds. The phase guard also prevents
                # applying a round's scores more than once.
                if current.phase == "CHOOSING":
                    for participant, box in current.choices.items():
                        game.scores[participant] += current.box_values[box - 1]
                    current.phase = "REVEAL"
                    self._changed(room)
                reveal_deadline = time.monotonic() + self.reveal_duration
            await self._sleep_until(reveal_deadline)
            async with room.lock:
                if game.round_number == 5:
                    highest = max(game.scores.values())
                    game.winner_ids = [
                        participant for participant, score in game.scores.items()
                        if score == highest
                    ]
                    room.status = "FINISHED"
                    self._changed(room)
                    return
                self._begin_round(game)
                self._changed(room)

    async def close(self) -> None:
        tasks = [room.task for room in self.rooms.values() if room.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
