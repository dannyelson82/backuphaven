"""Per-app backup mode chosen by the owner (Apps → Stop during backup), stored in
settings (``app_modes``). Without a choice, the plugin's ``backup.mode`` counts."""

from typing import Literal

from sqlalchemy.orm import Session

from backuphaven import settings_store

SETTING_KEY = "app_modes"
Mode = Literal["snapshot", "stop"]


def load(session: Session) -> dict[str, str]:
    return dict(settings_store.get(session, SETTING_KEY) or {})


def save(session: Session, app: str, mode: Mode | None) -> None:
    stored = load(session)
    if mode is None:
        stored.pop(app.lower(), None)
    else:
        stored[app.lower()] = mode
    settings_store.put(session, SETTING_KEY, stored)


def effective(chosen: dict[str, str], app: str, plugin_mode: str) -> str:
    return chosen.get(app.lower(), plugin_mode)
