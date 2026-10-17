"""Alerts (phase 0.4): raising, repeats, recovery, delivery, and the hooks."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backuphaven import retention, schedule
from backuphaven.alerts import AlertService
from backuphaven.config import Settings
from backuphaven.db import Alert, Database
from backuphaven.health import HealthCheck
from backuphaven.runner import Runner
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


class FakeChannel:
    def __init__(self, cid: str = "c1", fail_times: int = 0, kinds: set[str] | None = None) -> None:
        self.id, self.name = cid, f"channel {cid}"
        self.sent: list[tuple[str, str, str]] = []
        self.fail_times = fail_times
        self.kinds = kinds

    def wants(self, kind: str) -> bool:
        return self.kinds is None or kind in self.kinds

    def send(self, title: str, message: str, state: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("server down")
        self.sent.append((title, message, state))


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def alerts(db: Database, channel: FakeChannel) -> AlertService:
    return AlertService(db, lambda: [channel], retry_wait_s=0)


def rows(db: Database) -> list[Alert]:
    with db.read() as session:
        return list(session.query(Alert).order_by(Alert.id))


def test_raise_once_then_quiet_until_recovered(
    db: Database,
    alerts: AlertService,
    channel: FakeChannel,
) -> None:
    assert alerts.raise_("run_failed", "run_failed", "first") is True
    assert alerts.raise_("run_failed", "run_failed", "again") is False  # a repeat
    alerts.flush()
    assert channel.sent == [("Backup failed", "first", "open")]
    assert rows(db)[0].message == "again"  # newest detail kept
    assert alerts.resolve("run_failed", "fixed") == 1
    alerts.flush()
    assert channel.sent[-1] == ("Recovered: Backup failed", "fixed", "recovered")
    assert alerts.resolve("run_failed", "fixed") == 0  # nothing open any more
    assert alerts.raise_("run_failed", "run_failed", "broken again") is True


def test_a_long_open_problem_is_repeated_after_twelve_hours(
    db: Database,
    alerts: AlertService,
) -> None:
    alerts.raise_("disk_low", "disk_low", "low")
    with db.write() as session:
        row = session.query(Alert).one()
        row.last_sent_at = datetime.now(UTC) - timedelta(hours=13)
    assert alerts.raise_("disk_low", "disk_low", "still low") is True


def test_delivery_retries_and_records_results(db: Database) -> None:
    flaky, dead = FakeChannel("a", fail_times=1), FakeChannel("b", fail_times=9)
    quiet = FakeChannel("c", kinds={"summary"})
    service = AlertService(db, lambda: [flaky, dead, quiet], retry_wait_s=0)
    service.raise_("missed", "missed", "didn't run")
    service.flush()
    delivery = rows(db)[0].delivery or {}
    assert delivery["a"] == "sent" and delivery["b"].startswith("failed: server down")
    assert "c" not in delivery and quiet.sent == []


def test_a_failed_backup_alerts_and_a_good_one_recovers(
    unraid: Settings,
    db: Database,
    alerts: AlertService,
    channel: FakeChannel,
) -> None:
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    tool_db = unraid.data_root / "appdata" / "tool" / "state.sqlite"
    good = tool_db.read_bytes()
    con = sqlite3.connect(tool_db)
    con.execute("PRAGMA journal_mode=DELETE")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5000)])
    con.execute("CREATE INDEX t_x ON t(x)")
    con.commit()
    con.close()
    data = bytearray(tool_db.read_bytes())
    data[4096 * 3 : 4096 * 3 + 100] = b"\xff" * 100
    tool_db.write_bytes(bytes(data))
    runner = Runner(db, unraid, plugins_of(unraid), alerts=alerts)
    runner.run_now(only=["Tool"], scheduled=True)
    alerts.flush()
    titles = [t for t, _m, _s in channel.sent]
    assert titles == ["Database damaged", "Backup failed"]
    assert "state.sqlite is damaged" in channel.sent[0][1]

    tool_db.write_bytes(good)
    runner.run_now(only=["Tool"], scheduled=True)
    alerts.flush()
    assert {t for t, _m, s in channel.sent if s == "recovered"} == {
        "Recovered: Database damaged",
        "Recovered: Backup failed",
    }


def test_an_app_without_a_plugin_raises_not_protected(
    unraid: Settings,
    db: Database,
    alerts: AlertService,
    channel: FakeChannel,
) -> None:
    (unraid.config_dir / "plugins" / "tool.yaml").unlink()
    Runner(db, unraid, plugins_of(unraid), alerts=alerts).run_now(only=["Tool"], scheduled=True)
    alerts.flush()
    assert [t for t, _m, _s in channel.sent] == ["App not protected", "Backup failed"]


def test_hourly_checks_no_good_backup_and_disk_low(
    unraid: Settings,
    db: Database,
    alerts: AlertService,
    channel: FakeChannel,
) -> None:
    with db.write() as session:
        schedule.save_apps(session, ["Tool"])
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    Runner(db, unraid, plugins_of(unraid)).run_now(only=["Tool"], scheduled=True)
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=10))
    clock = {"now": datetime.now(UTC)}
    free = {"bytes": 100 * 10**9}
    check = HealthCheck(db, unraid, alerts, lambda: clock["now"], lambda: free["bytes"])
    check.maybe_run()
    alerts.flush()
    assert channel.sent == []
    clock["now"] += timedelta(hours=40)
    free["bytes"] = 15 * 2**30  # under twice the 10 GB minimum
    check.maybe_run()
    alerts.flush()
    assert [t for t, _m, _s in channel.sent] == ["No good backup", "Disk nearly full"]
    check.maybe_run()  # within the hour: not run again
    free["bytes"] = 100 * 10**9
    clock["now"] += timedelta(hours=1)
    check.maybe_run()
    alerts.flush()
    assert channel.sent[-1][0] == "Recovered: Disk nearly full"


def test_the_app_records_alerts_without_any_channels(unraid: Settings, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backuphaven.app import create_app

    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        assert app.state.alerts.raise_("test", "test", "hello") is True
        assert app.state.alerts.open_alerts()[0]["message"] == "hello"


def test_a_missed_backup_alerts(
    db: Database, alerts: AlertService, channel: FakeChannel, tmp_path: Path
) -> None:
    from backuphaven.scheduler import Scheduler
    from tests.test_scheduler import Clock, FakeRunner

    with db.write() as session:
        schedule.save_apps(session, ["plex"])
    clock = Clock(datetime(2026, 10, 16, 21, 0, tzinfo=UTC))
    scheduler = Scheduler(
        db,
        FakeRunner(),  # type: ignore[arg-type]
        Settings(config_dir=tmp_path, tz="Etc/UTC"),
        clock,
        alerts=alerts,
    )
    scheduler.tick()
    clock.now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    scheduler.tick()
    alerts.flush()
    assert channel.sent[0][0] == "Backup missed"
    assert "Sat 17 Oct 03:00" in channel.sent[0][1]
