"""Login sessions. Only SHA-256 hashes of session IDs are stored."""

from datetime import timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backuphaven.auth.tokens import hash_token, new_session_token
from backuphaven.db.models import AuthSession, User
from backuphaven.db.types import utcnow

# Refresh "last seen" (and the sliding expiry) at most this often per session,
# so ordinary page loads don't each cause a database write.
REFRESH_INTERVAL = timedelta(minutes=5)


def create(
    session: Session,
    user: User,
    idle_days: int,
    ip: str | None,
    user_agent: str | None,
    remember: bool = True,
) -> str:
    """Create a session and return the raw token for the cookie."""
    now = utcnow()
    session.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    token = new_session_token()
    session.add(
        AuthSession(
            id_hash=hash_token(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=idle_days),
            ip=ip,
            user_agent=(user_agent or "")[:512] or None,
            remember=remember,
        )
    )
    return token


def find(session: Session, token: str) -> tuple[AuthSession, User] | None:
    row = session.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id_hash == hash_token(token))
    ).one_or_none()
    if row is None or row[0].expires_at <= utcnow():
        return None
    return row[0], row[1]


def miss_reason(session: Session, token: str) -> Literal["unknown", "expired"]:
    """Why ``find`` returned None for this token, for the log (ReelHaven ADR-0033)."""
    row = session.get(AuthSession, hash_token(token))
    return "unknown" if row is None else "expired"


def needs_refresh(auth_session: AuthSession) -> bool:
    return utcnow() - auth_session.last_seen_at >= REFRESH_INTERVAL


def refresh(session: Session, id_hash: str, idle_days: int) -> None:
    row = session.get(AuthSession, id_hash)
    if row is not None:
        now = utcnow()
        row.last_seen_at = now
        row.expires_at = now + timedelta(days=idle_days)


def revoke(session: Session, id_hash: str) -> None:
    session.execute(delete(AuthSession).where(AuthSession.id_hash == id_hash))


def revoke_all(session: Session, user_id: int, keep: str | None = None) -> int:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep is not None:
        stmt = stmt.where(AuthSession.id_hash != keep)
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)
