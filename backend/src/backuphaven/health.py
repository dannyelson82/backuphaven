"""Hourly checks that raise alerts nothing else would (docs/plans/phase-0.4.md):
no good backup for a long time, and a backups disk nearly full."""

import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backuphaven import retention, schedule
from backuphaven.alerts import AlertService
from backuphaven.config import Settings
from backuphaven.db import Database, Run

logger = logging.getLogger(__name__)

NO_GOOD_AFTER = timedelta(hours=36)
EVERY = timedelta(hours=1)


class HealthCheck:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        alerts: AlertService,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        free_bytes: Callable[[], int] | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._alerts = alerts
        self._clock = clock
        self._free = free_bytes or (lambda: shutil.disk_usage(settings.backups_dir).free)
        self._last: datetime | None = None

    def maybe_run(self) -> None:
        """Run at most once an hour (called from the scheduler's loop)."""
        now = self._clock()
        if self._last is not None and now - self._last < EVERY:
            return
        self._last = now
        self.run(now)

    def run(self, now: datetime) -> None:
        with self._db.read() as session:
            apps = schedule.load_apps(session)
            plan = schedule.load_settings(session)
            min_free = retention.min_free_bytes(session, self._settings.min_free_bytes)
            good = session.scalars(
                select(Run)
                .where(Run.verified.is_(True), Run.status != "failed")
                .order_by(Run.started_at.desc())
                .limit(1)
            ).first()
        if apps and plan.enabled and good is not None:
            age = now - good.started_at
            if age > NO_GOOD_AFTER:
                self._alerts.raise_(
                    "no_good_backup",
                    "no_good_backup",
                    f"The last good backup was {int(age.total_seconds() // 3600)} hours ago.",
                )
            else:
                self._alerts.resolve("no_good_backup", "There's a recent good backup again.")
        try:
            free = self._free()
        except OSError:
            return
        if min_free and free < 2 * min_free:
            self._alerts.raise_(
                "disk_low",
                "disk_low",
                f"Only {free / 1e9:.1f} GB free on the backups disk; backups stop below "
                f"{min_free / 1e9:.1f} GB.",
            )
        else:
            self._alerts.resolve("disk_low", "The backups disk has room again.")
