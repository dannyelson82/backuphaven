"""Typed access to the key/value ``settings`` table."""

from typing import Any

from sqlalchemy.orm import Session

from backuphaven.db.models import Setting


def get(session: Session, key: str) -> dict[str, Any] | None:
    row = session.get(Setting, key)
    return None if row is None else row.value


def put(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
