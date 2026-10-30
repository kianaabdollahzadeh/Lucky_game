import asyncio
import random
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.main import create_app, get_mother_client
from backend.mother_client import MotherClient
from backend.rooms import RoomManager
from mock_mother.main import create_app as create_mother
from test_phase2 import SETTINGS, SECRET, TOKENS, auth, join, purchase, ready, room, snapshot


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def realtime_clients():
    manager = RoomManager(round_duration=0.12, reveal_duration=0.04, rng=random.Random(4))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_mother(SETTINGS)), base_url="http://mother") as mother:
        app = create_app(SETTINGS, room_manager=manager)
        client = MotherClient(mother, SECRET)
        app.state.mother_client = client
        app.dependency_overrides[get_mother_client] = lambda: client
        packets = []
        changed = asyncio.Event()

        async def emit(event, data=None, *args, **kwargs):
            packets.append((event, data, kwargs.get("to", kwargs.get("room"))))
            changed.set()

        app.state.sio.emit = AsyncMock(side_effect=emit)
        app.state.sio.enter_room = AsyncMock()
        app.state.sio.leave_room = AsyncMock()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as backend:
            try:
                yield backend, mother, manager, app.state.realtime, packets, changed
            finally:
                await app.state.realtime.close()
                await manager.close()


async def wait_packet(packets, changed, predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while True:
            for packet in packets:
                if predicate(*packet):
                    return packet
            changed.clear()
            await changed.wait()


async def connect(rt, sid, user="user-1", **extra):
    return await rt.connect(sid, {}, {"access_token": TOKENS[user], **extra})


@pytest.mark.anyio
async def test_auth_watch_membership_and_switch(realtime_clients):
    backend, _, _, rt, packets, changed = realtime_clients
    code = await room(backend)
    other = await room(backend)
    assert await rt.connect("missing", {}, None) is False
    assert await rt.connect("bad", {}, {"access_token": "invalid"}) is False
    for malformed in ({}, [], "token", {"access_token": 1}, {"access_token": ""}, {"access_token": "x" * 513}):
        assert await rt.connect("bad", {}, malformed) is False
    assert await connect(rt, "alice", user_id="user-2") is True
    assert await connect(rt, "bob", "user-2") is True
    assert (await rt.watch_room("bob", {"code": code}))["ok"] is False
    assert (await rt.watch_room("alice", {"code": "DOES-NOT-EXIST"}))["ok"] is False
    assert await rt.watch_room("alice", code) == {"ok": True, "code": code}
    state = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "alice"))[1]
    assert state["players"][0]["user_id"] == "user-1"
    revision = (await snapshot(backend, code))["revision"]
    results = await asyncio.gather(*(rt.watch_room("alice", code) for _ in range(4)))
    assert all(result["ok"] for result in results)
    assert (await snapshot(backend, code))["revision"] == revision
    assert (await rt.watch_room("alice", other))["ok"] is False
    await rt.disconnect("alice")
    assert await connect(rt, "alice2") is True
    assert (await rt.watch_room("alice2", other))["ok"] is True


@pytest.mark.anyio
async def test_waiting_connections_and_rest_notifications(realtime_clients):
    backend, _, _, rt, packets, changed = realtime_clients
    code = await room(backend)
    assert await connect(rt, "a1") and await connect(rt, "a2")
    assert (await rt.watch_room("a1", code))["ok"]
    assert (await rt.watch_room("a2", code))["ok"]
    assert (await join(backend, code, "user-2")).status_code == 200
    assert (await ready(backend, code, "user-2")).status_code == 200
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a1" and any(p["user_id"] == "user-2" and p["ready"] for p in d["players"]))
    assert await connect(rt, "unwatched", "user-2")
    await rt.disconnect("unwatched")
    assert len((await snapshot(backend, code))["players"]) == 2
    assert await connect(rt, "b", "user-2")
    assert (await rt.watch_room("b", code))["ok"]
    await rt.disconnect("a1")
    assert (await snapshot(backend, code))["players"][0]["connected"] is True
    await rt.disconnect("a2")
    state = await snapshot(backend, code, "user-2")
    assert [p["user_id"] for p in state["players"]] == ["user-2"]
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "b" and [p["user_id"] for p in d["players"]] == ["user-2"])


@pytest.mark.anyio
async def test_start_failure_pruning_and_round_notifications(realtime_clients):
    backend, mother, manager, rt, packets, changed = realtime_clients
    code = await room(backend)
    for user in ("user-2", "user-3"):
        assert (await join(backend, code, user)).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    for sid, user in (("a", "user-1"), ("b", "user-2"), ("c", "user-3")):
        assert await connect(rt, sid, user)
        assert (await rt.watch_room(sid, code))["ok"]
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 409
    starting = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["status"] == "STARTING"))[1]
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["status"] == "WAITING" and d["revision"] > starting["revision"])
    await purchase(mother, "user-1", "user-2")
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    await wait_packet(packets, changed, lambda e, d, to: e == "room_removed" and to == "c" and d == {"code": code, "reason": "not_ready"})
    for user, box in (("user-1", 1), ("user-2", 2)):
        assert (await backend.post(f"/rooms/{code}/choice", headers=auth(user), json={"round": 1, "box": box})).status_code == 200
    a = await snapshot(backend, code)
    b = await snapshot(backend, code, "user-2")
    assert a["game"]["current_round"]["choices"] == {"user-1": 1}
    assert b["game"]["current_round"]["choices"] == {"user-2": 2}
    assert a["game"]["current_round"]["my_choice"] == 1
    assert "box_values" not in a["game"]["current_round"]
    for sid, user, box in (("a", "user-1", 1), ("b", "user-2", 2)):
        choosing = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == sid and d["game"] and d["game"]["current_round"]["phase"] == "CHOOSING" and d["game"]["current_round"]["my_choice"] == box))[1]
        current = choosing["game"]["current_round"]
        assert current["choices"] == {user: box}
        assert "box_values" not in current
        assert all(secret not in str(choosing) for secret in (*TOKENS.values(), SECRET, "start_key"))
    reveal = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["game"] is not None and d["game"]["current_round"]["number"] == 1 and d["game"]["current_round"]["phase"] == "REVEAL"))[1]
    assert reveal["game"]["current_round"]["choices"] == {"user-1": 1, "user-2": 2}
    values = reveal["game"]["current_round"]["box_values"]
    assert len(values) == 3
    assert reveal["game"]["scores"] == {"user-1": values[0], "user-2": values[1]}
    assert (await rt.watch_room("c", code))["ok"] is False
    await asyncio.wait_for(manager.rooms[code].task, 3)
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["status"] == "FINISHED")
    states = [d for e, d, to in packets if e == "room_state" and to == "a"]
    assert {(d["game"]["current_round"]["number"], d["game"]["current_round"]["phase"]) for d in states if d["game"]} == {(number, phase) for number in range(1, 6) for phase in ("CHOOSING", "REVEAL")}
    finished = states[-1]["game"]
    assert finished["scores"] == reveal["game"]["scores"]
    assert set(finished["winner_ids"]) == {user for user, score in finished["scores"].items() if score == max(finished["scores"].values())}
    removed_index = next(i for i, (e, _, to) in enumerate(packets) if e == "room_removed" and to == "c")
    assert not any(e == "room_state" and to == "c" for e, _, to in packets[removed_index + 1:])
    revisions = [d["revision"] for d in states]
    assert revisions == sorted(revisions)
    await rt.disconnect("a")
    assert await connect(rt, "again")
    assert (await rt.watch_room("again", code))["ok"]
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "again" and d["status"] == "FINISHED")


@pytest.mark.anyio
async def test_starting_disconnect_preserves_frozen_players_and_playing_reconnect(realtime_clients):
    backend, mother, manager, rt, packets, changed = realtime_clients
    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    await purchase(mother, "user-1", "user-2")
    for sid, user in (("a", "user-1"), ("b", "user-2")):
        assert await connect(rt, sid, user)
        assert (await rt.watch_room(sid, code))["ok"]
    entered, release = asyncio.Event(), asyncio.Event()
    actual = MotherClient(mother, SECRET)
    calls = []

    class SlowMother:
        async def consume_tickets(self, participants, key):
            calls.append(tuple(participants))
            entered.set()
            await release.wait()
            return await actual.consume_tickets(participants, key)

    task = asyncio.create_task(manager.start(code, "user-1", SlowMother()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await rt.disconnect("b")
        state = await snapshot(backend, code)
        assert state["status"] == "STARTING"
        assert {p["user_id"] for p in state["players"]} == {"user-1", "user-2"}
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
    assert set(calls[0]) == {"user-1", "user-2"}
    assert await connect(rt, "b2", "user-2")
    assert (await rt.watch_room("b2", code))["ok"]
    assert (await backend.post(f"/rooms/{code}/choice", headers=auth("user-2"), json={"round": 1, "box": 2})).status_code == 200
    await rt.disconnect("b2")
    assert await connect(rt, "b3", "user-2")
    assert (await rt.watch_room("b3", code))["ok"]
    state = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "b3"))[1]
    assert state["game"]["current_round"]["my_choice"] == 2
    assert state["game"]["scores"] == {"user-1": 0, "user-2": 0}



@pytest.mark.anyio
async def test_slow_emit_does_not_hold_room_lock_or_stop_game(realtime_clients):
    backend, mother, manager, rt, _, _ = realtime_clients
    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    await purchase(mother, "user-1", "user-2")
    assert await connect(rt, "a")
    assert (await rt.watch_room("a", code))["ok"]
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_emit(*args, **kwargs):
        entered.set()
        await release.wait()

    rt.sio.emit = AsyncMock(side_effect=slow_emit)
    try:
        assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
        await asyncio.wait_for(entered.wait(), 1)
        state = await asyncio.wait_for(snapshot(backend, code), 0.5)
        assert state["status"] == "PLAYING"
        await asyncio.wait_for(manager.rooms[code].task, 2)
        assert (await snapshot(backend, code))["status"] == "FINISHED"
    finally:
        release.set()


@pytest.mark.anyio
async def test_playing_multiple_sockets_disconnect_and_score_reconnect(realtime_clients):
    backend, mother, manager, rt, packets, changed = realtime_clients
    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    await purchase(mother, "user-1", "user-2")
    for sid, user in (("a", "user-1"), ("b1", "user-2"), ("b2", "user-2")):
        assert await connect(rt, sid, user)
        assert (await rt.watch_room(sid, code))["ok"]
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    # Select a nonzero-valued box to verify scores survive reconnect.
    values = manager.rooms[code].game.current_round.box_values
    box = next(i + 1 for i, value in enumerate(values) if value != 0)
    assert (await backend.post(f"/rooms/{code}/choice", headers=auth("user-2"), json={"round": 1, "box": box})).status_code == 200
    reveal = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["game"] and d["game"]["current_round"]["phase"] == "REVEAL"))[1]
    score = reveal["game"]["scores"]["user-2"]
    assert score == values[box - 1] and score != 0
    await rt.disconnect("b1")
    assert next(p for p in (await snapshot(backend, code))["players"] if p["user_id"] == "user-2")["connected"]
    await rt.disconnect("b2")
    await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "a" and d["game"] and d["revision"] > reveal["revision"] and not next(p for p in d["players"] if p["user_id"] == "user-2")["connected"])
    assert await connect(rt, "b3", "user-2")
    assert (await rt.watch_room("b3", code))["ok"]
    state = (await wait_packet(packets, changed, lambda e, d, to: e == "room_state" and to == "b3"))[1]
    assert state["game"]["scores"]["user-2"] == score


@pytest.mark.anyio
async def test_start_failure_after_disconnect_and_retry(realtime_clients):
    backend, mother, manager, rt, _, _ = realtime_clients
    from backend.mother_client import InsufficientTickets

    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    assert await connect(rt, "b", "user-2")
    assert (await rt.watch_room("b", code))["ok"]
    entered, release = asyncio.Event(), asyncio.Event()
    actual = MotherClient(mother, SECRET)

    class SlowMother:
        async def consume_tickets(self, participants, key):
            entered.set()
            await release.wait()
            return await actual.consume_tickets(participants, key)

    task = asyncio.create_task(manager.start(code, "user-1", SlowMother()))
    await asyncio.wait_for(entered.wait(), 1)
    await rt.disconnect("b")
    release.set()
    with pytest.raises(InsufficientTickets):
        await task
    state = await snapshot(backend, code)
    assert state["status"] == "WAITING"
    assert {p["user_id"] for p in state["players"]} == {"user-1", "user-2"}
    await purchase(mother, "user-1", "user-2")
    assert (await manager.start(code, "user-1", actual))["status"] == "PLAYING"


@pytest.mark.anyio
async def test_concurrent_watch_disconnect_is_safe(realtime_clients):
    backend, _, _, rt, _, _ = realtime_clients
    code = await room(backend)
    assert await connect(rt, "a")
    await asyncio.gather(*(rt.watch_room("a", code) for _ in range(5)), rt.disconnect("a"))
    assert "a" not in rt.connections
    assert (await rt.watch_room("a", code))["ok"] is False


def test_socket_mount_websocket_and_origin():
    import json
    from fastapi.testclient import TestClient
    from backend.models import User

    app = create_app(SETTINGS)
    mother = AsyncMock()
    mother.validate_token.return_value = User(user_id="user-1", display_name="Alice", ticket_balance=0)
    app.dependency_overrides[get_mother_client] = lambda: mother
    with TestClient(app) as client:
        app.state.mother_client = mother
        assert client.get("/me", headers=auth()).status_code == 200
        code = client.post("/rooms", headers=auth()).json()["code"]
        allowed = client.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": SETTINGS.frontend_origin})
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == SETTINGS.frontend_origin
        assert client.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": "http://evil.test"}).status_code == 400
        with client.websocket_connect("/socket.io/?EIO=4&transport=websocket", headers={"Origin": SETTINGS.frontend_origin, "Upgrade": "websocket"}) as ws:
            assert ws.receive_text().startswith("0")
            ws.send_text('40{"access_token":"demo-alice"}')
            assert ws.receive_text().startswith("40")
            ws.send_text('421["watch_room",' + json.dumps({"code": code}) + ']')
            received = [ws.receive_text(), ws.receive_text()]
            assert any(packet.startswith('431') and json.loads(packet[3:])[0] == {"ok": True, "code": code} for packet in received)
            assert any(packet.startswith('42["room_state",') for packet in received)




