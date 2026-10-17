"""The morning summary (docs/plans/phase-0.4.md): once a day, at the owner's time,
how the backups since the last summary went. Sent to channels that want
"summary". If the server was off at that time, it's sent within two hours, or
skipped for that day."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backuphaven import alert_channels, settings_store
from backuphaven.alerts import AlertService
from backuphaven.db import Database, Run, RunApp
from backuphaven.scheduler import Scheduler
from backuphaven.timetable import Timetable, last_due, zone

logger = logging.getLogger(__name__)

STATE_KEY = "summary_state"
LATE = timedelta(hours=2)


def _size(n: int | None) -> str:
    return "-" if n is None else f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class MorningSummary:
    def __init__(
        self,
        db: Database,
        alerts: AlertService,
        scheduler: Scheduler,
        tz_name: str | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._alerts = alerts
        self._scheduler = scheduler
        self._tz = zone(tz_name)
        self._clock = clock

    def maybe_send(self) -> bool:
        """Send today's summary if it's due and not sent yet. Returns whether it sent."""
        now = self._clock()
        with self._db.read() as session:
            config = alert_channels.load(session)
            state = settings_store.get(session, STATE_KEY) or {}
        if not config.summary_enabled:
            return False
        hour, minute = (int(x) for x in config.summary_time.split(":"))
        due = last_due(
            now, Timetable(at=datetime.min.time().replace(hour=hour, minute=minute)), self._tz
        )
        if due is None or state.get("last_due") == due.isoformat():
            return False
        with self._db.write() as session:
            settings_store.put(session, STATE_KEY, {"last_due": due.isoformat()})
        if now - due > LATE:
            return False  # off at that time: skip today rather than send at noon
        since = (
            datetime.fromisoformat(state["last_due"])
            if state.get("last_due")
            else due - timedelta(days=1)
        )
        self._alerts.info("summary", "Backup summary", self.text(since, now))
        return True

    def text(self, since: datetime, now: datetime) -> str:
        with self._db.read() as session:
            runs = session.scalars(
                select(Run)
                .where(Run.started_at > since, Run.status != "running")
                .order_by(Run.started_at)
            ).all()
            apps = session.scalars(
                select(RunApp).where(RunApp.run_id.in_([r.id for r in runs]))
            ).all()
        lines: list[str] = []
        if not runs:
            lines.append("No backups ran since the last summary.")
        for run in runs:
            local = run.started_at.astimezone(self._tz).strftime("%a %H:%M")
            summary = run.summary or {}
            result = {
                "ok": "OK",
                "warning": "Warnings",
                "failed": "FAILED",
                "cancelled": "Cancelled",
            }.get(run.status, run.status)
            lines.append(
                f"{local}: {result}, {summary.get('apps', 0)} apps, {_size(run.size)}, "
                f"{int(summary.get('seconds', 0) // 60)} min"
            )
            if run.error:
                lines.append(f"  {run.error}")
        problems = []
        for a in apps:
            if a.status in ("failed", "warning"):
                found = (
                    (a.problems or {}).get("errors") or (a.problems or {}).get("warnings") or [""]
                )
                problems.append(f"- {a.app}: {found[0]}")
        if problems:
            lines += ["", "Needs a look:", *problems[:8]]
            if len(problems) > 8:
                lines.append(f"…and {len(problems) - 8} more")
        open_alerts = self._alerts.open_alerts()
        if open_alerts:
            lines += ["", "Open alerts: " + ", ".join(a["title"] for a in open_alerts)]
        upcoming = self._scheduler.next_run()
        lines += [
            "",
            "Next backup: "
            + (
                upcoming.astimezone(self._tz).strftime("%a %d %b %H:%M")
                if upcoming
                else "none planned"
            ),
        ]
        return "\n".join(lines)
