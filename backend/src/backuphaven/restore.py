"""Restoring an app, and undoing it (ARCHITECTURE.md §6.9, ADR-0007).

This is the only code that writes into data shares, and only for a restore the
owner started, only in that app's data folders and its set-aside folder.

A restore, for each of the app's data folders:

1. checks the folder isn't changing (the container must be stopped);
2. renames it to ``<share>/.backuphaven-restore/<app>-<time>/<folder key>``,
   a rename on the same share, so nothing is copied or deleted;
3. creates the folder again with its old owner and permissions, and unpacks the
   backup into it (``unpack.extract``: checked names, no links followed,
   checksums verified);
4. moves back from the set-aside folder what the backup deliberately left out
   (caches, posters, logs; plugin rules), except database side files (``-wal``,
   ``-shm``, ``-journal``), which belong to the old database and would damage the
   restored one.

If anything fails, the restored folders are removed and the set-aside ones put
back. **Undo** does the same on purpose. Set-aside folders are deleted when the
undo time runs out.
"""

import logging
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from backuphaven import discovery, unpack
from backuphaven.archive import folder_key
from backuphaven.config import Settings
from backuphaven.db import Database, Restore, Run
from backuphaven.db.types import utcnow
from backuphaven.plugins.manifest import Manifest
from backuphaven.plugins.registry import Registry
from backuphaven.retention import is_ours
from backuphaven.selection import (
    SIDECARS,
    OutsideShareError,
    Rules,
    _dir_excluded,
    could_contain,
    effective_rules,
    matches,
    resolve_app_folder,
)

logger = logging.getLogger(__name__)

ASIDE_DIR = ".backuphaven-restore"
UNDO_DAYS = 7
QUIET_SECONDS = 5.0


class RestoreError(Exception):
    """The restore can't start or didn't finish; nothing was left half-done."""


@dataclass
class FolderPlan:
    key: str  # folder key inside the backup (files/<key>/, databases/<key>/)
    target: str  # the path inside the container, e.g. /config
    host: str  # /mnt/user/<share>/...
    path: Path  # resolved, checked path inside BackupHaven
    share_root: Path
    in_backup: bool  # the backup holds something for this folder
    # The folder is a whole share (a plugin's share_folders): its contents are moved
    # aside one by one, since a share itself can't be renamed.
    whole_share: bool = False


@dataclass
class Plan:
    run_id: int
    archive: Path
    manifest: dict[str, Any]
    app: str
    app_key: str
    plugin: Manifest
    folders: list[FolderPlan]


@dataclass
class Progress:
    restore_id: int
    app: str
    phase: str = "checking"
    started: float = field(default_factory=time.monotonic)


# --- planning -------------------------------------------------------------------------


def _app_record(manifest: dict[str, Any], app: str) -> dict[str, Any]:
    for record in manifest.get("apps", []):
        if str(record.get("name", "")).lower() == app.lower():
            return dict(record)
    raise RestoreError(f"{app} isn't in this backup")


def _folders_from_template(
    archive: Path, manifest: dict[str, Any], key: str, settings: Settings
) -> list[dict[str, str]]:
    """Older backups don't list their folders: read them from the template in the
    backup (the container's paths as they were when it was made)."""
    name = f"apps/{key}/template.xml"
    for info, data in unpack.members(archive):
        if info.name == name and data is not None:
            template = discovery.parse_template(data.read(1 << 20).decode("utf-8", "replace"))
            shares = discovery.mounted_data_shares(settings.data_root)
            app = discovery.discover_app(template, shares)
            return [
                {"key": folder_key(f.target), "target": f.target, "host": f.host}
                for f in app.folders
            ]
    return []


def plan(db: Database, settings: Settings, registry: Registry, run_id: int, app: str) -> Plan:
    with db.read() as session:
        run = session.get(Run, run_id)
    if run is None or run.archive is None or run.deleted_at is not None or not is_ours(run.archive):
        raise RestoreError("that backup isn't available")
    archive = settings.backups_dir / run.archive
    if not archive.is_file() or archive.is_symlink():
        raise RestoreError("the backup file is missing from the backups folder")
    manifest = unpack.read_manifest(archive)
    record = _app_record(manifest, app)
    key = str(record.get("key") or "")
    if not key or record.get("status") == "skipped":
        raise RestoreError(f"{app} has nothing in this backup")
    plugin = registry.get(str(record.get("plugin")))
    if plugin is None:
        raise RestoreError(
            f"the {record.get('plugin')} plugin that made this backup isn't installed"
        )
    folders = record.get("folders") or _folders_from_template(archive, manifest, key, settings)
    if not folders:
        raise RestoreError(f"the backup doesn't say where {app}'s folders go")
    stored = {e["path"] for e in manifest.get("entries", [])}
    plans: list[FolderPlan] = []
    for folder in folders:
        host = str(folder["host"])
        try:
            share = PurePosixPath(host).relative_to("/mnt/user").parts[0]
        except (ValueError, IndexError):
            raise RestoreError(f"{host} isn't on a data share") from None
        share_root = settings.data_root / share
        if not share_root.is_dir():
            raise RestoreError(
                f"{host} is on the '{share}' share, which BackupHaven can't see: add it first"
            )
        try:
            path = resolve_app_folder(settings.data_root, host)
        except (OutsideShareError, ValueError) as exc:
            raise RestoreError(str(exc)) from None
        whole_share = path == share_root.resolve()
        if whole_share and str(folder.get("target")) not in _share_folders(registry, record):
            raise RestoreError(f"{host} is a whole share; BackupHaven won't replace it")
        prefixes = (f"apps/{key}/files/{folder['key']}/", f"apps/{key}/databases/{folder['key']}/")
        plans.append(
            FolderPlan(
                key=str(folder["key"]),
                target=str(folder["target"]),
                host=host,
                path=path,
                share_root=share_root,
                in_backup=any(name.startswith(prefixes) for name in stored),
                whole_share=whole_share,
            )
        )
    return Plan(
        run_id=run_id,
        archive=archive,
        manifest=manifest,
        app=str(record["name"]),
        app_key=key,
        plugin=plugin,
        folders=plans,
    )


def _share_folders(registry: Registry, record: dict[str, Any]) -> list[str]:
    plugin = registry.plugins.get(str(record.get("plugin")))
    return list(plugin.backup.share_folders) if plugin is not None else []


# --- is the app stopped? ------------------------------------------------------------------


def _fingerprint(root: Path, limit: int = 200_000) -> dict[str, tuple[int, int]]:
    seen: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ASIDE_DIR]
        for name in filenames:
            full = os.path.join(dirpath, name)  # noqa: PTH118
            with suppress(OSError):
                info = os.lstat(full)
                seen[full] = (info.st_size, info.st_mtime_ns)
            if len(seen) >= limit:
                return seen
    return seen


def changing_files(
    roots: list[Path], seconds: float = QUIET_SECONDS, sleep: Callable[[float], None] = time.sleep
) -> list[str]:
    """Files under ``roots`` that changed during ``seconds`` (a running app writes)."""
    before = {str(r): _fingerprint(r) for r in roots if r.is_dir()}
    sleep(seconds)
    changed: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        after = _fingerprint(root)
        old = before.get(str(root), {})
        changed += sorted(p for p in after.keys() | old.keys() if after.get(p) != old.get(p))
    return changed


# --- moving back what the backup left out --------------------------------------------------


def _is_sidecar(rel: str) -> bool:
    return rel.endswith(SIDECARS)


def left_out(aside: Path, rules: Rules, restored: Path) -> Iterator[str]:
    """Paths (relative, "/"-separated) in the set-aside folder that the backup didn't
    take on purpose and that aren't in the restored folder: whole folders where
    possible. Database side files are never included."""
    wanted = None if rules.databases == "auto" else (*rules.include, *rules.databases)
    for dirpath, dirnames, filenames in os.walk(aside, followlinks=False):
        rel_dir = os.path.relpath(dirpath, aside).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep_walking = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            whole = _dir_excluded(rel, rules.exclude) or (
                wanted is not None and not could_contain(rel, wanted)
            )
            if (aside / rel).is_symlink():
                whole = whole or not (restored / rel).exists()
            if whole and not (restored / rel).exists() and not (restored / rel).is_symlink():
                yield rel
            elif not (aside / rel).is_symlink():
                keep_walking.append(name)
        dirnames[:] = keep_walking
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _is_sidecar(rel) or (restored / rel).exists() or (restored / rel).is_symlink():
                continue
            taken = not matches(rel, rules.exclude) and matches(rel, rules.include)
            if not taken:
                yield rel


def _move(src_root: Path, dest_root: Path, rel: str) -> None:
    """Rename ``src_root/rel`` to ``dest_root/rel`` (same share). Missing parent
    folders are created with the owner and mode of their twins under ``src_root``.
    Neither side's parents may be links."""
    parts = PurePosixPath(rel).parts
    for depth in range(1, len(parts)):
        sub = Path(*parts[:depth])
        for root in (src_root, dest_root):
            if (root / sub).is_symlink():
                raise RestoreError(f"{root / sub} is a link; not moving through it")
        target = dest_root / sub
        if not target.exists():
            target.mkdir()
            with suppress(OSError):
                info = (src_root / sub).lstat()
                target.chmod(stat.S_IMODE(info.st_mode))
                if os.geteuid() == 0:
                    os.chown(target, info.st_uid, info.st_gid)
    (src_root / rel).rename(dest_root / rel)


# --- the restore itself ----------------------------------------------------------------------


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H%M%S")


class Restorer:
    """Runs one restore or undo at a time, never during a backup (shares the
    runner's lock)."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        registry: Registry,
        lock: threading.Lock,
        quiet_seconds: float = QUIET_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._db = db
        self._settings = settings
        self._registry = registry
        self._lock = lock
        self._quiet = quiet_seconds
        self.progress: Progress | None = None

    # -- checks before starting --

    def check(self, run_id: int, app: str) -> dict[str, Any]:
        """What a restore would do, and whether the app looks stopped."""
        p = plan(self._db, self._settings, self._registry, run_id, app)
        changed = changing_files([f.path for f in p.folders], self._quiet, self._sleep)
        return {
            "app": p.app,
            "folders": [
                {
                    "target": f.target,
                    "host": f.host,
                    "exists": f.path.exists(),
                    "in_backup": f.in_backup,
                }
                for f in p.folders
            ],
            "changing": changed[:20],
            "stopped": not changed,
        }

    def start(self, run_id: int, app: str, actor: str) -> int:
        """Start a restore in the background. ``RestoreError`` if it can't start."""
        p = plan(self._db, self._settings, self._registry, run_id, app)
        if not self._lock.acquire(blocking=False):
            raise RestoreError("a backup or another restore is running")
        try:
            with self._db.write() as session:
                row = Restore(run_id=run_id, app=p.app, status="running", actor=actor)
                session.add(row)
                session.flush()
                restore_id = row.id
        except Exception:
            self._lock.release()
            raise
        self.progress = Progress(restore_id=restore_id, app=p.app)
        threading.Thread(
            target=self._run_and_release, args=(restore_id, p), name="restore", daemon=True
        ).start()
        return restore_id

    def run(self, run_id: int, app: str, actor: str = "test") -> int:
        """Restore in this thread (tests)."""
        p = plan(self._db, self._settings, self._registry, run_id, app)
        with self._lock:
            with self._db.write() as session:
                row = Restore(run_id=run_id, app=p.app, status="running", actor=actor)
                session.add(row)
                session.flush()
                restore_id = row.id
            self.progress = Progress(restore_id=restore_id, app=p.app)
            self._restore(restore_id, p)
        return restore_id

    def _run_and_release(self, restore_id: int, p: Plan) -> None:
        try:
            self._restore(restore_id, p)
        finally:
            self._lock.release()

    def _phase(self, phase: str) -> None:
        if self.progress is not None:
            self.progress.phase = phase

    def _restore(self, restore_id: int, p: Plan) -> None:
        now = datetime.now(UTC)
        rules = effective_rules(p.plugin)
        done: list[dict[str, Any]] = []
        error: str | None = None
        try:
            self._phase("checking")
            changed = changing_files([f.path for f in p.folders], self._quiet, self._sleep)
            if changed:
                raise RestoreError(
                    f"{p.app}'s files are still changing ({changed[0]}): stop its container first"
                )
            for folder in p.folders:
                if not folder.in_backup:
                    continue  # nothing stored for it: leave it as it is
                aside = folder.share_root / ASIDE_DIR / f"{p.app_key}-{_stamp(now)}" / folder.key
                record: dict[str, Any] = {
                    "host": folder.host,
                    "path": str(folder.path),
                    "aside": None,
                    "moved_back": [],
                    "whole_share": folder.whole_share,
                }
                done.append(record)
                self._phase("moving the current files aside")
                old_info = None
                reserved: frozenset[str] = frozenset()
                if folder.whole_share:
                    reserved = frozenset({ASIDE_DIR})
                    aside.mkdir(parents=True)
                    record["aside"] = str(aside)
                    for child in sorted(folder.path.iterdir()):
                        if child.name != ASIDE_DIR:
                            child.rename(aside / child.name)
                elif folder.path.exists() or folder.path.is_symlink():
                    if folder.path.is_symlink() or not folder.path.is_dir():
                        raise RestoreError(f"{folder.host} isn't a plain folder")
                    old_info = folder.path.stat()
                    aside.parent.mkdir(parents=True, exist_ok=True)
                    folder.path.rename(aside)
                    record["aside"] = str(aside)
                if not folder.whole_share:
                    folder.path.mkdir(parents=True)
                if old_info is not None:
                    folder.path.chmod(stat.S_IMODE(old_info.st_mode))
                    if os.geteuid() == 0:
                        os.chown(folder.path, old_info.st_uid, old_info.st_gid)
                self._phase("unpacking the backup")
                result = unpack.extract(
                    p.archive,
                    [
                        unpack.Part(f"apps/{p.app_key}/files/{folder.key}/", folder.path, reserved),
                        unpack.Part(
                            f"apps/{p.app_key}/databases/{folder.key}/", folder.path, reserved
                        ),
                    ],
                    p.manifest,
                )
                record.update(files=result.files, links=result.links, bytes=result.bytes)
                if record["aside"] is not None:
                    self._phase("moving back what the backup left out")
                    for rel in list(left_out(aside, rules, folder.path)):
                        _move(aside, folder.path, rel)
                        record["moved_back"].append(rel)
                if old_info is not None:
                    os.utime(folder.path, ns=(old_info.st_atime_ns, old_info.st_mtime_ns))
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            logger.warning("restore failed; putting the app's files back", exc_info=True)
            self._phase("putting the app's files back")
            for record in reversed(done):
                try:
                    self._put_back(record)
                except Exception:
                    logger.exception("couldn't put back", extra={"folder": record["host"]})
                    error += (
                        f"; couldn't put back {record['host']}: its files are in {record['aside']}"
                    )
        finally:
            with self._db.write() as session:
                row = session.get(Restore, restore_id)
                if row is not None:
                    row.status = "failed" if error else "done"
                    row.error = error
                    row.finished_at = utcnow()
                    row.undo_until = None if error else utcnow() + timedelta(days=UNDO_DAYS)
                    row.detail = {"folders": done, "archive": p.archive.name}
            logger.info(
                "restore finished",
                extra={"restore_id": restore_id, "app": p.app, "error": error},
            )
            self.progress = None

    def _put_back(self, record: dict[str, Any]) -> None:
        """Remove the restored folder and rename the set-aside one back."""
        path = Path(record["path"])
        aside = Path(record["aside"]) if record.get("aside") else None
        for rel in reversed(record.get("moved_back", [])):
            if aside is not None and ((path / rel).exists() or (path / rel).is_symlink()):
                _move(path, aside, rel)
        if record.get("whole_share"):
            # Empty the share (apart from set-aside folders), then move its old
            # contents back one by one.
            for child in path.iterdir():
                if child.name == ASIDE_DIR:
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            if aside is not None and aside.is_dir():
                for child in sorted(aside.iterdir()):
                    child.rename(path / child.name)
                aside.rmdir()
                with suppress(OSError):
                    aside.parent.rmdir()
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        if aside is not None and aside.exists():
            aside.rename(path)
            with suppress(OSError):
                aside.parent.rmdir()  # the <app>-<time> folder, if now empty

    # -- undo and expiry --

    def undo(self, restore_id: int) -> None:
        with self._db.read() as session:
            row = session.get(Restore, restore_id)
            detail = dict(row.detail or {}) if row is not None else {}
            status_ = row.status if row is not None else None
            until = row.undo_until if row is not None else None
        if status_ != "done" or until is None or until < datetime.now(UTC):
            raise RestoreError("this restore can't be undone any more")
        folders = detail.get("folders", [])
        paths = [Path(f["path"]) for f in folders]
        if not self._lock.acquire(blocking=False):
            raise RestoreError("a backup or another restore is running")
        try:
            changed = changing_files(paths, self._quiet, self._sleep)
            if changed:
                raise RestoreError(
                    f"files are still changing ({changed[0]}): stop the container first"
                )
            for record in reversed(folders):
                self._put_back(record)
            with self._db.write() as session:
                row = session.get(Restore, restore_id)
                if row is not None:
                    row.status, row.undo_until = "undone", None
        finally:
            self._lock.release()

    def expire(self, now: datetime | None = None) -> list[int]:
        """Delete set-aside folders whose undo time has run out."""
        now = now or datetime.now(UTC)
        expired: list[int] = []
        with self._db.write() as session:
            rows = session.query(Restore).filter(
                Restore.status == "done", Restore.undo_until.is_not(None)
            )
            for row in rows:
                if row.undo_until is None or row.undo_until > now:
                    continue
                for record in (row.detail or {}).get("folders", []):
                    aside = record.get("aside")
                    if aside and ASIDE_DIR in Path(aside).parts and not Path(aside).is_symlink():
                        shutil.rmtree(aside, ignore_errors=True)
                        with suppress(OSError):
                            Path(aside).parent.rmdir()
                row.status, row.undo_until = "expired", None
                expired.append(row.id)
        return expired
