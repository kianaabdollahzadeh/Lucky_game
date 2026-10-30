from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


Identifier = Annotated[
    str, Field(strict=True, min_length=1, max_length=128, pattern=r"\S")
]


class User(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    user_id: Identifier
    display_name: str
    ticket_balance: int = Field(ge=0)


class ValidateTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(strict=True, min_length=1, max_length=512)


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Identifier


class ConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_user_ids: list[Identifier] = Field(min_length=1)
    idempotency_key: Identifier

    @field_validator("participant_user_ids")
    @classmethod
    def reject_duplicates(cls, participants: list[str]) -> list[str]:
        if len(participants) != len(set(participants)):
            raise ValueError("Participant user IDs must be unique")
        return participants


class ConsumeResponse(BaseModel):
    idempotency_key: str
    ticket_balances: dict[str, int]


class ReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool = Field(strict=True)


class ChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(strict=True, ge=1, le=5)
    box: int = Field(strict=True, ge=1, le=3)
