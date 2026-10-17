"""Alerts (ARCHITECTURE.md §6.10, docs/plans/phase-0.4.md).

Parts of BackupHaven *raise* an alert when something goes wrong and *resolve* it
when it's fixed. Each alert has a key that names the problem (``run_failed``,
``db_damaged:plex/library.db``); while an alert with that key is open, raising it
again within ``REPEAT_AFTER`` sends nothing, and resolving it sends one
"recovered" message. Every alert is stored, sent or not.

Sending happens on a background thread, to every enabled channel that wants that
kind of alert, with a few retries. Channels come from the settings (chunk 2
fills in the channel types); a channel only needs ``send(title, message, state)``.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select

from backuphaven.db import Alert, Database
from backuphaven.db.types import utcnow

logger = logging.getLogger(__name__)

REPEAT_AFTER = timedelta(hours=12)
RETRIES = 3
RETRY_WAIT_S = 5.0

KINDS: dict[str, str] = {
    "run_failed": "Backup failed",
    "db_damaged": "Database damaged",
    "missed": "Backup missed",
    "no_good_backup": "No good backup",
    "disk_low": "Disk nearly full",
    "restore_failed": "Restore failed",
    "unprotected": "App not protected",
    "test": "Test",
    "summary": "Morning summary",
}


class Channel(Protocol):
    id: str
    name: str

    def send(self, title: str, message: str, state: str) -> None:
        """Deliver one message; raise on failure."""
        ...

    def wants(self, kind: str) -> bool:
        """Whether this channel sends alerts of this kind."""
        ...


@dataclass(frozen=True)
class Outgoing:
    alert_id: int
    title: str
    message: str
    state: str  # open | recovered | info
    kind: str


class AlertService:
    """Raises, resolves and sends alerts."""

    def __init__(
        self,
        db: Database,
        channels: Callable[[], list[Channel]] = list,
        retry_wait_s: float = RETRY_WAIT_S,
    ) -> None:
        self._db = db
        self._channels = channels
        self._retry_wait = retry_wait_s
        self._queue: queue.Queue[Outgoing | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- the sender thread ---------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    def _loop(self) -> None:
        while (item := self._queue.get()) is not None:
            try:
                self._deliver(item)
            except Exception:
                logger.exception("alert delivery failed")

    def flush(self) -> None:
        """Send everything queued now, in this thread (tests, and no thread running)."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                self._deliver(item)

    def _deliver(self, item: Outgoing) -> None:
        results: dict[str, str] = {}
        for channel in self._channels():
            if not channel.wants(item.kind):
                continue
            for attempt in range(RETRIES):
                try:
                    channel.send(item.title, item.message, item.state)
                except Exception as exc:  # any failure of an outside service
                    results[channel.id] = f"failed: {exc}"[:300]
                    if attempt + 1 < RETRIES:
                        time.sleep(self._retry_wait * (attempt + 1))
                    continue
                results[channel.id] = "sent"
                break
            if results[channel.id] != "sent":
                logger.warning(
                    "alert not delivered",
                    extra={"channel": channel.name, "reason": results[channel.id]},
                )
        with self._db.write() as session:
            row = session.get(Alert, item.alert_id)
            if row is not None:
                row.delivery = {**(row.delivery or {}), **results}

    # --- raising and resolving -----------------------------------------------------------------

    def raise_(self, kind: str, key: str, message: str, title: str | None = None) -> bool:
        """Report a problem. Returns whether it was sent (not a repeat)."""
        title = title or KINDS.get(kind, kind)
        now = datetime.now(UTC)
        with self._lock, self._db.write() as session:
            row = session.scalars(
                select(Alert).where(Alert.key == key, Alert.state == "open").limit(1)
            ).first()
            if row is not None and row.last_sent_at and now - row.last_sent_at < REPEAT_AFTER:
                row.message = message  # keep the newest detail, don't send again
                return False
            if row is None:
                row = Alert(kind=kind, key=key, title=title, message=message, state="open")
                session.add(row)
            else:
                row.message = message
            row.last_sent_at = utcnow()
            session.flush()
            outgoing = Outgoing(row.id, title, message, "open", kind)
        logger.info("alert", extra={"kind": kind, "key": key, "title": title})
        self._queue.put(outgoing)
        return True

    def resolve(self, key_prefix: str, message: str) -> int:
        """Close open alerts whose key starts with ``key_prefix``; send "recovered"
        for each. Returns how many were closed."""
        closed: list[Outgoing] = []
        with self._lock, self._db.write() as session:
            rows = session.scalars(
                select(Alert).where(Alert.state == "open", Alert.key.startswith(key_prefix))
            ).all()
            for row in rows:
                row.state, row.recovered_at = "recovered", utcnow()
                closed.append(
                    Outgoing(row.id, f"Recovered: {row.title}", message, "recovered", row.kind)
                )
        for item in closed:
            self._queue.put(item)
        return len(closed)

    def info(self, kind: str, title: str, message: str) -> None:
        """A message that isn't a problem (test, morning summary)."""
        with self._db.write() as session:
            row = Alert(
                kind=kind,
                key=f"{kind}:{time.time_ns()}",
                title=title,
                message=message,
                state="info",
            )
            row.last_sent_at = utcnow()
            session.add(row)
            session.flush()
            outgoing = Outgoing(row.id, title, message, "info", kind)
        self._queue.put(outgoing)

    def open_alerts(self) -> list[dict[str, Any]]:
        with self._db.read() as session:
            rows = session.scalars(
                select(Alert).where(Alert.state == "open").order_by(Alert.created_at.desc())
            ).all()
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "title": r.title,
                "message": r.message,
                "since": r.created_at,
            }
            for r in rows
        ]
