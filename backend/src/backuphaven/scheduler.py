"""Starts scheduled backups (ARCHITECTURE.md §6.11). In-app timer, no cron.

A background thread checks every ``TICK_S`` seconds whether a due time has
passed since the last one it handled. If it passed only just now, the backup
starts. If it passed long ago (the server or BackupHaven was off, or a backup
was still running), the backup is **missed**: reported, not run late.

What has been handled is stored in the ``settings`` table (``scheduler``), so a
restart doesn't run a backup twice or forget one that was missed.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backuphaven import schedule, settings_store
from backuphaven.config import Settings
from backuphaven.db import Database
from backuphaven.runner import Runner
from backuphaven.timetable import last_due, next_due, zone

logger = logging.getLogger(__name__)

STATE_KEY = "scheduler"
TICK_S = 30.0
# A due time noticed later than this was missed rather than just reached.
GRACE = timedelta(minutes=10)
KEEP_MISSED = 10


@dataclass(frozen=True)
class Missed:
    due: datetime
    reason: str


class Scheduler:
    def __init__(
        self,
        db: Database,
        runner: Runner,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._runner = runner
        self._tz = zone(settings.tz)
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def timezone(self) -> str:
        return str(self._tz)

    # --- the thread ---------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("scheduler check failed")
            if self._stop.wait(TICK_S):
                return

    # --- the decision -------------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        with self._db.read() as session:
            return settings_store.get(session, STATE_KEY) or {}

    def _save_state(self, state: dict[str, Any]) -> None:
        with self._db.write() as session:
            settings_store.put(session, STATE_KEY, state)

    def tick(self) -> None:
        """Handle a due time that has passed since the last one handled, if any."""
        now = self._clock()
        with self._db.read() as session:
            plan = schedule.load_settings(session)
            apps = schedule.load_apps(session)
        state = self._state()
        handled = state.get("last_due")
        if handled is None or not plan.enabled:
            # First start, or switched off: nothing before now is owed.
            self._save_state({**state, "last_due": now.isoformat()})
            return
        due = last_due(now, plan.timetable(), self._tz)
        if due is None or due <= datetime.fromisoformat(handled):
            return
        state["last_due"] = due.isoformat()
        if not apps:
            logger.info("scheduled backup due, but the backup schedule is empty")
            self._save_state(state)
            return
        if now - due > GRACE:
            self._miss(state, due, "BackupHaven wasn't running at that time")
            return
        run_id = self._runner.start("schedule", apps, scheduled=True)
        if run_id is None:
            self._miss(state, due, "another backup was still running")
            return
        logger.info("scheduled backup started", extra={"run_id": run_id, "due": due.isoformat()})
        self._save_state(state)

    def reset(self) -> None:
        """Forget due times before now (after the timetable changes, so moving the
        time earlier in the day doesn't count today's new time as missed)."""
        self._save_state({**self._state(), "last_due": self._clock().isoformat()})

    def _miss(self, state: dict[str, Any], due: datetime, reason: str) -> None:
        logger.warning("scheduled backup missed", extra={"due": due.isoformat(), "reason": reason})
        missed = [{"due": due.isoformat(), "reason": reason}, *state.get("missed", [])]
        self._save_state({**state, "missed": missed[:KEEP_MISSED]})

    # --- for the API ----------------------------------------------------------------------

    def next_run(self) -> datetime | None:
        with self._db.read() as session:
            plan = schedule.load_settings(session)
            apps = schedule.load_apps(session)
        if not plan.enabled or not apps:
            return None
        return next_due(self._clock(), plan.timetable(), self._tz)

    def missed(self) -> list[Missed]:
        return [
            Missed(due=datetime.fromisoformat(m["due"]), reason=str(m["reason"]))
            for m in self._state().get("missed", [])
        ]
