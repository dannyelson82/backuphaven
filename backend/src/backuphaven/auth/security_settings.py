"""Security settings stored in the ``settings`` table."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backuphaven import settings_store
from backuphaven.auth.network import parse_networks

KEY = "security"
API_KEY_KEY = "api_key"


class SecuritySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_bypass: bool = False
    trusted_proxies: list[str] = Field(default_factory=list, max_length=32)
    session_idle_days: int = Field(default=7, ge=1, le=365)

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_networks(cls, values: list[str]) -> list[str]:
        try:
            return [str(n) for n in parse_networks(values)]
        except ValueError as exc:
            raise ValueError(f"not an IP address or CIDR range: {exc}") from None


class StoredApiKey(BaseModel):
    hash: str
    prefix: str
    created_at: datetime


def load(session: Session) -> SecuritySettings:
    raw = settings_store.get(session, KEY)
    return SecuritySettings() if raw is None else SecuritySettings.model_validate(raw)


def save(session: Session, value: SecuritySettings) -> None:
    settings_store.put(session, KEY, value.model_dump())


def load_api_key(session: Session) -> StoredApiKey | None:
    raw = settings_store.get(session, API_KEY_KEY)
    return None if raw is None else StoredApiKey.model_validate(raw)


def save_api_key(session: Session, value: StoredApiKey) -> None:
    settings_store.put(session, API_KEY_KEY, value.model_dump(mode="json"))
