import asyncio

import httpx
import pytest

from backend.config import Settings
from backend.main import create_app as create_backend, get_mother_client
from backend.mother_client import MotherClient
from mock_mother.main import create_app as create_mother


SECRET = "test-secret"
SETTINGS = Settings(service_secret=SECRET, frontend_origin="http://frontend.test")
SERVICE = {"X-Service-Secret": SECRET}
TOKENS = {"user-1": "demo-alice", "user-2": "demo-bob", "user-3": "demo-charlie", "user-4": "demo-dana"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def clients():
    mother_app = create_mother(SETTINGS)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mother_app), base_url="http://mother") as mother:
        backend_app = create_backend(SETTINGS)
        backend_app.dependency_overrides[get_mother_client] = lambda: MotherClient(mother, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://backend") as backend:
            yield backend, mother


def auth(user="user-1"):
    return {"Authorization": f"Bearer {TOKENS[user]}"}


async def buy(mother, user="user-1"):
    response = await mother.post("/tickets/purchase", headers=SERVICE, json={"user_id": user})
    assert response.status_code == 200
    return response.json()


async def consume(mother, participants, key):
    return await mother.post("/tickets/consume", headers=SERVICE, json={"participant_user_ids": participants, "idempotency_key": key})


async def balance(backend, user):
    response = await backend.get("/me", headers=auth(user))
    assert response.status_code == 200
    return response.json()["ticket_balance"]


@pytest.mark.anyio
@pytest.mark.parametrize("user,name", [("user-1", "Alice"), ("user-2", "Bob"), ("user-3", "Charlie"), ("user-4", "Dana")])
async def test_demo_users(clients, user, name):
    backend, _ = clients
    response = await backend.get("/me", headers=auth(user))
    assert response.status_code == 200
    assert response.json() == {"user_id": user, "display_name": name, "ticket_balance": 0}


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer bad-token"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}])
@pytest.mark.parametrize("path,method", [("/me", "get"), ("/tickets/purchase", "post")])
async def test_invalid_bearer(clients, headers, path, method):
    backend, _ = clients
    response = await getattr(backend, method)(path, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert await balance(backend, "user-1") == 0


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"X-Service-Secret": "wrong"}])
@pytest.mark.parametrize("path,body", [("/tickets/purchase", {"user_id": "user-1"}), ("/tickets/consume", {"participant_user_ids": ["user-1"], "idempotency_key": "k"})])
async def test_service_secret(clients, headers, path, body):
    backend, mother = clients
    assert (await mother.post(path, headers=headers, json=body)).status_code == 403
    assert await balance(backend, "user-1") == 0


@pytest.mark.anyio
async def test_purchase_uses_authenticated_identity(clients):
    backend, _ = clients
    for count in (1, 2):
        response = await backend.post("/tickets/purchase", headers=auth(), json={"user_id": "user-2"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "user-1"
        assert response.json()["ticket_balance"] == count
    assert await balance(backend, "user-2") == 0


@pytest.mark.anyio
async def test_consume_and_replay(clients):
    backend, mother = clients
    await buy(mother, "user-1")
    await buy(mother, "user-2")
    result = await consume(mother, ["user-1", "user-2"], "game")
    assert result.status_code == 200
    assert result.json() == {"idempotency_key": "game", "ticket_balances": {"user-1": 0, "user-2": 0}}
    await buy(mother, "user-1")
    replay = await consume(mother, ["user-2", "user-1"], "game")
    assert replay.status_code == 200 and replay.json() == result.json()
    assert (await consume(mother, ["user-1"], "game")).status_code == 409
    assert await balance(backend, "user-1") == 1
    assert await balance(backend, "user-2") == 0


@pytest.mark.anyio
async def test_failed_consumption_does_not_charge_or_reserve_key(clients):
    backend, mother = clients
    await buy(mother, "user-1")
    assert (await consume(mother, ["user-1", "user-2"], "retry")).status_code == 409
    assert await balance(backend, "user-1") == 1
    await buy(mother, "user-2")
    assert (await consume(mother, ["user-1", "user-2"], "retry")).status_code == 200
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 0


@pytest.mark.anyio
async def test_unknown_and_duplicate_participants_do_not_change_state(clients):
    backend, mother = clients
    await buy(mother)
    assert (await consume(mother, ["user-1", "unknown"], "unknown")).status_code == 404
    assert (await consume(mother, ["user-1", "user-1"], "duplicate")).status_code == 422
    assert (await mother.post("/tickets/purchase", headers=SERVICE, json={"user_id": "unknown"})).status_code == 404
    assert await balance(backend, "user-1") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("path,body", [
    ("/auth/validate", {}), ("/auth/validate", {"access_token": ""}),
    ("/tickets/purchase", {}), ("/tickets/purchase", {"user_id": 123}),
    ("/tickets/purchase", {"user_id": "user-1", "extra": True}),
    ("/tickets/consume", {}), ("/tickets/consume", {"participant_user_ids": [], "idempotency_key": "k"}),
    ("/tickets/consume", {"participant_user_ids": ["user-1"], "idempotency_key": ""}),
    ("/tickets/consume", {"participant_user_ids": "user-1", "idempotency_key": "k"}),
])
async def test_malformed_requests(clients, path, body):
    backend, mother = clients
    assert (await mother.post(path, headers=SERVICE, json=body)).status_code == 422
    assert await balance(backend, "user-1") == 0


@pytest.mark.anyio
async def test_concurrent_competing_consumptions_are_atomic(clients):
    backend, mother = clients
    for user in ("user-1", "user-2", "user-3"):
        await buy(mother, user)
    results = await asyncio.gather(consume(mother, ["user-1", "user-2"], "a"), consume(mother, ["user-1", "user-3"], "b"))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert await balance(backend, "user-1") == 0
    assert (await balance(backend, "user-2")) + (await balance(backend, "user-3")) == 1


@pytest.mark.anyio
async def test_concurrent_same_key_charges_once(clients):
    backend, mother = clients
    for user in ("user-1", "user-2"):
        await buy(mother, user)
        await buy(mother, user)
    results = await asyncio.gather(*(consume(mother, ["user-1", "user-2"], "same") for _ in range(8)))
    assert all(result.status_code == 200 and result.json() == results[0].json() for result in results)
    assert await balance(backend, "user-1") == await balance(backend, "user-2") == 1


@pytest.mark.anyio
async def test_cors(clients):
    backend, _ = clients
    headers = {"Origin": SETTINGS.frontend_origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    response = await backend.options("/tickets/purchase", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == SETTINGS.frontend_origin
    denied = await backend.options("/tickets/purchase", headers={**headers, "Origin": "http://evil.test"})
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.anyio
@pytest.mark.parametrize("failure,status", [("network", 503), ("timeout", 504), ("server", 502), ("payload", 502)])
@pytest.mark.parametrize("path", ["/me", "/tickets/purchase"])
async def test_upstream_failures_are_sanitized(failure, status, path):
    async def handler(request):
        # Let validation succeed so the purchase endpoint exercises its own upstream request.
        if path == "/tickets/purchase" and request.url.path == "/auth/validate":
            return httpx.Response(200, json={"user_id": "user-1", "display_name": "Alice", "ticket_balance": 0})
        if failure == "network":
            raise httpx.ConnectError("sensitive upstream detail", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive upstream detail", request=request)
        if failure == "server":
            return httpx.Response(500, text="sensitive upstream detail")
        return httpx.Response(200, json={"user_id": "user-1", "ticket_balance": "sensitive upstream detail"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mother") as mother:
        app = create_backend(SETTINGS)
        app.dependency_overrides[get_mother_client] = lambda: MotherClient(mother, SECRET)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as backend:
            response = await backend.request("GET" if path == "/me" else "POST", path, headers=auth())
    assert response.status_code == status
    assert "sensitive" not in response.text and SECRET not in response.text
