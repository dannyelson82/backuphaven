"""The backup schedule: which apps (ADR-0011) and when (ARCHITECTURE.md §6.11).

Stored in the ``settings`` table under ``schedule``. The list of apps is empty
until the owner adds apps; names are kept as added and matched ignoring case.
The timetable defaults to every night at 03:00.
"""

from datetime import time as dt_time

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from backuphaven import settings_store
from backuphaven.timetable import EVERY_DAY, Timetable

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


# --- when: the timetable (ARCHITECTURE.md §6.11) -------------------------------------


class ScheduleSettings(BaseModel):
    enabled: bool = True
    time: str = Field(default="03:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(default_factory=lambda: list(EVERY_DAY), min_length=1, max_length=7)

    @field_validator("days")
    @classmethod
    def _weekdays(cls, days: list[int]) -> list[int]:
        if any(d not in EVERY_DAY for d in days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(days))

    def timetable(self) -> Timetable:
        hour, minute = (int(part) for part in self.time.split(":"))
        return Timetable(at=dt_time(hour, minute), days=tuple(self.days))


def load_settings(session: Session) -> ScheduleSettings:
    value = settings_store.get(session, SETTING_KEY) or {}
    try:
        return ScheduleSettings.model_validate({k: v for k, v in value.items() if k != "apps"})
    except ValidationError:
        return ScheduleSettings()


def save_settings(session: Session, new: ScheduleSettings) -> None:
    value = settings_store.get(session, SETTING_KEY) or {}
    settings_store.put(session, SETTING_KEY, {**value, **new.model_dump()})
