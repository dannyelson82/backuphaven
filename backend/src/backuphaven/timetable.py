"""When scheduled backups are due (ARCHITECTURE.md §6.11). Pure functions.

A timetable is a time of day and the weekdays it applies to, in the server's
time zone. A time that doesn't exist on the night the clocks go forward (01:30
when they jump from 01:00 to 02:00) runs as long after the jump as it would have
been after 01:00 (02:30), still once that night; a time that happens twice when
the clocks go back runs once, the first time.
"""

import zoneinfo
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class Timetable:
    at: time = time(3, 0)
    days: tuple[int, ...] = field(default=EVERY_DAY)  # 0 = Monday


def zone(name: str | None) -> tzinfo:
    """The named time zone, or UTC when it's missing or unknown."""
    try:
        return zoneinfo.ZoneInfo(name or "Etc/UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo("Etc/UTC")


def _on(day: date, at: time, tz: tzinfo) -> datetime:
    """``at`` on ``day`` in ``tz``, moved past a clock-change gap if needed."""
    local = datetime.combine(day, at, tzinfo=tz)  # fold=0: the first of two
    # A time in a gap doesn't survive a round trip through UTC; normalise it to
    # the real clock time it lands on.
    # (astimezone to the same zone is a no-op, so go through UTC.)
    round_trip = local.astimezone(UTC).astimezone(tz)
    if round_trip.replace(tzinfo=None) != local.replace(tzinfo=None):
        return round_trip
    return local


def next_due(after: datetime, table: Timetable, tz: tzinfo) -> datetime | None:
    """The first due moment strictly after ``after`` (an aware datetime)."""
    if not table.days:
        return None
    start = after.astimezone(tz).date()
    for offset in range(8):
        day = start + timedelta(days=offset)
        if day.weekday() in table.days:
            due = _on(day, table.at, tz)
            if due > after:
                return due
    return None


def last_due(before: datetime, table: Timetable, tz: tzinfo) -> datetime | None:
    """The latest due moment at or before ``before``."""
    if not table.days:
        return None
    start = before.astimezone(tz).date()
    for offset in range(8):
        day = start - timedelta(days=offset)
        if day.weekday() in table.days:
            due = _on(day, table.at, tz)
            if due <= before:
                return due
    return None
