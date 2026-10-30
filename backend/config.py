import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mother_server_base_url: str = "http://127.0.0.1:8001"
    service_secret: str = "lucky-rooms-development-only"
    frontend_origin: str = "http://localhost:5173"

    def __post_init__(self) -> None:
        if not self.service_secret.strip():
            raise ValueError("SERVICE_SECRET must not be empty")


def load_settings() -> Settings:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    return Settings(
        mother_server_base_url=os.getenv(
            "MOTHER_SERVER_BASE_URL", Settings.mother_server_base_url
        ),
        service_secret=os.getenv("SERVICE_SECRET", Settings.service_secret),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", Settings.frontend_origin),
    )
