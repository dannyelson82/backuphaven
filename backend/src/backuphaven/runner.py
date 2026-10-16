"""The run engine (ARCHITECTURE.md §6.5): one backup run, one archive.

Apps are backed up one at a time; a failure in one app never stops the others. The
archive is written as ``.partial``, verified by reading it back, and only then
renamed into place, so every ``backuphaven-*.tar.zst`` in the backups folder is a
verified archive.
"""

import contextlib
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from backuphaven import __version__, discovery, retention
from backuphaven.archive import PARTIAL_SUFFIX, ArchiveWriter, folder_key, safe_name, verify
from backuphaven.config import Settings
from backuphaven.db import Database, DbCheck, Run, RunApp
from backuphaven.db.types import utcnow
from backuphaven.diagnostics import Diagnostics
from backuphaven.plugins.manifest import Manifest
from backuphaven.plugins.registry import Registry
from backuphaven.selection import OutsideShareError, effective_rules, resolve_app_folder, select
from backuphaven.sqlite_tools import SnapshotError, check, snapshot

logger = logging.getLogger(__name__)

Status = Literal["ok", "warning", "failed", "skipped"]
ARCHIVE_PREFIX = "backuphaven-"
ARCHIVE_SUFFIX = ".tar.zst"
MANIFEST_SUFFIX = ".manifest.json"
WORK_DIR = ".work"
_BIG_FILE = 50 * 1024 * 1024  # logged one by one in the diagnostics files
FORMAT = 1


class LowSpaceError(Exception):
    """The backups disk is below the minimum free space."""


class CancelledError(Exception):
    """Someone pressed Cancel."""


class NoSuchAppsError(ValueError):
    """Run now was asked for apps that aren't there."""


def remove_leftovers(backups: Path) -> list[str]:
    """Delete what a run cut off by a restart left behind: its half-written archive
    and its working folder. Call only while no run is going (at start-up)."""
    removed: list[str] = []
    for partial in backups.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}.partial"):
        if partial.is_file() and not partial.is_symlink():
            partial.unlink(missing_ok=True)
            removed.append(partial.name)
    work = backups / WORK_DIR
    if work.is_dir() and not work.is_symlink():
        shutil.rmtree(work, ignore_errors=True)
        removed.append(WORK_DIR)
    return removed


@dataclass
class Progress:
    """What the running run is doing, for the status page."""

    run_id: int
    started: float
    app: str | None = None
    apps_done: int = 0
    apps_total: int = 0
    files: int = 0
    bytes: int = 0
    phase: str = "starting"


@dataclass
class AppResult:
    name: str
    plugin: str
    status: Status = "ok"
    files: int = 0
    bytes: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    databases: list[dict[str, Any]] = field(default_factory=list)

    def error(self, text: str) -> None:
        self.status = "failed"
        if len(self.errors) < 100:
            self.errors.append(text)

    def skip(self, reason: str) -> None:
        """Not backed up, on purpose: not a failure (ADR-0011)."""
        self.status = "skipped"
        self.warnings = [reason]

    def warn(self, text: str) -> None:
        if self.status == "ok":
            self.status = "warning"
        if len(self.warnings) < 100:
            self.warnings.append(text)


def not_installed(data_root: Path, app: discovery.App) -> str | None:
    """Why an app looks removed, or None. Removed containers keep their Unraid
    template; when none of the app's data folders exist any more, there is
    nothing to back up (ADR-0011). Apps on unmounted shares aren't judged."""
    if not app.folders or app.missing:
        return None
    for folder in app.folders:
        try:
            if resolve_app_folder(data_root, folder.host).exists():
                return None
        except (OutsideShareError, ValueError):
            return None  # reported properly by the backup itself
    hosts = ", ".join(f.host for f in app.folders)
    return f"not installed: {hosts} doesn't exist (container removed?)"


def worst(statuses: list[Status]) -> Status:
    """The run's result from its apps'; skipped apps don't count (ADR-0011)."""
    if "failed" in statuses:
        return "failed"
    return "warning" if "warning" in statuses else "ok"


def archive_name(now: datetime, taken: Callable[[str], bool] = lambda _: False) -> str:
    """``backuphaven-<date>T<time>.tar.zst``; ``-2``, ``-3``… when that name is taken
    (two backups started in the same second)."""
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    name, n = f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}", 2
    while taken(name):
        name, n = f"{ARCHIVE_PREFIX}{stamp}-{n}{ARCHIVE_SUFFIX}", n + 1
    return name


def _unique(name: str, used: set[str]) -> str:
    key, n = name, 2
    while key.lower() in used:
        key, n = f"{name}-{n}", n + 1
    used.add(key.lower())
    return key


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class Runner:
    """Runs backups, one at a time."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        registry: Registry,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        self._db = db
        self._diagnostics = diagnostics
        self._settings = settings
        self._registry = registry
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self.progress: Progress | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start(
        self, trigger: str, only: list[str] | None = None, scheduled: bool = False
    ) -> int | None:
        """Start a run in the background. None if one is already running.

        ``only`` limits the run to those apps (by name). Chosen by hand, names that
        aren't found raise ``NoSuchAppsError``; from the backup schedule
        (``scheduled``), they are reported as skipped instead."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if not scheduled:
                self._check_names(only)
            run_id = self._create_run(trigger, only, chosen=only is not None and not scheduled)
        except Exception:
            self._lock.release()
            raise
        self._cancel.clear()
        thread = threading.Thread(
            target=self._run_and_release, args=(run_id, only), name="backup-run"
        )
        thread.start()
        return run_id

    def run_now(
        self, trigger: str = "manual", only: list[str] | None = None, scheduled: bool = False
    ) -> int:
        """Run in this thread (tests and the scheduler's own thread)."""
        with self._lock:
            if not scheduled:
                self._check_names(only)
            self._cancel.clear()
            run_id = self._create_run(trigger, only, chosen=only is not None and not scheduled)
            self._run(run_id, only)
        return run_id

    def cancel(self) -> bool:
        """Ask the running run to stop. False if nothing is running."""
        if not self.running:
            return False
        self._cancel.set()
        if self.progress is not None and self.progress.phase != "verifying":
            self.progress.phase = "cancelling"
        return True

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise CancelledError

    def _check_names(self, only: list[str] | None) -> None:
        if only is None:
            return
        if not only:
            raise NoSuchAppsError("choose at least one app")
        found = {a.name.lower() for a in discovery.discover(*self._discovery_dirs())}
        unknown = [n for n in only if n.lower() not in found]
        if unknown:
            raise NoSuchAppsError("not found: " + ", ".join(unknown))

    def _discovery_dirs(self) -> tuple[Path, Path]:
        return self._settings.templates_dir, self._settings.data_root

    def _run_and_release(self, run_id: int, only: list[str] | None) -> None:
        try:
            self._run(run_id, only)
        finally:
            self._lock.release()

    def _create_run(self, trigger: str, only: list[str] | None = None, chosen: bool = False) -> int:
        """``chosen``: Choose apps… rather than the backup schedule; the keep rules
        treat those backups as extras."""
        summary = None if only is None else {"only": only, "chosen": chosen}
        with self._db.write() as session:
            run = Run(trigger=trigger, status="running", summary=summary)
            session.add(run)
            session.flush()
            return run.id

    # --- the run itself ----------------------------------------------------------------

    def _run(self, run_id: int, only: list[str] | None = None) -> None:
        settings = self._settings
        started = time.monotonic()
        self.progress = Progress(run_id=run_id, started=started)
        backups = settings.backups_dir
        work = backups / WORK_DIR / str(run_id)
        results: list[AppResult] = []
        error: str | None = None
        archive: Path | None = None
        size: int | None = None
        verified = False
        writer: ArchiveWriter | None = None
        cancelled = False
        with self._db.read() as session:
            min_free = retention.min_free_bytes(session, settings.min_free_bytes)
        try:
            backups.mkdir(parents=True, exist_ok=True)
            if _free_bytes(backups) < min_free:
                raise LowSpaceError("not enough free space on the backups disk")
            work.mkdir(parents=True, exist_ok=True)
            apps = discovery.discover(*self._discovery_dirs())
            found = len(apps)
            gone: list[str] = []
            if only is not None:
                wanted = {n.lower() for n in only}
                found_names = {a.name.lower() for a in apps}
                gone = [n for n in only if n.lower() not in found_names]
                apps = [a for a in apps if a.name.lower() in wanted]
            logger.debug(
                "run starting",
                extra={
                    "run_id": run_id,
                    "apps_found": found,
                    "apps": [a.name for a in apps],
                    "only": only,
                    "free_bytes": _free_bytes(backups),
                },
            )
            self.progress.apps_total = len(apps)
            archive = backups / archive_name(
                datetime.now(UTC),
                lambda name: (
                    (backups / name).exists() or (backups / f"{name}{PARTIAL_SUFFIX}").exists()
                ),
            )
            writer = ArchiveWriter(archive)
            used: set[str] = set()
            for app in apps:
                self._check_cancel()
                key = _unique(safe_name(app.name), used)
                self.progress.app, self.progress.phase = app.name, "backing up"
                if self._cancel.is_set():
                    self.progress.phase = "cancelling"
                results.append(self._backup_app(writer, app, key, work))
                self.progress.apps_done += 1
                if _free_bytes(backups) < min_free:
                    raise LowSpaceError("the backups disk filled up during the run")
            for name in gone:  # in the backup schedule, but its template is gone
                missing = AppResult(name=name, plugin="-")
                missing.skip("not found: no Unraid template with this name any more")
                results.append(missing)
            self._check_cancel()
            self.progress.app, self.progress.phase = None, "verifying"
            step = time.monotonic()
            manifest = writer.close(self._manifest(run_id, results, only))
            writer = None
            logger.debug("archive written", extra={"seconds": round(time.monotonic() - step, 2)})
            step = time.monotonic()
            check_result = verify(archive.with_name(archive.name + ".partial"), manifest)
            logger.debug(
                "archive verified",
                extra={
                    "seconds": round(time.monotonic() - step, 2),
                    "ok": check_result.ok,
                    "entries": check_result.entries,
                },
            )
            if not check_result.ok:
                raise RuntimeError(
                    "the archive failed verification: " + "; ".join(check_result.problems[:5])
                )
            partial = archive.with_name(archive.name + ".partial")
            partial.rename(archive)
            sidecar = archive.with_name(archive.name + MANIFEST_SUFFIX)
            sidecar.write_bytes(manifest)
            sidecar.chmod(0o600)
            for path in (archive, sidecar):
                self._own(path)
            size, verified = archive.stat().st_size, True
        except Exception as exc:
            if isinstance(exc, CancelledError):
                cancelled = True
                logger.info("backup run cancelled", extra={"run_id": run_id})
            else:
                logger.exception("backup run failed", extra={"run_id": run_id})
                error = str(exc) or type(exc).__name__
            if writer is not None:
                writer.abort()
            if archive is not None:
                archive.with_name(archive.name + ".partial").unlink(missing_ok=True)
            archive = None
        finally:
            shutil.rmtree(work, ignore_errors=True)
            with contextlib.suppress(OSError):
                work.parent.rmdir()  # only if no other run's work is there
            self._save(
                run_id, results, archive, size, verified, error, time.monotonic() - started,
                only, cancelled,
            )  # fmt: skip
            self.progress = None
            self._cancel.clear()
            self._apply_keep_rules()

    def _apply_keep_rules(self) -> None:
        try:
            retention.apply(self._db, self._settings.backups_dir, self._settings.tz)
        except Exception:
            logger.exception("keep rules failed; no backups were deleted by this step")

    def _own(self, path: Path) -> None:
        if os.geteuid() == 0:
            os.chown(path, self._settings.puid, self._settings.pgid)

    def _manifest(
        self, run_id: int, results: list[AppResult], only: list[str] | None
    ) -> dict[str, Any]:
        return {
            "format": FORMAT,
            # Apps chosen for this run; None means every app was included.
            "only": only,
            "backuphaven": __version__,
            "run_id": run_id,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "apps": [
                {
                    "name": r.name,
                    "plugin": r.plugin,
                    "status": r.status,
                    "files": r.files,
                    "bytes": r.bytes,
                    "databases": r.databases,
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in results
            ],
        }

    def _backup_app(
        self, writer: ArchiveWriter, app: discovery.App, key: str, work: Path
    ) -> AppResult:
        started = time.monotonic()
        plugin = self._registry.match(app.image)
        result = AppResult(name=app.name, plugin=plugin.id)
        logger.debug(
            "app starting",
            extra={
                "app": app.name,
                "image": app.image,
                "plugin": plugin.id,
                "folders": [f"{f.target}={f.host}" for f in app.folders],
                "missing": [f"{m.target}={m.host}" for m in app.missing],
            },
        )
        base = f"apps/{key}"
        gone = not_installed(self._settings.data_root, app)
        if gone is not None:
            result.skip(gone)
            logger.debug("app skipped", extra={"app": app.name, "reason": gone})
            return result
        for missing in app.missing:
            result.warn(
                f"{missing.target} is on the '{missing.share}' share, which BackupHaven "
                "can't see: add it to BackupHaven's container settings"
            )
        if app.template_file:
            try:
                writer.add_bytes(f"{base}/template.xml", Path(app.template_file).read_bytes())
            except OSError as exc:
                result.warn(f"template couldn't be read: {exc}")
        if plugin.backup.mode == "stop":
            result.warn("stop mode needs the socket proxy (coming later); backed up while running")
        folders = [f for f in app.folders if plugin.backup.folder in ("*", f.target)]
        if not folders and not app.missing:
            if app.folders:
                result.error(f"no data folder mapped to {plugin.backup.folder}")
            else:
                result.warn("no data folders on the data shares")
        for folder in folders:
            self._backup_folder(writer, plugin, folder, f"{base}", work, result)
        result.seconds = time.monotonic() - started
        logger.debug(
            "app finished",
            extra={
                "app": app.name,
                "status": result.status,
                "files": result.files,
                "bytes": result.bytes,
                "seconds": round(result.seconds, 2),
            },
        )
        return result

    def _backup_folder(
        self,
        writer: ArchiveWriter,
        plugin: Manifest,
        folder: discovery.DataFolder,
        base: str,
        work: Path,
        result: AppResult,
    ) -> None:
        fkey = folder_key(folder.target)
        try:
            root = resolve_app_folder(self._settings.data_root, folder.host)
        except (OutsideShareError, ValueError) as exc:
            result.error(str(exc))
            return
        if not root.is_dir():
            if root.is_file():  # a single file mapped into the container
                result.warn(f"{folder.target} is a single file; not backed up yet")
            else:
                result.error(f"{folder.host} doesn't exist")
            return
        step = time.monotonic()
        selection = select(root, effective_rules(plugin))
        logger.debug(
            "folder read",
            extra={
                "app": result.name,
                "folder": str(root),
                "files": len(selection.files),
                "databases": [e.rel for e in selection.databases],
                "bytes": selection.total_bytes,
                "special": len(selection.skipped_special),
                "seconds": round(time.monotonic() - step, 2),
            },
        )
        step = time.monotonic()
        for rel in selection.skipped_special:
            result.warn(f"skipped special file {rel}")
        for pattern in selection.missing_expected:
            result.error(f"expected file missing: {pattern}")
        for entry in selection.files:
            self._check_cancel()
            name = f"{base}/files/{fkey}/{entry.rel}"
            source = root / entry.rel
            try:
                record = (
                    writer.add_symlink(name, source)
                    if entry.kind == "symlink"
                    else writer.add_file(name, source)
                )
            except FileNotFoundError:
                continue  # removed since the folder was read: nothing to keep
            except OSError as exc:
                result.error(f"{entry.rel}: {exc.strerror or exc}")
                continue
            if record.changed:
                result.warn(f"{entry.rel} changed while it was read")
            if record.size >= _BIG_FILE:
                logger.debug(
                    "big file stored",
                    extra={"app": result.name, "file": entry.rel, "bytes": record.size},
                )
            result.files += 1
            result.bytes += record.size
            self._count(record.size)
        logger.debug(
            "files stored",
            extra={
                "app": result.name,
                "folder": str(root),
                "seconds": round(time.monotonic() - step, 2),
            },
        )
        for index, entry in enumerate(selection.databases):
            self._check_cancel()
            self._backup_database(
                writer, root, entry.rel, f"{base}/databases/{fkey}", work, index, result
            )

    def _backup_database(
        self,
        writer: ArchiveWriter,
        root: Path,
        rel: str,
        base: str,
        work: Path,
        index: int,
        result: AppResult,
    ) -> None:
        source = root / rel
        copy = work / f"{safe_name(result.name)}-{index}.db"
        record: dict[str, Any] = {"path": rel}
        try:
            wal = Path(f"{source}-wal")
            wal_bytes = wal.stat().st_size if wal.exists() else 0
            logger.debug(
                "database copy starting",
                extra={
                    "app": result.name,
                    "db": rel,
                    "bytes": source.stat().st_size,
                    "wal_bytes": wal_bytes,
                    "copy_to": str(copy),
                },
            )
            snap = snapshot(source, copy)
            logger.debug(
                "database copied",
                extra={
                    "app": result.name,
                    "db": rel,
                    "method": snap.method,
                    "seconds": round(snap.seconds, 2),
                },
            )
            checked = check(copy)
            timing = {
                "copy_method": snap.method,
                "copy_seconds": round(snap.seconds, 2),
                "check_seconds": round(checked.seconds, 2),
                "wal_bytes": wal_bytes,
            }
            logger.info(
                "database backed up",
                extra={"app": result.name, "db": rel, "status": checked.status, **timing},
            )
            record.update(
                status=checked.status,
                mode=checked.mode,
                size=snap.size,
                seconds=round(snap.seconds + checked.seconds, 2),
                timing=timing,
                messages=list(checked.messages),
                unknown_collations=list(checked.unknown_collations),
                skipped_tables=list(checked.skipped_tables),
            )
            stored = writer.add_file(f"{base}/{rel}", copy, source.stat())
            result.files += 1
            result.bytes += stored.size
            self._count(stored.size)
            if checked.status == "damaged":
                result.error(f"database {rel} is damaged")
            elif checked.status == "unchecked":
                result.warn(f"database {rel} couldn't be checked: {', '.join(checked.messages)}")
        except SnapshotError as exc:
            record.update(
                status="damaged" if exc.kind == "damaged" else "failed", messages=[str(exc)]
            )
            result.error(f"database {rel} couldn't be copied: {exc}")
        except OSError as exc:
            record.update(status="failed", messages=[str(exc)])
            result.error(f"database {rel}: {exc}")
        finally:
            copy.unlink(missing_ok=True)
        result.databases.append(record)

    def _count(self, size: int) -> None:
        if self.progress is not None:
            self.progress.files += 1
            self.progress.bytes += size

    def _save(
        self,
        run_id: int,
        results: list[AppResult],
        archive: Path | None,
        size: int | None,
        verified: bool,
        error: str | None,
        seconds: float,
        only: list[str] | None = None,
        cancelled: bool = False,
    ) -> None:
        status: Status | Literal["cancelled"] = (
            "cancelled" if cancelled else "failed" if error else worst([r.status for r in results])
        )
        with self._db.write() as session:
            run = session.get(Run, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = utcnow()
            run.archive = archive.name if archive else None
            run.size = size
            run.verified = verified
            run.error = error
            run.summary = {
                **(run.summary or {}),
                "apps": len(results),
                "failed": sum(r.status == "failed" for r in results),
                "skipped": sum(r.status == "skipped" for r in results),
                "warnings": sum(r.status == "warning" for r in results),
                "files": sum(r.files for r in results),
                "seconds": round(seconds, 1),
                **({} if only is None else {"only": only}),
            }
            for r in results:
                session.add(
                    RunApp(
                        run_id=run_id,
                        app=r.name,
                        plugin=r.plugin,
                        status=r.status,
                        files=r.files,
                        bytes=r.bytes,
                        seconds=round(r.seconds, 2),
                        problems={"errors": r.errors, "warnings": r.warnings},
                    )
                )
                for d in r.databases:
                    session.add(
                        DbCheck(
                            run_id=run_id,
                            app=r.name,
                            path=d["path"],
                            status=d.get("status", "failed"),
                            mode=d.get("mode"),
                            size=d.get("size", 0),
                            seconds=d.get("seconds", 0.0),
                            messages={
                                "messages": d.get("messages", []),
                                "unknown_collations": d.get("unknown_collations", []),
                                "skipped_tables": d.get("skipped_tables", []),
                                "timing": d.get("timing", {}),
                            },
                        )
                    )
        logger.info(
            "backup run finished",
            extra={
                "run_id": run_id,
                "status": status,
                "archive": archive.name if archive else None,
            },
        )
        if self._diagnostics is not None:
            self._diagnostics.write_run(
                run_id,
                {
                    "run_id": run_id,
                    "status": status,
                    "error": error,
                    "only": only,
                    "archive": archive.name if archive else None,
                    "size": size,
                    "verified": verified,
                    "seconds": round(seconds, 2),
                    "finished": datetime.now(UTC).isoformat(timespec="seconds"),
                    "apps": [asdict(r) for r in results],
                },
            )
