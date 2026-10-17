"""Settings → Alerts: channels, which alerts they send, the morning summary; and
the alert history (docs/plans/phase-0.4.md)."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from backuphaven import alert_channels, audit
from backuphaven.alert_channels import ChannelConfig, ChannelType, LiveChannel
from backuphaven.alerts import KINDS
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.db import Alert
from backuphaven.secretbox import SecretBox

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])
logger = logging.getLogger(__name__)


class ChannelView(BaseModel):
    id: str
    type: str
    name: str
    enabled: bool
    kinds: list[str]
    settings: dict[str, Any]
    has_secret: bool  # the secret itself is never sent back


class AlertSettingsView(BaseModel):
    channels: list[ChannelView]
    summary_enabled: bool
    summary_time: str
    kinds: dict[str, str]  # kind -> label, for the checkboxes


class ChannelInput(BaseModel):
    type: ChannelType
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    kinds: list[str] = Field(default_factory=lambda: list(alert_channels.DEFAULT_KINDS))
    settings: dict[str, Any] = Field(default_factory=dict)
    # New secret (token, password, webhook address). On edit, None keeps the old one.
    secret: str | None = Field(default=None, max_length=2000)


class SummaryInput(BaseModel):
    summary_enabled: bool
    summary_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class AlertView(BaseModel):
    id: int
    kind: str
    title: str
    message: str
    state: str
    created_at: datetime
    recovered_at: datetime | None
    delivery: dict[str, Any] | None


def _box(request: Request) -> SecretBox:
    box: SecretBox = request.app.state.secrets
    return box


def _view(db: DbDep) -> AlertSettingsView:
    with db.read() as session:
        stored = alert_channels.load(session)
    return AlertSettingsView(
        channels=[
            ChannelView(
                id=c.id,
                type=c.type,
                name=c.name,
                enabled=c.enabled,
                kinds=c.kinds,
                settings=c.settings,
                has_secret=c.secret is not None,
            )
            for c in stored.channels
        ],
        summary_enabled=stored.summary_enabled,
        summary_time=stored.summary_time,
        kinds={k: v for k, v in KINDS.items() if k not in ("test",)},
    )


def _checked(body: ChannelInput, keep_secret: str | None, box: SecretBox) -> ChannelConfig:
    unknown = [k for k in body.kinds if k not in KINDS]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown alert kinds: {unknown}"
        )
    config = ChannelConfig(
        type=body.type,
        name=body.name,
        enabled=body.enabled,
        kinds=body.kinds,
        settings=body.settings,
    )
    secret = body.secret if body.secret else None
    try:
        alert_channels.validate(config, secret)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    encrypted = box.encrypt(secret) if secret else keep_secret
    if encrypted is None and body.type in alert_channels.SECRET_REQUIRED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"a {body.type} channel needs its address or key"
        )
    return config.model_copy(update={"secret": encrypted})


@router.get("/settings/alerts")
def get_alert_settings(db: DbDep, _principal: InteractiveDep) -> AlertSettingsView:
    return _view(db)


@router.post("/settings/alerts/channels", status_code=status.HTTP_201_CREATED)
def add_channel(
    body: ChannelInput, request: Request, db: DbDep, principal: InteractiveDep
) -> AlertSettingsView:
    config = _checked(body, None, _box(request))
    with db.write() as session:
        stored = alert_channels.load(session)
        if len(stored.channels) >= 20:
            raise HTTPException(status.HTTP_409_CONFLICT, "too many channels")
        stored.channels.append(config)
        alert_channels.save(session, stored)
        audit.record(
            session, principal.actor, "alerts.channel_added", config.name, {"type": config.type}
        )
    return _view(db)


@router.put("/settings/alerts/channels/{channel_id}")
def edit_channel(
    channel_id: str, body: ChannelInput, request: Request, db: DbDep, principal: InteractiveDep
) -> AlertSettingsView:
    with db.write() as session:
        stored = alert_channels.load(session)
        old = next((c for c in stored.channels if c.id == channel_id), None)
        if old is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "channel_not_found")
        keep = old.secret if old.type == body.type else None
        config = _checked(body, keep, _box(request)).model_copy(update={"id": channel_id})
        stored.channels = [config if c.id == channel_id else c for c in stored.channels]
        alert_channels.save(session, stored)
        audit.record(session, principal.actor, "alerts.channel_changed", config.name)
    return _view(db)


@router.delete("/settings/alerts/channels/{channel_id}")
def remove_channel(channel_id: str, db: DbDep, principal: InteractiveDep) -> AlertSettingsView:
    with db.write() as session:
        stored = alert_channels.load(session)
        if not any(c.id == channel_id for c in stored.channels):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "channel_not_found")
        stored.channels = [c for c in stored.channels if c.id != channel_id]
        alert_channels.save(session, stored)
        audit.record(session, principal.actor, "alerts.channel_removed", channel_id)
    return _view(db)


@router.post("/settings/alerts/channels/{channel_id}/test")
def test_channel(
    channel_id: str, request: Request, db: DbDep, _principal: InteractiveDep
) -> dict[str, Any]:
    """Send a test message now and say whether it arrived at the service."""
    with db.read() as session:
        stored = alert_channels.load(session)
    config = next((c for c in stored.channels if c.id == channel_id), None)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel_not_found")
    secret = _box(request).decrypt(config.secret) if config.secret else None
    try:
        LiveChannel(config, secret).send(
            "Test from BackupHaven", "If you can read this, alerts reach you here.", "info"
        )
    except Exception as exc:  # any failure of an outside service, shown to the owner
        logger.warning(
            "channel test failed",
            extra={"channel": config.name, "type": config.type, "reason": str(exc)[:300]},
        )
        return {"ok": False, "error": str(exc)[:300]}
    logger.info("channel test sent", extra={"channel": config.name, "type": config.type})
    return {"ok": True, "error": None}


@router.put("/settings/alerts/summary")
def set_summary(body: SummaryInput, db: DbDep, principal: InteractiveDep) -> AlertSettingsView:
    with db.write() as session:
        stored = alert_channels.load(session)
        stored.summary_enabled, stored.summary_time = body.summary_enabled, body.summary_time
        alert_channels.save(session, stored)
        audit.record(session, principal.actor, "alerts.summary", None, body.model_dump())
    return _view(db)


@router.get("/alerts")
def list_alerts(db: DbDep, _principal: InteractiveDep, limit: int = 50) -> list[AlertView]:
    with db.read() as session:
        rows = session.scalars(
            select(Alert).order_by(Alert.id.desc()).limit(max(1, min(limit, 200)))
        ).all()
    return [AlertView.model_validate(r, from_attributes=True) for r in rows]
