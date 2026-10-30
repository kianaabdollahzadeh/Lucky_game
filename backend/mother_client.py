import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend.models import ConsumeResponse, User


class InsufficientTickets(HTTPException):
    """The Mother Server confirmed that no participant was charged."""

    def __init__(self) -> None:
        super().__init__(409, "Every participant needs at least one ticket")


class MotherClient:
    def __init__(self, http: httpx.AsyncClient, service_secret: str) -> None:
        self.http = http
        self.service_secret = service_secret

    async def validate_token(self, access_token: str) -> User:
        return await self._request_user(
            "/auth/validate", {"access_token": access_token}
        )

    async def purchase_ticket(self, user_id: str) -> User:
        return await self._request_user("/tickets/purchase", {"user_id": user_id})

    async def consume_tickets(
        self, participant_user_ids: tuple[str, ...], idempotency_key: str
    ) -> ConsumeResponse:
        try:
            response = await self.http.post(
                "/tickets/consume",
                json={
                    "participant_user_ids": list(participant_user_ids),
                    "idempotency_key": idempotency_key,
                },
                headers={"X-Service-Secret": self.service_secret},
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "Mother Server timed out; retry Start") from exc
        except httpx.RequestError as exc:
            raise HTTPException(503, "Mother Server is unavailable; retry Start") from exc

        # A conflicting idempotency key also returns 409. Only this specific
        # rejection confirms that it is safe to unfreeze the room.
        if response.status_code == 409:
            try:
                detail = response.json()
            except ValueError:
                detail = None
            if detail == {"detail": "Every participant needs at least one ticket"}:
                raise InsufficientTickets()
        if not response.is_success:
            raise HTTPException(502, "Mother Server request failed; retry Start")

        try:
            result = ConsumeResponse.model_validate(response.json(), strict=True)
            if (
                result.idempotency_key != idempotency_key
                or set(result.ticket_balances) != set(participant_user_ids)
                or any(balance < 0 for balance in result.ticket_balances.values())
            ):
                raise ValueError("Consumption response does not match the request")
            return result
        except (ValueError, ValidationError) as exc:
            raise HTTPException(502, "Mother Server returned an invalid response") from exc

    async def _request_user(self, path: str, body: dict[str, str]) -> User:
        try:
            response = await self.http.post(
                path,
                json=body,
                headers={"X-Service-Secret": self.service_secret},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "Mother Server timed out") from exc
        except httpx.RequestError as exc:
            raise HTTPException(503, "Mother Server is unavailable") from exc
        except httpx.HTTPStatusError as exc:
            if path == "/auth/validate" and exc.response.status_code == 401:
                raise HTTPException(
                    401,
                    "Invalid access token",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc
            # Internal authorization errors and upstream details stay server-side.
            raise HTTPException(502, "Mother Server request failed") from exc

        try:
            return User.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(502, "Mother Server returned an invalid response") from exc
