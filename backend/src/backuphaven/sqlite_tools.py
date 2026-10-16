"""SQLite snapshots and checks (ARCHITECTURE.md §6.2, §6.7).

A snapshot is a consistent copy of a live database made with SQLite's online backup
API, so the app keeps running. It never changes the app's files:

- While the app has the database open in WAL mode (its ``-wal``/``-shm`` files exist),
  the source is opened read-only next to it, like any other reader.
- Otherwise it is opened ``immutable``: SQLite then creates no ``-wal``/``-shm`` files
  (a plain read-only open would, owned by root, in the app's folder). If the file
  changes during the copy, the app has started, and the copy is made again the first
  way.

Checks run on the snapshot, never on the live database.
"""

import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.request import pathname2url

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "damaged", "unchecked"]
MAX_MESSAGES = 20
_MAX_STUB_COLLATIONS = 10


class SnapshotError(Exception):
    def __init__(self, kind: Literal["damaged", "unreadable"], message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class SnapshotResult:
    size: int
    seconds: float
    method: Literal["live", "immutable"]


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    mode: Literal["full", "quick"]
    seconds: float
    messages: tuple[str, ...] = ()
    # Collations the database uses that BackupHaven doesn't know (e.g. Plex's own).
    unknown_collations: tuple[str, ...] = field(default=())
    # Search tables left out because they need an add-on BackupHaven doesn't have.
    skipped_tables: tuple[str, ...] = field(default=())


def _state(path: Path) -> tuple[int, int, bool]:
    info = path.stat()
    wal = Path(f"{path}-wal").exists()
    return info.st_size, info.st_mtime_ns, wal


def _is_live(path: Path) -> bool:
    return Path(f"{path}-wal").exists() or Path(f"{path}-shm").exists()


def _uri(path: Path, immutable: bool) -> str:
    return f"file:{pathname2url(str(path))}?mode=ro" + ("&immutable=1" if immutable else "")


def _copy(source: Path, dest: Path, immutable: bool, timeout_s: float) -> None:
    dest.unlink(missing_ok=True)
    with (
        closing(sqlite3.connect(_uri(source, immutable), uri=True, timeout=timeout_s)) as src,
        closing(sqlite3.connect(dest)) as dst,
    ):
        src.backup(dst)  # one step: a single consistent read of the whole database


def snapshot(source: Path, dest: Path, timeout_s: float = 60.0) -> SnapshotResult:
    """Copy the database at ``source`` to ``dest`` (a new file BackupHaven owns)."""
    started = time.monotonic()
    method: Literal["live", "immutable"] = "live" if _is_live(source) else "immutable"
    try:
        if method == "immutable":
            before = _state(source)
            _copy(source, dest, immutable=True, timeout_s=timeout_s)
            logger.debug(
                "immutable copy done",
                extra={"db": str(source), "seconds": round(time.monotonic() - started, 2)},
            )
            if _state(source) != before or _is_live(source):
                logger.info(
                    "database changed during the copy; copying again", extra={"db": str(source)}
                )
                method = "live"
        if method == "live":
            _copy(source, dest, immutable=False, timeout_s=timeout_s)
    except sqlite3.DatabaseError as exc:
        dest.unlink(missing_ok=True)
        text = str(exc)
        kind: Literal["damaged", "unreadable"] = (
            "damaged" if "malformed" in text or "not a database" in text else "unreadable"
        )
        raise SnapshotError(kind, text) from None
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise SnapshotError("unreadable", str(exc)) from None
    return SnapshotResult(dest.stat().st_size, time.monotonic() - started, method)


def _stub_collation(a: str, b: str) -> int:
    return (a > b) - (a < b)


def _add_stub(conn: sqlite3.Connection, stubs: list[str], text: str) -> bool:
    """Stand in for a collation the app has and BackupHaven doesn't, if that's the error."""
    prefix = "no such collation sequence: "
    if not text.startswith(prefix) or len(stubs) >= _MAX_STUB_COLLATIONS:
        return False
    stubs.append(text[len(prefix) :])
    conn.create_collation(stubs[-1], _stub_collation)
    return True


def _needs_addon(text: str) -> bool:
    return text.startswith(("unknown tokenizer", "no such module", "no such tokenizer"))


def _check_tables(
    conn: sqlite3.Connection, stubs: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``quick_check`` table by table, leaving out virtual tables (full-text search,
    spellfix) whose add-on isn't loaded. Their data lives in ordinary shadow tables,
    which are still checked. Returns the problems found and the tables left out."""
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_schema WHERE type = 'table' ORDER BY name"
    ).fetchall()
    skipped = tuple(n for n, sql in tables if (sql or "").upper().startswith("CREATE VIRTUAL"))
    problems: list[str] = []
    for name, _sql in tables:
        if name in skipped or len(problems) >= MAX_MESSAGES:
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        step = time.monotonic()
        while True:
            try:
                rows = conn.execute(f"PRAGMA quick_check({quoted})").fetchall()
                break
            except sqlite3.OperationalError as exc:
                if not _add_stub(conn, stubs, str(exc)):
                    raise
        problems += [str(r[0]) for r in rows if r[0] != "ok"]
        took = time.monotonic() - step
        if took >= 1:
            logger.debug("slow table check", extra={"table": name, "seconds": round(took, 2)})
    return tuple(problems[:MAX_MESSAGES]), skipped


def check(path: Path, timeout_s: float = 600.0, full_max_bytes: int = 8 << 30) -> CheckResult:
    """Check a snapshot for damage.

    ``integrity_check`` normally; ``quick_check`` (structure only) for very large
    databases, and for databases that use collations BackupHaven doesn't know, because
    a stand-in collation would make the full check report false damage. If even that
    needs an add-on (Plex's search index uses its own tokenizer), every table except
    those search tables is checked on its own.
    """
    started = time.monotonic()
    deadline = started + timeout_s
    stubs: list[str] = []
    skipped: tuple[str, ...] = ()
    status: CheckStatus
    mode: Literal["full", "quick"] = "full" if path.stat().st_size <= full_max_bytes else "quick"
    messages: tuple[str, ...] = ()
    with closing(sqlite3.connect(_uri(path, immutable=True), uri=True)) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        while True:
            pragma = "integrity_check" if mode == "full" else "quick_check"
            logger.debug(
                "database check",
                extra={
                    "db": str(path),
                    "pragma": pragma,
                    "after_seconds": round(time.monotonic() - started, 2),
                },
            )
            try:
                rows = conn.execute(f"PRAGMA {pragma}({MAX_MESSAGES})").fetchall()
            except sqlite3.OperationalError as exc:
                text = str(exc)
                if _add_stub(conn, stubs, text):
                    mode = "quick"
                    continue
                if mode == "full" and "interrupted" not in text:
                    mode = "quick"  # e.g. an unknown full-text tokenizer
                    continue
                if _needs_addon(text):
                    logger.debug("checking table by table", extra={"db": str(path), "reason": text})
                    try:
                        messages, skipped = _check_tables(conn, stubs)
                    except sqlite3.OperationalError as inner:
                        text = str(inner)
                    except sqlite3.DatabaseError as inner:
                        status, messages = "damaged", (str(inner),)
                        break
                    else:
                        status = "damaged" if messages else "ok"
                        break
                status = "unchecked"
                messages = ("check took too long",) if "interrupted" in text else (text,)
                break
            except sqlite3.DatabaseError as exc:
                status, messages = "damaged", (str(exc),)
                break
            results = tuple(str(r[0]) for r in rows)
            status = "ok" if results == ("ok",) else "damaged"
            messages = () if status == "ok" else results[:MAX_MESSAGES]
            break
    return CheckResult(
        status=status,
        mode=mode,
        seconds=time.monotonic() - started,
        messages=messages,
        unknown_collations=tuple(stubs),
        skipped_tables=skipped,
    )
