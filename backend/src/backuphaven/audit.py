"""Audit log (ReelHaven ADR-0010): security-relevant and destructive actions."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from backuphaven.db.models import AuditLog

logger = logging.getLogger("backuphaven.audit")


def record(
    session: Session,
    actor: str,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to ``session`` (committed with the caller's transaction)."""
    session.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
    logger.info(
        "audit: %s by %s", action, actor, extra={"action": action, "actor": actor, "target": target}
    )
