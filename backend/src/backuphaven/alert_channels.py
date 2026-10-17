"""Where alerts go (ARCHITECTURE.md §6.10): ntfy, Discord, Home Assistant, email
and Unraid notifications.

Channels are stored in the ``settings`` table (``alerts``). Each has a type, a
name, which kinds of alert it sends (all problems by default), its plain settings
and one secret (token, password or webhook address) kept encrypted with the
secret box and registered for log redaction when used. Only http(s) URLs are
accepted; every request has a timeout.
"""

import json
import smtplib
import ssl
import urllib.error
import urllib.request
import uuid
from email.message import EmailMessage
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backuphaven import settings_store
from backuphaven.logsetup import SecretRegistry
from backuphaven.secretbox import SecretBox

SETTING_KEY = "alerts"
TIMEOUT_S = 15
ChannelType = Literal["ntfy", "discord", "homeassistant", "email", "unraid"]
# Kinds a channel sends unless it says otherwise: problems and their recovery.
DEFAULT_KINDS = [
    "run_failed",
    "db_damaged",
    "missed",
    "no_good_backup",
    "disk_low",
    "restore_failed",
    "unprotected",
    "container_down",
]


def _check_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an http:// or https:// address")
    return value.strip()


class ChannelConfig(BaseModel):
    """A stored channel. ``secret`` is encrypted (never sent to the browser)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: ChannelType
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    kinds: list[str] = Field(default_factory=lambda: list(DEFAULT_KINDS))
    settings: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None


class AlertSettings(BaseModel):
    channels: list[ChannelConfig] = Field(default_factory=list)
    summary_enabled: bool = False
    summary_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


def load(session: Session) -> AlertSettings:
    return AlertSettings.model_validate(settings_store.get(session, SETTING_KEY) or {})


def save(session: Session, value: AlertSettings) -> None:
    settings_store.put(session, SETTING_KEY, value.model_dump())


# --- the channel types -----------------------------------------------------------------------


class NtfySettings(BaseModel):
    server: str = "https://ntfy.sh"
    topic: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    _url = field_validator("server")(_check_url)


class EmailSettings(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = ""
    sender: str = Field(min_length=3, max_length=255)
    to: str = Field(min_length=3, max_length=500)  # comma-separated


class UnraidSettings(BaseModel):
    # e.g. http://192.168.1.10/graphql (BackupHaven reaches the server by its address)
    url: str

    _url = field_validator("url")(_check_url)


SETTINGS_MODELS: dict[str, type[BaseModel] | None] = {
    "ntfy": NtfySettings,
    "discord": None,  # the webhook address is the secret
    "homeassistant": None,  # likewise
    "email": EmailSettings,
    "unraid": UnraidSettings,
}
SECRET_REQUIRED = {"discord", "homeassistant", "unraid"}


def validate(config: ChannelConfig, secret: str | None) -> None:
    """Raise ``ValueError`` if the channel's settings (or a new secret) aren't usable."""
    model = SETTINGS_MODELS[config.type]
    if model is not None:
        model.model_validate(config.settings)
    elif config.settings:
        raise ValueError(f"{config.type} channels have no settings besides the address")
    if secret is not None and config.type in ("discord", "homeassistant"):
        _check_url(secret)


def _post(url: str, body: bytes, headers: dict[str, str]) -> None:
    _check_url(url)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - http(s) only, checked
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            response.read(1024)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"the server answered {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"can't reach the server: {exc.reason}") from None


class LiveChannel:
    """A configured channel, ready to send."""

    def __init__(self, config: ChannelConfig, secret: str | None) -> None:
        self.config = config
        self.id = config.id
        self.name = config.name
        self._secret = secret
        if secret:
            SecretRegistry.register(secret)

    def wants(self, kind: str) -> bool:
        return self.config.enabled and (kind in ("test",) or kind in self.config.kinds)

    def send(self, title: str, message: str, state: str) -> None:
        getattr(self, f"_send_{self.config.type}")(title, message, state)

    def _send_ntfy(self, title: str, message: str, state: str) -> None:
        s = NtfySettings.model_validate(self.config.settings)
        headers = {
            "Title": title.encode("utf-8").decode("latin-1", "replace"),
            "Priority": "high" if state == "open" else "default",
            "Tags": {"open": "warning", "recovered": "white_check_mark"}.get(state, "floppy_disk"),
        }
        if self._secret:
            headers["Authorization"] = f"Bearer {self._secret}"
        _post(f"{s.server.rstrip('/')}/{quote(s.topic)}", message.encode(), headers)

    def _send_discord(self, title: str, message: str, state: str) -> None:
        color = {"open": 0xD9480F, "recovered": 0x2F9E44}.get(state, 0x5B21B6)
        body = {
            "username": "BackupHaven",
            "embeds": [{"title": title, "description": message[:4000], "color": color}],
        }
        _post(str(self._secret), json.dumps(body).encode(), {"Content-Type": "application/json"})

    def _send_homeassistant(self, title: str, message: str, state: str) -> None:
        body = {"title": title, "message": message, "state": state, "source": "backuphaven"}
        _post(str(self._secret), json.dumps(body).encode(), {"Content-Type": "application/json"})

    def _send_email(self, title: str, message: str, state: str) -> None:
        s = EmailSettings.model_validate(self.config.settings)
        mail = EmailMessage()
        mail["Subject"] = f"BackupHaven: {title}"
        mail["From"] = s.sender
        mail["To"] = s.to
        mail.set_content(message)
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if s.security == "ssl":
            smtp = smtplib.SMTP_SSL(s.host, s.port, timeout=TIMEOUT_S, context=context)
        else:
            smtp = smtplib.SMTP(s.host, s.port, timeout=TIMEOUT_S)
        with smtp:
            if s.security == "starttls":
                smtp.starttls(context=context)
            if s.username:
                smtp.login(s.username, self._secret or "")
            smtp.send_message(mail)

    def _send_unraid(self, title: str, message: str, state: str) -> None:
        s = UnraidSettings.model_validate(self.config.settings)
        importance = {"open": "ALERT", "recovered": "NORMAL"}.get(state, "NORMAL")
        query = (
            "mutation Notify($input: NotificationData!) "
            "{ createNotification(input: $input) { id } }"
        )
        variables = {
            "input": {
                "title": "BackupHaven",
                "subject": title,
                "description": message[:1000],
                "importance": importance,
            }
        }
        body = json.dumps({"query": query, "variables": variables}).encode()
        _post(s.url, body, {"Content-Type": "application/json", "x-api-key": str(self._secret)})


def live_channels(session: Session, box: SecretBox) -> list[LiveChannel]:
    out: list[LiveChannel] = []
    for config in load(session).channels:
        secret = box.decrypt(config.secret) if config.secret else None
        out.append(LiveChannel(config, secret))
    return out
