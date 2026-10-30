from secrets import compare_digest
from threading import Lock
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from backend.config import Settings, load_settings
from backend.models import (
    ConsumeRequest,
    ConsumeResponse,
    PurchaseRequest,
    User,
    ValidateTokenRequest,
)


DEMO_USERS = (
    ("user-1", "Alice", "demo-alice"),
    ("user-2", "Bob", "demo-bob"),
    ("user-3", "Charlie", "demo-charlie"),
    ("user-4", "Dana", "demo-dana"),
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Lucky Rooms Mock Mother Server")
    users = {
        user_id: User(user_id=user_id, display_name=name, ticket_balance=0)
        for user_id, name, _ in DEMO_USERS
    }
    token_users = {token: user_id for user_id, _, token in DEMO_USERS}
    successful_consumptions: dict[str, tuple[tuple[str, ...], ConsumeResponse]] = {}
    # Sync handlers run in FastAPI's thread pool; one lock protects all state.
    lock = Lock()

    def require_service_secret(
        x_service_secret: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_service_secret is None or not compare_digest(
            x_service_secret.encode("utf-8"), settings.service_secret.encode("utf-8")
        ):
            raise HTTPException(403, "Invalid service secret")

    @app.post("/auth/validate", response_model=User)
    def validate_token(body: ValidateTokenRequest) -> User:
        user_id = token_users.get(body.access_token)
        if user_id is None:
            raise HTTPException(401, "Invalid access token")
        with lock:
            return users[user_id]

    @app.post(
        "/tickets/purchase",
        response_model=User,
        dependencies=[Depends(require_service_secret)],
    )
    def purchase_ticket(body: PurchaseRequest) -> User:
        with lock:
            user = users.get(body.user_id)
            if user is None:
                raise HTTPException(404, "Unknown user")
            updated = user.model_copy(update={"ticket_balance": user.ticket_balance + 1})
            users[body.user_id] = updated
            return updated

    @app.post(
        "/tickets/consume",
        response_model=ConsumeResponse,
        dependencies=[Depends(require_service_secret)],
    )
    def consume_tickets(body: ConsumeRequest) -> ConsumeResponse:
        # Participant order does not change the identity of a game-start request.
        participants = tuple(sorted(body.participant_user_ids))
        with lock:
            previous = successful_consumptions.get(body.idempotency_key)
            if previous is not None:
                if previous[0] != participants:
                    raise HTTPException(409, "Idempotency key has different participants")
                return previous[1]

            if any(user_id not in users for user_id in participants):
                raise HTTPException(404, "Unknown participant")
            if any(users[user_id].ticket_balance < 1 for user_id in participants):
                raise HTTPException(409, "Every participant needs at least one ticket")

            balances = {
                user_id: users[user_id].ticket_balance - 1 for user_id in participants
            }
            result = ConsumeResponse(
                idempotency_key=body.idempotency_key, ticket_balances=balances
            )
            for user_id, balance in balances.items():
                users[user_id] = users[user_id].model_copy(
                    update={"ticket_balance": balance}
                )
            successful_consumptions[body.idempotency_key] = (participants, result)
            return result

    return app


app = create_app()
