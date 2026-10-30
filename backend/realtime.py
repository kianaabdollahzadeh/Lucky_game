"""Authenticated room observation; gameplay mutations stay in the REST API."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import socketio
from fastapi import HTTPException

from backend.mother_client import MotherClient
from backend.rooms import Room, RoomManager


logger = logging.getLogger(__name__)


@dataclass
class Connection:
    user_id: str
    room_code: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RoomRealtime:
    def __init__(
        self, sio: socketio.AsyncServer, rooms: RoomManager,
        mother: Callable[[], MotherClient],
    ) -> None:
        self.sio = sio
        self.rooms = rooms
        self.mother = mother
        self.connections: dict[str, Connection] = {}
        self.pending: set[asyncio.Task] = set()
        self.delivery_lock = asyncio.Lock()
        self.closed = False
        rooms.realtime = self
        sio.on("connect", self.connect)
        sio.on("watch_room", self.watch_room)
        sio.on("disconnect", self.disconnect)

    async def connect(self, sid: str, environ: dict, auth: object) -> bool:
        token = auth.get("access_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token or len(token) > 512:
            return False
        try:
            user = await self.mother().validate_token(token)
        except HTTPException:
            return False
        self.connections[sid] = Connection(user.user_id)
        return True

    async def watch_room(self, sid: str, data: object = None) -> dict:
        connection = self.connections.get(sid)
        if connection is None:
            return {"ok": False, "error": "Authentication required"}
        code = data.get("code") if isinstance(data, dict) else data
        if not isinstance(code, str) or not code:
            return {"ok": False, "error": "Room code required"}
        async with connection.lock:
            if self.connections.get(sid) is not connection:
                return {"ok": False, "error": "Authentication required"}
            try:
                room = self.rooms._room(code)
                async with room.lock:
                    player = self.rooms._member(room, connection.user_id)
                    if connection.room_code not in (None, room.code):
                        return {"ok": False, "error": "Disconnect before watching another room"}
                    # enter_room updates Socket.IO's local room map, without I/O.
                    await self.sio.enter_room(sid, room.code)
                    connection.room_code = room.code
                    if not player.connected:
                        player.connected = True
                        self.rooms._changed(room)
                    else:
                        self._schedule([(
                            sid, connection, "room_state",
                            self.rooms._snapshot(room, connection.user_id),
                        )])
            except HTTPException as exc:
                return {"ok": False, "error": exc.detail}
        return {"ok": True, "code": room.code}

    async def disconnect(self, sid: str, reason: str | None = None) -> None:
        connection = self.connections.get(sid)
        if connection is None:
            return
        async with connection.lock:
            self.connections.pop(sid, None)
            room = self.rooms.rooms.get(connection.room_code)
            if room is None:
                return
            async with room.lock:
                if any(
                    other.user_id == connection.user_id and other.room_code == room.code
                    for other in self.connections.values()
                ):
                    return
                player = room.players.get(connection.user_id)
                if player is None:
                    return
                player.connected = False
                if room.status == "WAITING":
                    del room.players[connection.user_id]
                # STARTING keeps frozen participants, including on ticket failure.
                # PLAYING and FINISHED retain every choice, score, and participant.
                self.rooms._changed(room)

    def room_changed(self, room: Room) -> None:
        # Synchronous capture under room.lock uses exactly the REST serializer.
        deliveries = []
        for sid, connection in self.connections.items():
            if connection.room_code != room.code:
                continue
            if connection.user_id in room.players:
                deliveries.append((
                    sid, connection, "room_state",
                    self.rooms._snapshot(room, connection.user_id),
                ))
            else:
                # Only a successful Start removes a still-watching member.
                connection.room_code = None
                deliveries.append((
                    sid, connection, "room_removed",
                    {"code": room.code, "reason": "not_ready"},
                ))
        self._schedule(deliveries)

    def _schedule(self, deliveries: list[tuple[str, Connection, str, dict]]) -> None:
        if not deliveries or self.closed:
            return
        task = asyncio.create_task(self._deliver(deliveries), name="room-notification")
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _deliver(self, deliveries: list[tuple[str, Connection, str, dict]]) -> None:
        # Tasks are scheduled in mutation order; the FIFO lock preserves that
        # order even when emit yields. No room lock or game timer waits on I/O.
        async with self.delivery_lock:
            for sid, connection, event, payload in deliveries:
                if self.connections.get(sid) is not connection:
                    continue
                if event == "room_state" and connection.room_code != payload["code"]:
                    continue
                try:
                    if event == "room_removed":
                        await self.sio.leave_room(sid, payload["code"])
                    await self.sio.emit(event, payload, to=sid)
                except Exception:
                    # Delivery failure must never undo a REST mutation or stop
                    # the game. Clients can recover with watch_room or GET.
                    logger.warning("Socket.IO %s delivery failed", event)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.sio.shutdown()
        self.connections.clear()
