"""The backup schedule's list of apps (ADR-0011): empty until the owner adds apps.

Stored in the ``settings`` table under ``schedule``. Names are kept as the owner
added them; matching against discovered apps ignores case.
"""

from sqlalchemy.orm import Session

from backuphaven import settings_store

SETTING_KEY = "schedule"


def load_apps(session: Session) -> list[str]:
    value = settings_store.get(session, SETTING_KEY) or {}
    return [str(name) for name in value.get("apps", [])]


def save_apps(session: Session, apps: list[str]) -> list[str]:
    """Save without duplicates (ignoring case), in a stable order."""
    seen: set[str] = set()
    unique: list[str] = []
    for name in apps:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    unique.sort(key=str.lower)
    value = settings_store.get(session, SETTING_KEY) or {}
    settings_store.put(session, SETTING_KEY, {**value, "apps": unique})
    return unique
