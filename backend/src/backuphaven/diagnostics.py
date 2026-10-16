"""Diagnostics files: a developer option for debugging (Settings → Developer).

When switched on, BackupHaven writes small files to the diagnostics folder:

- ``debug.log``: every BackupHaven log line down to DEBUG, JSON lines, secrets
  redacted, rotated at 1 MB with 3 old files kept.
- ``run-<id>.json``: one per backup run, with every app, database and timing.
  The newest ``KEEP_RUNS`` are kept.
- ``system.json``: versions, user, capabilities, mounted shares and settings
  (no secrets), rewritten when diagnostics are switched on and at start-up.

The folder is ``/diagnostics`` when that is mapped into the container (so it can
be put anywhere, such as a share another tool can read), otherwise
``<config>/diagnostics``. Files belong to PUID:PGID like the backups.
"""

import contextlib
import json
import logging
import logging.handlers
import os
import platform
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backuphaven import __version__
from backuphaven.config import Settings
from backuphaven.logsetup import JsonFormatter, redact

SETTING_KEY = "developer"
MAPPED_DIR = Path("/diagnostics")
LOG_NAME = "debug.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 3
KEEP_RUNS = 10
FILE_MODE = 0o640


def folder(settings: Settings) -> Path:
    if settings.diagnostics_dir is not None:
        return settings.diagnostics_dir
    return MAPPED_DIR if MAPPED_DIR.is_dir() else settings.config_dir / "diagnostics"


def _own(path: Path, settings: Settings) -> None:
    with contextlib.suppress(OSError):
        path.chmod(FILE_MODE if path.is_file() else 0o750)
        if os.geteuid() == 0:
            os.chown(path, settings.puid, settings.pgid)


class _OwnedRotatingHandler(logging.handlers.RotatingFileHandler):
    """Rotating log whose files belong to PUID:PGID, not root."""

    def __init__(self, path: Path, settings: Settings) -> None:
        self._settings = settings
        super().__init__(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")

    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        _own(Path(self.baseFilename), self._settings)
        return stream


class _OursOrImportant(logging.Filter):
    """BackupHaven's own debug lines; other libraries only from INFO up."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("backuphaven") or record.levelno >= logging.INFO


class Diagnostics:
    """Switches the diagnostics files on and off while BackupHaven runs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._handler: logging.Handler | None = None
        self._ours = logging.getLogger("backuphaven")

    @property
    def enabled(self) -> bool:
        return self._handler is not None

    @property
    def folder(self) -> Path:
        return folder(self._settings)

    def enable(self) -> None:
        if self._handler is not None:
            return
        target = self.folder
        target.mkdir(parents=True, exist_ok=True)
        _own(target, self._settings)
        handler = _OwnedRotatingHandler(target / LOG_NAME, self._settings)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(_OursOrImportant())
        logging.getLogger().addHandler(handler)
        self._ours.setLevel(logging.DEBUG)  # other handlers keep their own level
        self._handler = handler
        self.write_system()
        self._ours.info("diagnostics files on", extra={"folder": str(target)})

    def disable(self) -> None:
        handler, self._handler = self._handler, None
        if handler is None:
            return
        self._ours.info("diagnostics files off")
        logging.getLogger().removeHandler(handler)
        handler.close()
        self._ours.setLevel(logging.NOTSET)

    def _write_json(self, name: str, data: dict[str, Any]) -> None:
        path = self.folder / name
        temp = path.with_name(f".{name}.tmp")
        temp.write_text(redact(json.dumps(data, indent=1, default=str, ensure_ascii=False)))
        _own(temp, self._settings)
        temp.replace(path)

    def write_system(self) -> None:
        if not self.enabled:
            return
        settings = self._settings
        data_root = settings.data_root
        try:
            shares = sorted(p.name for p in data_root.iterdir() if p.is_dir())
        except OSError as exc:
            shares = [f"unreadable: {exc}"]
        try:
            backups = os.statvfs(settings.backups_dir)
            free = backups.f_bavail * backups.f_frsize
        except OSError:
            free = None
        self._write_json(
            "system.json",
            {
                "written": datetime.now(UTC).isoformat(timespec="seconds"),
                "backuphaven": __version__,
                "python": sys.version,
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.platform(),
                "uid": os.getuid(),
                "euid": os.geteuid(),
                "gid": os.getgid(),
                "capabilities": _capabilities(),
                "data_root": str(data_root),
                "data_shares": shares,
                "templates_dir": str(settings.templates_dir),
                "templates": _count(settings.templates_dir, "*.xml"),
                "backups_dir": str(settings.backups_dir),
                "backups_free_bytes": free,
                "settings": settings.model_dump(mode="json"),
            },
        )

    def write_run(self, run_id: int, data: dict[str, Any]) -> None:
        """Save one run's details and keep only the newest ``KEEP_RUNS``."""
        if not self.enabled:
            return
        try:
            self._write_json(f"run-{run_id}.json", data)
            runs = sorted(
                self.folder.glob("run-*.json"),
                key=lambda p: int(p.stem.split("-", 1)[1]) if p.stem[4:].isdigit() else 0,
            )
            for old in runs[:-KEEP_RUNS]:
                old.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("diagnostics run file not written", exc_info=True)

    def files(self) -> list[dict[str, Any]]:
        try:
            entries = sorted(self.folder.iterdir())
        except OSError:
            return []
        return [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime, UTC),
            }
            for p in entries
            if p.is_file() and not p.name.startswith(".")
        ]


def _count(path: Path, pattern: str) -> int | None:
    try:
        return sum(1 for p in path.glob(pattern) if not p.name.startswith("."))
    except OSError:
        return None


def _capabilities() -> dict[str, str]:
    try:
        lines = Path("/proc/self/status").read_text().splitlines()
    except OSError:
        return {}
    return {
        k: v.strip()
        for k, _, v in (line.partition(":") for line in lines)
        if k in ("CapEff", "CapBnd", "NoNewPrivs", "Seccomp")
    }
