"""The Backups page: every backup file, why it's kept, delete by hand (§9.2)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from backuphaven import audit, retention
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.db import DbCheck, Run
from backuphaven.runner import Runner

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])

# Reasons that make a backup impossible to delete by hand.
PROTECTED = ("newest good backup", "last good copy of ")


class BackupView(BaseModel):
    run_id: int
    archive: str
    started_at: datetime
    trigger: str
    status: str
    size: int | None
    verified: bool
    summary: dict[str, Any] | None
    databases: int
    healthy: int
    damaged: int
    keep_reasons: list[str]
    protected: bool
    on_disk: bool


def _protected(reasons: list[str]) -> bool:
    return any(r.startswith(PROTECTED) for r in reasons)


@router.get("/backups")
def list_backups(request: Request, db: DbDep, _principal: InteractiveDep) -> list[BackupView]:
    settings: Settings = request.app.state.settings
    reasons = retention.reasons(db, settings.tz)
    with db.read() as session:
        runs = session.scalars(
            select(Run)
            .where(Run.archive.is_not(None), Run.deleted_at.is_(None))
            .order_by(Run.started_at.desc())
        ).all()
        checks: dict[int, list[str]] = {}
        for check in session.scalars(
            select(DbCheck).where(DbCheck.run_id.in_([r.id for r in runs]))
        ):
            checks.setdefault(check.run_id, []).append(check.status)
    return [
        BackupView(
            run_id=run.id,
            archive=str(run.archive),
            started_at=run.started_at,
            trigger=run.trigger,
            status=run.status,
            size=run.size,
            verified=run.verified,
            summary=run.summary,
            databases=len(checks.get(run.id, [])),
            healthy=checks.get(run.id, []).count("ok"),
            damaged=checks.get(run.id, []).count("damaged"),
            keep_reasons=reasons.get(run.id, []),
            protected=_protected(reasons.get(run.id, [])),
            on_disk=retention.is_ours(str(run.archive))
            and (settings.backups_dir / str(run.archive)).is_file(),
        )
        for run in runs
    ]


@router.delete("/backups/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(run_id: int, request: Request, db: DbDep, principal: InteractiveDep) -> None:
    settings: Settings = request.app.state.settings
    runner: Runner = request.app.state.runner
    if runner.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "run_in_progress")
    reasons = retention.reasons(db, settings.tz)
    if run_id not in reasons:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup_not_found")
    if _protected(reasons[run_id]):
        raise HTTPException(status.HTTP_409_CONFLICT, "backup_protected")
    with db.write() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "backup_not_found")
        try:
            retention.delete_backup(settings.backups_dir, run)
        except (OSError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        audit.record(session, principal.actor, "backup.deleted", str(run.archive))
