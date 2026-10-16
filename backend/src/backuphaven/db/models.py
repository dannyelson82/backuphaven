"""ORM models (ARCHITECTURE.md §10). Change only together with a migration."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backuphaven.db.types import UTCDateTime, utcnow

# Stable constraint names so Alembic migrations work on SQLite (batch mode).
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSON(),
    }


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stored lower-cased; usernames are case-insensitive.
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuthSession(Base):
    """A login session. Only the SHA-256 of the session ID is stored."""

    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    ip: Mapped[str | None] = mapped_column(String(45), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(512), default=None)
    # Keep me logged in (ReelHaven ADR-0033): False makes the cookie a browser-session cookie.
    remember: Mapped[bool] = mapped_column(default=True, server_default=true())


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))  # username, "api-key", "system" or an IP
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(512), default=None)
    detail: Mapped[dict[str, Any] | None] = mapped_column(default=None)


RUN_STATUSES = ("running", "ok", "warning", "failed", "cancelled", "skipped")


class Run(Base):
    """One backup run (ARCHITECTURE.md §8): one archive."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # "manual" | "schedule"
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Enum(*RUN_STATUSES, name="run_status"))
    archive: Mapped[str | None] = mapped_column(String(255), default=None)  # file name
    size: Mapped[int | None] = mapped_column(Integer, default=None)
    verified: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    # Set when the keep rules (or the owner) deleted the backup file.
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class Restore(Base):
    """One app restored from one backup (ARCHITECTURE.md §6.9), and its undo."""

    __tablename__ = "restores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)  # the backup restored from
    app: Mapped[str] = mapped_column(String(255))
    # running | done | failed | undone | expired
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    # Until when Undo is possible (the set-aside folders are kept until then).
    undo_until: Mapped[datetime | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Per folder: host path, set-aside path, what was moved back, counts.
    detail: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    actor: Mapped[str] = mapped_column(String(128), default="")


class RunApp(Base):
    """One app's part of a run."""

    __tablename__ = "run_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    app: Mapped[str] = mapped_column(String(255))
    plugin: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Enum(*RUN_STATUSES, name="run_app_status"))
    files: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(default=0.0)
    problems: Mapped[dict[str, Any] | None] = mapped_column(default=None)  # errors, warnings


class DbCheck(Base):
    """A database's snapshot and check in a run."""

    __tablename__ = "db_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    app: Mapped[str] = mapped_column(String(255), index=True)
    path: Mapped[str] = mapped_column(String(1024))  # relative to the app's folder
    status: Mapped[str] = mapped_column(String(16))  # ok | damaged | unchecked | failed
    mode: Mapped[str | None] = mapped_column(String(8), default=None)  # full | quick
    size: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(default=0.0)
    messages: Mapped[dict[str, Any] | None] = mapped_column(default=None)
