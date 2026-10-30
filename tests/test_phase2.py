import asyncio
import random

import httpx
import pytest

from backend.config import Settings
from backend.main import create_app, get_mother_client
from backend.mother_client import MotherClient
from backend.rooms import RoomManager
from mock_mother.main import create_app as create_mother

SECRET = "test-secret"
SETTINGS = Settings(service_secret=SECRET, frontend_origin="http://frontend.test")
TOKENS = {"user-1": "demo-alice", "user-2": "demo-bob", "user-3": "demo-charlie", "user-4": "demo-dana"}


def auth(user="user-1"):
    return {"Authorization": f"Bearer {TOKENS[user]}"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def clients():
    manager = RoomManager(round_duration=0.15, reveal_duration=0.04, rng=random.Random(4))
    mother_app = create_mother(SETTINGS)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mother_app), base_url="http://mother") as mother:
        app = create_app(SETTINGS, room_manager=manager)
        app.dependency_overrides[get_mother_client] = lambda: MotherClient(mother, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as backend:
            try:
                yield backend, mother, manager
            finally:
                await manager.close()


async def room(backend):
    response = await backend.post("/rooms", headers=auth())
    assert response.status_code == 201
    return response.json()["code"]


async def join(backend, code, user):
    return await backend.post(f"/rooms/{code}/join", headers=auth(user))


async def ready(backend, code, user, value=True):
    return await backend.patch(f"/rooms/{code}/ready", headers=auth(user), json={"ready": value})


async def purchase(mother, *users):
    for user in users:
        response = await mother.post("/tickets/purchase", headers={"X-Service-Secret": SECRET}, json={"user_id": user})
        assert response.status_code == 200


async def balance(backend, user):
    response = await backend.get("/me", headers=auth(user))
    assert response.status_code == 200
    return response.json()["ticket_balance"]


async def prepare(backend, mother):
    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    for user in ("user-1", "user-2"):
        assert (await ready(backend, code, user)).status_code == 200
    await purchase(mother, "user-1", "user-2")
    return code


async def snapshot(backend, code, user="user-1"):
    response = await backend.get(f"/rooms/{code}", headers=auth(user))
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_create_join_and_membership(clients):
    backend, _, _ = clients
    code = await room(backend)
    initial = await snapshot(backend, code)
    assert initial["code"] == code and initial["status"] == "WAITING"
    assert initial["game"] is None and isinstance(initial["server_time"], (int, float))
    assert initial["players"] == [{"user_id": "user-1", "display_name": "Alice", "ready": False, "connected": False}]
    assert (await backend.get(f"/rooms/{code}", headers=auth("user-2"))).status_code == 403
    assert (await backend.get("/rooms/NO-SUCH-ROOM", headers=auth())).status_code == 404
    assert (await join(backend, code, "user-2")).status_code == 200
    assert (await join(backend, code, "user-2")).status_code == 200
    assert len((await snapshot(backend, code))["players"]) == 2


@pytest.mark.anyio
async def test_room_code_collision_is_retried(clients, monkeypatch):
    import backend.rooms as rooms_module

    backend, _, _ = clients
    characters = iter("AAAAAA" "AAAAAA" "B12345")
    monkeypatch.setattr(rooms_module.secrets, "choice", lambda _: next(characters))
    assert await room(backend) == "AAAAAA"
    assert await room(backend) == "B12345"


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,body", [
    ("post", "/rooms", None), ("post", "/rooms/ABC/join", None),
    ("get", "/rooms/ABC", None), ("patch", "/rooms/ABC/ready", {"ready": True}),
    ("post", "/rooms/ABC/start", None), ("post", "/rooms/ABC/choice", {"round": 1, "box": 1}),
])
async def test_room_routes_require_authentication(clients, method, path, body):
    backend, _, _ = clients
    response = await backend.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{}, {"ready": 1}, {"ready": "true"}, {"ready": True, "extra": 1}])
async def test_ready_schema_strict(clients, body):
    backend, _, _ = clients
    code = await room(backend)
    assert (await backend.patch(f"/rooms/{code}/ready", headers=auth(), json=body)).status_code == 422


@pytest.mark.anyio
async def test_ready_and_nonmember(clients):
    backend, _, _ = clients
    code = await room(backend)
    assert (await ready(backend, code, "user-2")).status_code == 403
    assert (await ready(backend, code, "user-1")).status_code == 200
    assert (await ready(backend, code, "user-1")).status_code == 200
    assert (await snapshot(backend, code))["players"][0]["ready"] is True
    assert (await ready(backend, code, "user-1", False)).status_code == 200
    assert (await snapshot(backend, code))["players"][0]["ready"] is False


@pytest.mark.anyio
async def test_start_requires_ready_members_and_prunes_unready_only_after_success(clients):
    backend, mother, _ = clients
    code = await room(backend)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth("user-2"))).status_code == 403
    assert (await ready(backend, code, "user-1")).status_code == 200
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 409
    assert (await join(backend, code, "user-2")).status_code == 200
    assert (await join(backend, code, "user-3")).status_code == 200
    assert (await ready(backend, code, "user-2")).status_code == 200
    assert (await backend.post(f"/rooms/{code}/start", headers=auth("user-3"))).status_code == 403
    await purchase(mother, "user-1")
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 409
    state = await snapshot(backend, code)
    assert state["status"] == "WAITING" and len(state["players"]) == 3
    assert await balance(backend, "user-1") == 1
    await purchase(mother, "user-2")
    result = await backend.post(f"/rooms/{code}/start", headers=auth("user-2"))
    assert result.status_code == 200
    state = result.json()
    assert state["status"] == "PLAYING"
    assert {p["user_id"] for p in state["players"]} == {"user-1", "user-2"}
    assert set(state["game"]["participant_ids"]) == {"user-1", "user-2"}
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 0
    assert (await join(backend, code, "user-1")).status_code == 409
    assert (await ready(backend, code, "user-1", False)).status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{}, {"round": 0, "box": 1}, {"round": 6, "box": 1}, {"round": 1, "box": 0}, {"round": 1, "box": 4}, {"round": True, "box": 1}, {"round": 1, "box": "1"}, {"round": 1, "box": 1, "extra": 1}])
async def test_choice_schema_strict(clients, body):
    backend, _, _ = clients
    code = await room(backend)
    response = await backend.post(f"/rooms/{code}/choice", headers=auth(), json=body)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_choice_retry_change_wrong_round_and_hidden_values(clients):
    backend, mother, _ = clients
    code = await prepare(backend, mother)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    state = await snapshot(backend, code)
    current = state["game"]["current_round"]
    assert current["phase"] == "CHOOSING" and "box_values" not in current
    assert isinstance(current["deadline"], (int, float))
    async def choose(user, number, box):
        return await backend.post(f"/rooms/{code}/choice", headers=auth(user), json={"round": number, "box": box})
    assert (await choose("user-3", 1, 1)).status_code == 403
    assert (await choose("user-1", 2, 1)).status_code == 409
    first = await choose("user-1", 1, 1)
    assert first.status_code == 200 and first.json()["game"]["current_round"]["choices"]["user-1"] == 1
    assert (await choose("user-1", 1, 1)).status_code == 200
    assert (await choose("user-1", 1, 2)).status_code == 409
    assert (await choose("user-2", 1, 2)).status_code == 200


@pytest.mark.anyio
async def test_expired_choice_rejected_even_if_timer_has_not_run(clients):
    from fastapi import HTTPException

    backend, mother, manager = clients
    code = await prepare(backend, mother)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    with pytest.raises(HTTPException) as invalid:
        await manager.choose(code, "user-1", 1, 4)
    assert invalid.value.status_code == 422
    manager.rooms[code].game.current_round.deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(HTTPException) as expired:
        await manager.choose(code, "user-1", 1, 1)
    assert expired.value.status_code == 409
    response = await backend.post(f"/rooms/{code}/choice", headers=auth(), json={"round": 1, "box": 1})
    assert response.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("offset,expected_status", [(-0.000001, 200), (0, 409), (0.000001, 409)])
async def test_choice_at_monotonic_deadline_boundary(clients, monkeypatch, offset, expected_status):
    from types import SimpleNamespace
    import backend.rooms as rooms_module

    backend, mother, manager = clients
    code = await prepare(backend, mother)

    async def delayed_timer(deadline):
        # Simulate a busy scheduler: submissions must enforce expiry themselves.
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_sleep_until", delayed_timer)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    current = manager.rooms[code].game.current_round
    monkeypatch.setattr(rooms_module, "time", SimpleNamespace(
        monotonic=lambda: current.deadline + offset,
        # A wall-clock jump must not change the authoritative selection window.
        time=lambda: current.deadline_at + 3600,
    ))

    response = await backend.post(
        f"/rooms/{code}/choice", headers=auth(), json={"round": 1, "box": 1},
    )
    assert response.status_code == expected_status
    state = await snapshot(backend, code)
    assert state["game"]["current_round"]["phase"] == "CHOOSING"
    assert "box_values" not in state["game"]["current_round"]
    assert state["game"]["scores"] == {"user-1": 0, "user-2": 0}
    if expected_status == 200:
        assert state["game"]["current_round"]["choices"] == {"user-1": 1}
    else:
        assert response.json()["detail"] == "Selection deadline has passed"
        assert state["game"]["current_round"]["choices"] == {}


@pytest.mark.anyio
async def test_autonomous_rounds_finish_without_polling(clients):
    backend, mother, manager = clients
    code = await prepare(backend, mother)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    await asyncio.wait_for(manager.rooms[code].task, 3)
    state = await snapshot(backend, code)
    assert state["status"] == "FINISHED"
    game = state["game"]
    assert game["round_number"] == 5
    assert game["scores"] == {"user-1": 0, "user-2": 0}
    assert set(game["winner_ids"]) == {"user-1", "user-2"}
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 409
    assert (await join(backend, code, "user-1")).status_code == 409
    assert (await ready(backend, code, "user-1", False)).status_code == 409
    assert (await backend.post(f"/rooms/{code}/choice", headers=auth(), json={"round": 5, "box": 1})).status_code == 409


@pytest.mark.anyio
async def test_chosen_values_are_scored_across_five_rounds(clients):
    backend, mother, _ = clients
    code = await prepare(backend, mother)
    assert (await backend.post(f"/rooms/{code}/start", headers=auth())).status_code == 200
    submitted = set()
    expected = {"user-1": 0, "user-2": 0}
    revealed = set()
    async with asyncio.timeout(3):
        while True:
            state = await snapshot(backend, code)
            game = state["game"]
            current = game["current_round"]
            number = current["number"]
            if current["phase"] == "CHOOSING" and number not in submitted:
                for user, box in (("user-1", 1), ("user-2", 2)):
                    response = await backend.post(f"/rooms/{code}/choice", headers=auth(user), json={"round": number, "box": box})
                    assert response.status_code == 200
                submitted.add(number)
            if current["phase"] == "REVEAL" and number not in revealed:
                assert sorted(current["box_values"]) == [-5, 0, 10]
                expected["user-1"] += current["box_values"][0]
                expected["user-2"] += current["box_values"][1]
                revealed.add(number)
            assert game["scores"] == expected
            if state["status"] == "FINISHED":
                break
            await asyncio.sleep(0.005)
    assert submitted == revealed == set(range(1, 6))
    assert game["scores"] == expected
    assert game["winner_ids"] == ["user-2"]


@pytest.mark.anyio
async def test_four_players_can_choose_same_box_without_early_reveal(clients):
    backend, mother, manager = clients
    manager.round_duration = 2
    code = await room(backend)
    for user in ("user-2", "user-3", "user-4"):
        assert (await join(backend, code, user)).status_code == 200
    for user in TOKENS:
        assert (await ready(backend, code, user)).status_code == 200
    await purchase(mother, *TOKENS)
    result = await backend.post(f"/rooms/{code}/start", headers=auth())
    assert result.status_code == 200
    assert set(result.json()["game"]["participant_ids"]) == set(TOKENS)
    for user in TOKENS:
        response = await backend.post(f"/rooms/{code}/choice", headers=auth(user), json={"round": 1, "box": 1})
        assert response.status_code == 200
    game = response.json()["game"]
    assert game["current_round"]["phase"] == "CHOOSING"
    # Before REVEAL the shared REST/socket serializer exposes only this viewer.
    assert game["current_round"]["choices"] == {"user-4": 1}
    assert manager.rooms[code].game.current_round.choices == {user: 1 for user in TOKENS}
    assert "box_values" not in game["current_round"]
    assert game["scores"] == {user: 0 for user in TOKENS}
    for user in TOKENS:
        assert await balance(backend, user) == 0



@pytest.mark.anyio
async def test_concurrent_final_slot_is_atomic(clients):
    from fastapi import HTTPException
    from backend.models import User

    backend, _, manager = clients
    code = await room(backend)
    assert (await join(backend, code, "user-2")).status_code == 200
    assert (await join(backend, code, "user-3")).status_code == 200
    users = [User(user_id=f"candidate-{i}", display_name=str(i), ticket_balance=0) for i in range(2)]
    results = await asyncio.gather(*(manager.join(code, user) for user in users), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert len((await snapshot(backend, code))["players"]) == 4
    assert (await join(backend, code, "user-1")).status_code == 200
    assert (await join(backend, code, "user-4")).status_code == 409


@pytest.mark.anyio
async def test_start_in_flight_does_not_hold_room_lock(clients):
    from fastapi import HTTPException

    backend, mother, manager = clients
    code = await prepare(backend, mother)
    entered, release = asyncio.Event(), asyncio.Event()
    actual = MotherClient(mother, SECRET)

    class SlowMother:
        async def consume_tickets(self, participants, key):
            entered.set()
            await release.wait()
            return await actual.consume_tickets(participants, key)

    task = asyncio.create_task(manager.start(code, "user-1", SlowMother()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        state = await asyncio.wait_for(manager.get(code, "user-1"), 1)
        assert state["status"] == "STARTING"
        assert (await join(backend, code, "user-3")).status_code == 409
        assert (await ready(backend, code, "user-1", False)).status_code == 409
        with pytest.raises(HTTPException) as exc:
            await asyncio.wait_for(manager.start(code, "user-2", SlowMother()), 1)
        assert exc.value.status_code == 409
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
    assert (await snapshot(backend, code))["status"] == "PLAYING"
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 0


@pytest.mark.anyio
async def test_timeout_after_consumption_retries_same_key_without_double_charge(clients):
    from fastapi import HTTPException

    backend, mother, manager = clients
    code = await prepare(backend, mother)
    await purchase(mother, "user-1", "user-2")
    actual = MotherClient(mother, SECRET)
    calls = []

    class AmbiguousMother:
        async def consume_tickets(self, participants, key):
            calls.append((participants, key))
            result = await actual.consume_tickets(participants, key)
            if len(calls) == 1:
                raise HTTPException(504, "Mother Server timed out")
            return result

    client = AmbiguousMother()
    with pytest.raises(HTTPException) as exc:
        await manager.start(code, "user-1", client)
    assert exc.value.status_code == 504
    assert (await snapshot(backend, code))["status"] == "STARTING"
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 1
    assert (await manager.start(code, "user-2", client))["status"] == "PLAYING"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert set(calls[0][0]) == {"user-1", "user-2"}
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 1


@pytest.mark.anyio
async def test_cancelled_start_can_retry_frozen_consumption(clients):
    backend, mother, manager = clients
    code = await prepare(backend, mother)
    entered = asyncio.Event()
    calls = []
    actual = MotherClient(mother, SECRET)

    class CancelAfterDebit:
        async def consume_tickets(self, participants, key):
            calls.append((participants, key))
            result = await actual.consume_tickets(participants, key)
            if len(calls) == 1:
                entered.set()
                await asyncio.Event().wait()
            return result

    client = CancelAfterDebit()
    task = asyncio.create_task(manager.start(code, "user-1", client))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await snapshot(backend, code))["status"] == "STARTING"
    assert (await manager.start(code, "user-1", client))["status"] == "PLAYING"
    assert calls[0] == calls[1]
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 0


@pytest.mark.anyio
@pytest.mark.parametrize("reply", [
    (409, {"detail": "Idempotency key reused with different participants"}),
    (200, {"idempotency_key": "wrong-key", "ticket_balances": {"user-1": 0, "user-2": 0}}),
    (200, {"idempotency_key": "__actual__", "ticket_balances": {"user-1": 0}}),
])
async def test_uncertain_mother_reply_keeps_frozen_start(clients, reply):
    from fastapi import HTTPException

    backend, mother, manager = clients
    code = await prepare(backend, mother)
    status, body = reply

    async def handler(request):
        import json
        payload = json.loads(request.content)
        response_body = dict(body)
        if response_body.get("idempotency_key") == "__actual__":
            response_body["idempotency_key"] = payload["idempotency_key"]
        return httpx.Response(status, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mother") as upstream:
        with pytest.raises(HTTPException):
            await manager.start(code, "user-1", MotherClient(upstream, SECRET))
    state = await snapshot(backend, code)
    assert state["status"] == "STARTING"
    assert len(state["players"]) == 2
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 1
    assert (await ready(backend, code, "user-1", False)).status_code == 409
