"""Applying the keep rules to the backups folder (ARCHITECTURE.md §6.8).

``keep.decide`` is the pure decision; this module gathers the facts from the
database, deletes what the rules don't keep, and stores the owner's settings
(Settings → Keeping backups). Only backup files BackupHaven made and recorded are
ever deleted; anything else in the folder is left alone.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backuphaven import settings_store
from backuphaven.db import Database, DbCheck, Run
from backuphaven.db.types import utcnow
from backuphaven.keep import Backup, KeepSettings, decide
from backuphaven.timetable import zone

logger = logging.getLogger(__name__)

SETTING_KEY = "keep"
ARCHIVE_PREFIX = "backuphaven-"
ARCHIVE_SUFFIX = ".tar.zst"
MANIFEST_SUFFIX = ".manifest.json"
GIB = 1 << 30


class KeepingSettings(BaseModel):
    """Settings → Keeping backups."""

    nightly: int = Field(default=7, ge=0, le=90)
    weekly: int = Field(default=4, ge=0, le=104)
    monthly: int = Field(default=6, ge=0, le=120)
    # A backup doesn't start, and stops, below this much free space.
    min_free_gb: float = Field(default=2, ge=0, le=100_000)

    def rules(self) -> KeepSettings:
        return KeepSettings(nightly=self.nightly, weekly=self.weekly, monthly=self.monthly)

    @property
    def min_free_bytes(self) -> int:
        return int(self.min_free_gb * GIB)


def load_settings(session: Session) -> KeepingSettings:
    try:
        return KeepingSettings.model_validate(settings_store.get(session, SETTING_KEY) or {})
    except ValidationError:
        return KeepingSettings()


def min_free_bytes(session: Session, fallback: int) -> int:
    """The owner's minimum free space, or ``fallback`` (the environment's) if unset."""
    if settings_store.get(session, SETTING_KEY) is None:
        return fallback
    return load_settings(session).min_free_bytes


def save_settings(session: Session, value: KeepingSettings) -> None:
    settings_store.put(session, SETTING_KEY, value.model_dump())


@dataclass(frozen=True)
class Kept:
    run_id: int
    name: str
    reasons: list[str]


def backups(session: Session) -> list[tuple[Run, Backup]]:
    """Every backup file still on disk (as far as BackupHaven knows), with its facts."""
    runs = session.scalars(
        select(Run).where(Run.archive.is_not(None), Run.deleted_at.is_(None))
    ).all()
    healthy: dict[int, set[str]] = {}
    for check in session.scalars(
        select(DbCheck).where(DbCheck.run_id.in_([r.id for r in runs]), DbCheck.status == "ok")
    ):
        healthy.setdefault(check.run_id, set()).add(f"{check.app}/{check.path}")
    return [
        (
            run,
            Backup(
                name=str(run.archive),
                created=run.started_at,
                good=run.verified and run.status != "failed",
                chosen=bool((run.summary or {}).get("chosen")),
                healthy=frozenset(healthy.get(run.id, set())),
            ),
        )
        for run in runs
    ]


def reasons(db: Database, tz_name: str | None, now: datetime | None = None) -> dict[int, list[str]]:
    """Each backup's reasons to be kept, by run id (empty: will be deleted)."""
    with db.read() as session:
        rules = load_settings(session).rules()
        found = backups(session)
    decided = decide([b for _, b in found], rules, zone(tz_name), now or datetime.now(UTC))
    return {run.id: decided[backup.name] for run, backup in found}


def is_ours(name: str) -> bool:
    """A plain BackupHaven backup file name, safe to join to the backups folder."""
    return (
        name.startswith(ARCHIVE_PREFIX)
        and name.endswith(ARCHIVE_SUFFIX)
        and "/" not in name
        and "\x00" not in name
        and ".." not in name
    )


def delete_backup(backups_dir: Path, run: Run) -> None:
    """Delete one backup file and its contents list, and mark the run."""
    name = str(run.archive)
    if not is_ours(name):
        raise ValueError(f"not a BackupHaven backup file: {name!r}")
    for path in (backups_dir / name, backups_dir / f"{name}{MANIFEST_SUFFIX}"):
        if path.is_symlink():
            raise ValueError(f"{path.name} is a link; left alone")
        path.unlink(missing_ok=True)
    run.deleted_at = utcnow()


def apply(
    db: Database, backups_dir: Path, tz_name: str | None, now: datetime | None = None
) -> list[str]:
    """Delete every backup the keep rules don't keep. Returns the deleted names."""
    decided = reasons(db, tz_name, now)
    deleted: list[str] = []
    with db.write() as session:
        for run_id, why in decided.items():
            run = session.get(Run, run_id)
            if why or run is None:
                continue
            try:
                delete_backup(backups_dir, run)
            except (OSError, ValueError):
                logger.warning(
                    "old backup not deleted", extra={"archive": run.archive}, exc_info=True
                )
                continue
            deleted.append(str(run.archive))
    if deleted:
        logger.info("old backups deleted by the keep rules", extra={"archives": deleted})
    return deleted
