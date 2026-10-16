"""Scheduled backups (ARCHITECTURE.md §6.11): due, started, missed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backuphaven import schedule
from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database
from backuphaven.scheduler import Scheduler
from tests.helpers import API, admin_client
from tests.test_runner import unraid  # noqa: F401 - fixture


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[tuple[str, list[str] | None, bool]] = []
        self.busy = False

    def start(
        self, trigger: str, only: list[str] | None = None, scheduled: bool = False
    ) -> int | None:
        if self.busy:
            return None
        self.started.append((trigger, only, scheduled))
        return len(self.started)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Database, FakeRunner, Clock, Scheduler]:
    db = Database(tmp_path / "bh.db")
    db.migrate()
    with db.write() as session:
        schedule.save_apps(session, ["plex", "sonarr"])
    runner = FakeRunner()
    clock = Clock(datetime(2026, 10, 16, 21, 0, tzinfo=UTC))  # Friday evening
    settings = Settings(config_dir=tmp_path, tz="Etc/UTC")
    return db, runner, clock, Scheduler(db, runner, settings, clock)  # type: ignore[arg-type]


def test_runs_once_when_due(setup) -> None:  # type: ignore[no-untyped-def]
    _db, runner, clock, scheduler = setup
    scheduler.tick()  # first start: nothing owed
    assert runner.started == []
    assert scheduler.next_run() == datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
    clock.now = datetime(2026, 10, 17, 3, 0, 20, tzinfo=UTC)
    scheduler.tick()
    assert runner.started == [("schedule", ["plex", "sonarr"], True)]
    clock.now += timedelta(seconds=30)
    scheduler.tick()  # not twice
    assert len(runner.started) == 1
    assert scheduler.next_run() == datetime(2026, 10, 18, 3, 0, tzinfo=UTC)
    assert scheduler.missed() == []


def test_a_backup_due_while_the_server_was_off_is_reported_not_run_late(setup) -> None:  # type: ignore[no-untyped-def]
    _db, runner, clock, scheduler = setup
    scheduler.tick()
    clock.now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)  # switched on again at 09:00
    scheduler.tick()
    assert runner.started == []
    [missed] = scheduler.missed()
    assert missed.due == datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
    assert "wasn't running" in missed.reason
    scheduler.tick()
    assert len(scheduler.missed()) == 1  # reported once


def test_a_backup_due_while_another_runs_is_missed(setup) -> None:  # type: ignore[no-untyped-def]
    _db, runner, clock, scheduler = setup
    scheduler.tick()
    runner.busy = True
    clock.now = datetime(2026, 10, 17, 3, 0, 5, tzinfo=UTC)
    scheduler.tick()
    assert runner.started == []
    assert scheduler.missed()[0].reason == "another backup was still running"


def test_empty_schedule_or_switched_off_means_nothing_runs_or_is_missed(setup) -> None:  # type: ignore[no-untyped-def]
    db, runner, clock, scheduler = setup
    scheduler.tick()
    with db.write() as session:
        schedule.save_apps(session, [])
    clock.now = datetime(2026, 10, 17, 3, 0, 5, tzinfo=UTC)
    scheduler.tick()
    assert scheduler.next_run() is None
    with db.write() as session:
        schedule.save_apps(session, ["plex"])
        schedule.save_settings(session, schedule.ScheduleSettings(enabled=False))
    clock.now = datetime(2026, 10, 18, 3, 0, 5, tzinfo=UTC)
    scheduler.tick()
    assert scheduler.next_run() is None
    # Switched on again later: no catching up on nights it was off.
    with db.write() as session:
        schedule.save_settings(session, schedule.ScheduleSettings(enabled=True))
    clock.now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    scheduler.tick()
    assert runner.started == [] and scheduler.missed() == []


def test_chosen_days_and_time(setup) -> None:  # type: ignore[no-untyped-def]
    db, _runner, _clock, scheduler = setup
    with db.write() as session:
        schedule.save_settings(session, schedule.ScheduleSettings(time="22:15", days=[0, 2]))
    assert scheduler.next_run() == datetime(2026, 10, 19, 22, 15, tzinfo=UTC)  # Monday


def test_schedule_settings_api(unraid: Settings) -> None:  # noqa: F811
    settings = unraid.model_copy(update={"tz": "Europe/London"})
    app = create_app(settings, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        view = admin.get(f"{API}/settings/schedule").json()
        assert view["enabled"] and view["time"] == "03:00" and view["days"] == [0, 1, 2, 3, 4, 5, 6]
        assert view["timezone"] == "Europe/London"
        assert view["next_run"] is None  # nothing in the backup schedule yet
        admin.put(f"{API}/schedule/apps", json={"apps": ["plex"]})
        bads: list[dict[str, object]] = [
            {"time": "25:00"},
            {"time": "3am"},
            {"days": []},
            {"days": [7]},
        ]
        for bad in bads:
            assert admin.put(f"{API}/settings/schedule", json=bad).status_code == 422
        view = admin.put(
            f"{API}/settings/schedule", json={"enabled": True, "time": "01:45", "days": [5, 5, 6]}
        ).json()
        assert view["days"] == [5, 6] and view["next_run"].endswith(
            ("01:45:00+01:00", "01:45:00Z", "01:45:00+00:00")
        )
        assert view["missed"] == []
        status = admin.get(f"{API}/status").json()
        assert status["next_run"] == view["next_run"] and status["missed"] == []
