"""When scheduled backups are due (ARCHITECTURE.md §6.11)."""

from datetime import UTC, datetime, time

import pytest

from backuphaven.timetable import Timetable, last_due, next_due, zone

LONDON = zone("Europe/London")


def at(text: str, tz=LONDON) -> datetime:  # type: ignore[no-untyped-def]
    return datetime.fromisoformat(text).replace(tzinfo=tz)


def test_every_night_at_three() -> None:
    table = Timetable()
    assert next_due(at("2026-10-16T21:00"), table, LONDON) == at("2026-10-17T03:00")
    assert next_due(at("2026-10-17T02:59"), table, LONDON) == at("2026-10-17T03:00")
    # Exactly at the time: that one is now, the next is tomorrow.
    assert next_due(at("2026-10-17T03:00"), table, LONDON) == at("2026-10-18T03:00")
    assert last_due(at("2026-10-17T03:00"), table, LONDON) == at("2026-10-17T03:00")
    assert last_due(at("2026-10-17T02:59"), table, LONDON) == at("2026-10-16T03:00")


def test_chosen_weekdays() -> None:
    table = Timetable(at=time(1, 30), days=(0, 3))  # Monday and Thursday
    # 2026-10-16 is a Friday.
    assert next_due(at("2026-10-16T12:00"), table, LONDON) == at("2026-10-19T01:30")
    assert last_due(at("2026-10-16T12:00"), table, LONDON) == at("2026-10-15T01:30")


def test_no_days_means_never() -> None:
    table = Timetable(days=())
    assert next_due(at("2026-10-16T12:00"), table, LONDON) is None
    assert last_due(at("2026-10-16T12:00"), table, LONDON) is None


def test_a_time_skipped_by_the_clock_change_still_runs_that_night() -> None:
    # UK clocks go from 01:00 to 02:00 on 2027-03-28; 01:30 doesn't exist.
    table = Timetable(at=time(1, 30))
    due = next_due(at("2027-03-27T12:00"), table, LONDON)
    assert due is not None
    assert due.astimezone(UTC) == datetime(2027, 3, 28, 1, 30, tzinfo=UTC)  # 02:30 BST
    assert (due.hour, due.minute) == (2, 30)
    after = next_due(due, table, LONDON)
    assert after is not None and after.date().isoformat() == "2027-03-29"


def test_a_time_that_happens_twice_runs_once() -> None:
    # UK clocks go back from 02:00 to 01:00 on 2026-10-25; 01:30 happens twice.
    table = Timetable(at=time(1, 30))
    first = next_due(at("2026-10-24T12:00"), table, LONDON)
    assert first is not None and first.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    after = next_due(first, table, LONDON)
    assert after is not None and after.date().isoformat() == "2026-10-26"


@pytest.mark.parametrize("name", [None, "", "Not/AZone", "../etc/passwd"])
def test_unknown_time_zones_fall_back_to_utc(name: str | None) -> None:
    assert str(zone(name)) == "Etc/UTC"
