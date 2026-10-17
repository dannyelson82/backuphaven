"""The morning summary (phase 0.4 chunk 4)."""

from datetime import UTC, datetime, timedelta

from backuphaven import alert_channels, retention, schedule
from backuphaven.alerts import AlertService
from backuphaven.config import Settings
from backuphaven.db import Database
from backuphaven.runner import Runner
from backuphaven.scheduler import Scheduler
from backuphaven.summary import MorningSummary
from tests.test_alerts import FakeChannel
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


def setup(
    unraid: Settings, db: Database, now: dict[str, datetime]
) -> tuple[MorningSummary, FakeChannel, AlertService]:
    channel = FakeChannel(kinds={"summary"})
    alerts = AlertService(db, lambda: [channel], retry_wait_s=0)
    with db.write() as session:
        schedule.save_apps(session, ["Tool", "sonarr"])
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
        alert_channels.save(
            session, alert_channels.AlertSettings(summary_enabled=True, summary_time="08:00")
        )
    scheduler = Scheduler(db, Runner(db, unraid, plugins_of(unraid)), unraid, lambda: now["now"])
    return MorningSummary(db, alerts, scheduler, "Etc/UTC", lambda: now["now"]), channel, alerts


def test_sent_once_a_day_at_the_chosen_time_with_last_nights_result(
    unraid: Settings, db: Database
) -> None:
    now = {"now": datetime.now(UTC).replace(hour=7, minute=50, second=0, microsecond=0)}
    summary, channel, alerts = setup(unraid, db, now)
    Runner(db, unraid, plugins_of(unraid)).run_now(only=["Tool", "sonarr"], scheduled=True)
    assert summary.maybe_send() is False  # yesterday's 08:00 is long past: skipped
    now["now"] = now["now"].replace(hour=8, minute=0, second=30)
    assert summary.maybe_send() is True
    assert summary.maybe_send() is False  # once
    alerts.flush()
    title, text, state = channel.sent[-1]
    assert title == "Backup summary" and state == "info"
    assert "No backups ran" in text or "OK, 2 apps" in text
    assert "Next backup:" in text


def test_a_summary_missed_by_hours_is_skipped(unraid: Settings, db: Database) -> None:
    now = {"now": datetime.now(UTC).replace(hour=8, minute=0, second=30, microsecond=0)}
    summary, _channel, _alerts = setup(unraid, db, now)
    assert summary.maybe_send() is True
    now["now"] += timedelta(days=1, hours=4)  # server off at 08:00 the next day
    assert summary.maybe_send() is False
    now["now"] += timedelta(days=1)
    now["now"] = now["now"].replace(hour=8, minute=5)
    assert summary.maybe_send() is True


def test_problems_and_open_alerts_are_listed(unraid: Settings, db: Database) -> None:
    now = {"now": datetime.now(UTC) + timedelta(minutes=1)}
    summary, _channel, alerts = setup(unraid, db, now)
    (unraid.config_dir / "plugins" / "tool.yaml").unlink()
    Runner(db, unraid, plugins_of(unraid), alerts=alerts).run_now(only=["Tool"], scheduled=True)
    text = summary.text(now["now"] - timedelta(hours=1), now["now"])
    assert "FAILED" in text and "Needs a look:" in text and "- Tool: no plugin" in text
    assert "Open alerts:" in text and "App not protected" in text and "Backup failed" in text


def test_nothing_when_switched_off(unraid: Settings, db: Database) -> None:
    now = {"now": datetime.now(UTC)}
    summary, _channel, _alerts = setup(unraid, db, now)
    with db.write() as session:
        alert_channels.save(session, alert_channels.AlertSettings(summary_enabled=False))
    assert summary.maybe_send() is False
