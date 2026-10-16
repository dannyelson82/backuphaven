"""Run now, run history and the status overview (ARCHITECTURE.md §9)."""

import shutil
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backuphaven import audit, discovery, schedule
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.db import DbCheck, Run, RunApp
from backuphaven.runner import NoSuchAppsError, Runner
from backuphaven.scheduler import Scheduler

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class RunView(BaseModel):
    id: int
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    archive: str | None
    size: int | None
    verified: bool
    error: str | None
    summary: dict[str, Any] | None


class RunRequest(BaseModel):
    # Back up only these apps (by name); left out, every app is backed up.
    apps: list[str] | None = Field(default=None, max_length=500)


class RunAppView(BaseModel):
    app: str
    plugin: str
    status: str
    files: int
    bytes: int
    seconds: float
    errors: list[str]
    warnings: list[str]


class DbCheckView(BaseModel):
    app: str
    path: str
    status: str
    mode: str | None
    size: int
    messages: list[str]
    unknown_collations: list[str]
    skipped_tables: list[str] = []
    seconds: float = 0.0
    # How the copy was made and where the time went: copy_method, copy_seconds,
    # check_seconds, wal_bytes.
    timing: dict[str, Any] = {}


class RunDetail(RunView):
    apps: list[RunAppView]
    databases: list[DbCheckView]


class ProgressView(BaseModel):
    run_id: int
    seconds: float
    app: str | None
    apps_done: int
    apps_total: int
    files: int
    bytes: int
    phase: str


class MissingShareView(BaseModel):
    app: str
    target: str
    share: str


class MissedRunView(BaseModel):
    due: datetime
    reason: str


class StatusView(BaseModel):
    running: ProgressView | None
    scheduled_apps: list[str] = []
    next_run: datetime | None = None
    # Scheduled backups missed since the last backup started.
    missed: list[MissedRunView] = []
    last_run: RunView | None
    last_good_run: RunView | None
    apps: int
    data_shares: list[str]
    templates_found: bool
    missing_shares: list[MissingShareView]
    backups_free_bytes: int | None
    # Backup files kept, and their total size.
    backups_count: int = 0
    backups_bytes: int = 0
    # The latest check of each database of the apps in the backup schedule.
    database_health: list[DbCheckView]


def _runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


def _view(run: Run) -> RunView:
    return RunView.model_validate(run, from_attributes=True)


def _db_view(row: DbCheck) -> DbCheckView:
    extra = row.messages or {}
    return DbCheckView(
        app=row.app,
        path=row.path,
        status=row.status,
        mode=row.mode,
        size=row.size,
        messages=list(extra.get("messages", [])),
        unknown_collations=list(extra.get("unknown_collations", [])),
        skipped_tables=list(extra.get("skipped_tables", [])),
        seconds=row.seconds,
        timing=dict(extra.get("timing", {})),
    )


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def run_now(
    request: Request, db: DbDep, principal: InteractiveDep, body: RunRequest | None = None
) -> dict[str, int]:
    only = body.apps if body is not None else None
    scheduled = only is None
    if scheduled:  # Run now: the apps in the backup schedule (ADR-0011)
        with db.read() as session:
            only = schedule.load_apps(session)
        if not only:
            raise HTTPException(status.HTTP_409_CONFLICT, "schedule_empty")
    try:
        run_id = _runner(request).start("manual", only, scheduled=scheduled)
    except NoSuchAppsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if run_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "run_in_progress")
    with db.write() as session:
        audit.record(
            session,
            principal.actor,
            "run.started",
            str(run_id),
            None if only is None else {"apps": only},
        )
    return {"run_id": run_id}


@router.post("/runs/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(request: Request, db: DbDep, principal: InteractiveDep) -> dict[str, bool]:
    if not _runner(request).cancel():
        raise HTTPException(status.HTTP_409_CONFLICT, "no_run_in_progress")
    with db.write() as session:
        audit.record(session, principal.actor, "run.cancelled")
    return {"cancelling": True}


@router.get("/runs")
def list_runs(db: DbDep, _principal: InteractiveDep, limit: int = 30) -> list[RunView]:
    with db.read() as session:
        runs = session.scalars(
            select(Run).order_by(Run.id.desc()).limit(max(1, min(limit, 200)))
        ).all()
    return [_view(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: DbDep, _principal: InteractiveDep) -> RunDetail:
    with db.read() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run_not_found")
        apps = session.scalars(
            select(RunApp).where(RunApp.run_id == run_id).order_by(RunApp.id)
        ).all()
        checks = session.scalars(
            select(DbCheck).where(DbCheck.run_id == run_id).order_by(DbCheck.id)
        ).all()
    return RunDetail(
        **_view(run).model_dump(),
        apps=[
            RunAppView(
                app=a.app,
                plugin=a.plugin,
                status=a.status,
                files=a.files,
                bytes=a.bytes,
                seconds=a.seconds,
                errors=list((a.problems or {}).get("errors", [])),
                warnings=list((a.problems or {}).get("warnings", [])),
            )
            for a in apps
        ],
        databases=[_db_view(c) for c in checks],
    )


@router.get("/status")
def get_status(request: Request, db: DbDep, _principal: InteractiveDep) -> StatusView:
    settings: Settings = request.app.state.settings
    scheduler: Scheduler = request.app.state.scheduler
    progress = _runner(request).progress
    apps = discovery.discover(settings.templates_dir, settings.data_root)
    with db.read() as session:
        scheduled_apps = schedule.load_apps(session)
        last = session.scalars(
            select(Run).where(Run.status != "running").order_by(Run.id.desc()).limit(1)
        ).first()
        good = session.scalars(
            select(Run).where(Run.verified.is_(True)).order_by(Run.id.desc()).limit(1)
        ).first()
        kept = session.execute(
            select(func.count(Run.id), func.coalesce(func.sum(Run.size), 0)).where(
                Run.archive.is_not(None), Run.deleted_at.is_(None)
            )
        ).one()
        wanted = {n.lower() for n in scheduled_apps}
        latest: dict[tuple[str, str], DbCheck] = {}
        for check in session.scalars(select(DbCheck).order_by(DbCheck.run_id.desc(), DbCheck.id)):
            if check.app.lower() in wanted:
                latest.setdefault((check.app.lower(), check.path), check)
        checks = sorted(latest.values(), key=lambda c: (c.app.lower(), c.path))
    try:
        free: int | None = shutil.disk_usage(settings.backups_dir).free
    except OSError:
        free = None
    return StatusView(
        running=None
        if progress is None
        else ProgressView(
            run_id=progress.run_id,
            seconds=round(time.monotonic() - progress.started, 1),
            app=progress.app,
            apps_done=progress.apps_done,
            apps_total=progress.apps_total,
            files=progress.files,
            bytes=progress.bytes,
            phase=progress.phase,
        ),
        scheduled_apps=scheduled_apps,
        next_run=scheduler.next_run(),
        missed=[
            MissedRunView(due=m.due, reason=m.reason)
            for m in scheduler.missed()
            if last is None or m.due > last.started_at
        ],
        last_run=None if last is None else _view(last),
        last_good_run=None if good is None else _view(good),
        apps=len(apps),
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        # Only apps in the backup schedule need attention here; the Apps page
        # shows the rest.
        missing_shares=[
            MissingShareView(app=a.name, target=m.target, share=m.share)
            for a in apps
            if a.name.lower() in {n.lower() for n in scheduled_apps}
            for m in a.missing
        ],
        backups_free_bytes=free,
        backups_count=int(kept[0]),
        backups_bytes=int(kept[1] or 0),
        database_health=[_db_view(c) for c in checks],
    )
