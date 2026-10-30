from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import socketio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings, load_settings
from backend.models import ChoiceRequest, ReadyRequest, User
from backend.mother_client import MotherClient
from backend.rooms import RoomManager
from backend.realtime import RoomRealtime


bearer = HTTPBearer(auto_error=False)


def get_mother_client(request: Request) -> MotherClient:
    return request.app.state.mother_client


async def authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    mother: Annotated[MotherClient, Depends(get_mother_client)],
) -> User:
    if credentials is None:
        raise HTTPException(
            401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"}
        )
    return await mother.validate_token(credentials.credentials)


def create_app(
    settings: Settings | None = None, *, room_manager: RoomManager | None = None
) -> FastAPI:
    settings = settings or load_settings()
    rooms = room_manager if room_manager is not None else RoomManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            base_url=settings.mother_server_base_url,
            timeout=httpx.Timeout(5.0, connect=2.0),
            # Keep local service calls independent of desktop proxy settings.
            trust_env=False,
        ) as http:
            app.state.mother_client = MotherClient(http, settings.service_secret)
            try:
                yield
            finally:
                await rooms.close()
                await realtime.close()

    app = FastAPI(title="Lucky Rooms Game Backend", lifespan=lifespan)
    app.state.rooms = rooms
    sio = socketio.AsyncServer(
        async_mode="asgi", cors_allowed_origins=[settings.frontend_origin],
    )
    realtime = RoomRealtime(sio, rooms, lambda: app.state.mother_client)
    app.state.sio = sio
    app.state.realtime = realtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Socket.IO handles its own origin checks and headers. Keep it outside the
    # REST CORS middleware so browsers receive only one Allow-Origin header.
    app.add_middleware(lambda app: socketio.ASGIApp(sio, other_asgi_app=app))

    @app.get("/me", response_model=User)
    async def me(user: Annotated[User, Depends(authenticated_user)]) -> User:
        return user

    @app.post("/tickets/purchase", response_model=User)
    async def purchase_ticket(
        user: Annotated[User, Depends(authenticated_user)],
        mother: Annotated[MotherClient, Depends(get_mother_client)],
    ) -> User:
        # Only the validated identity can select the account receiving a ticket.
        return await mother.purchase_ticket(user.user_id)

    @app.post("/rooms", status_code=201)
    async def create_room(user: Annotated[User, Depends(authenticated_user)]) -> dict:
        return await rooms.create(user)

    @app.post("/rooms/{code}/join")
    async def join_room(
        code: str, user: Annotated[User, Depends(authenticated_user)]
    ) -> dict:
        return await rooms.join(code, user)

    @app.get("/rooms/{code}")
    async def get_room(
        code: str, user: Annotated[User, Depends(authenticated_user)]
    ) -> dict:
        return await rooms.get(code, user.user_id)

    @app.patch("/rooms/{code}/ready")
    async def set_ready(
        code: str, body: ReadyRequest, user: Annotated[User, Depends(authenticated_user)]
    ) -> dict:
        return await rooms.set_ready(code, user.user_id, body.ready)

    @app.post("/rooms/{code}/start")
    async def start_game(
        code: str,
        user: Annotated[User, Depends(authenticated_user)],
        mother: Annotated[MotherClient, Depends(get_mother_client)],
    ) -> dict:
        return await rooms.start(code, user.user_id, mother)

    @app.post("/rooms/{code}/choice")
    async def submit_choice(
        code: str, body: ChoiceRequest, user: Annotated[User, Depends(authenticated_user)]
    ) -> dict:
        return await rooms.choose(code, user.user_id, body.round, body.box)

    return app


app = create_app()
