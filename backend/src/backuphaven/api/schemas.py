"""Request and response models. Unknown fields are rejected (SECURITY.md)."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backuphaven.auth.passwords import MAX_LENGTH, MIN_LENGTH

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _normalise_username(value: str) -> str:
    value = value.strip().lower()
    if not _USERNAME.fullmatch(value):
        raise ValueError(
            "use 1-64 letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return value


class Credentials(StrictModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=MAX_LENGTH)
    remember: bool = True  # Keep me logged in (ReelHaven ADR-0033)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return value.strip().lower()


class SetupRequest(StrictModel):
    username: str = Field(max_length=64)
    password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return _normalise_username(value)


class PasswordChange(StrictModel):
    current_password: str = Field(max_length=MAX_LENGTH)
    new_password: str = Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)


class AuthState(BaseModel):
    setup_required: bool
    authenticated: bool
    username: str | None
    method: Literal["session", "bypass", "api_key"] | None
    csrf_token: str


class ApiKeyInfo(BaseModel):
    prefix: str
    created_at: datetime


class SecurityView(BaseModel):
    local_bypass: bool
    trusted_proxies: list[str]
    session_idle_days: int
    api_key: ApiKeyInfo | None
    client_ip: str | None
    bypass_applies_to_you: bool
    gateway_warning: bool


class NewApiKey(BaseModel):
    api_key: str
    prefix: str
