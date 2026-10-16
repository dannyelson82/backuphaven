"""Run now, run history and the status overview (ARCHITECTURE.md §9)."""

import shutil
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from backuphaven import audit, discovery
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.db import DbCheck, Run, RunApp
from backuphaven.runner import Runner

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


class StatusView(BaseModel):
    running: ProgressView | None
    last_run: RunView | None
    last_good_run: RunView | None
    apps: int
    data_shares: list[str]
    templates_found: bool
    missing_shares: list[MissingShareView]
    backups_free_bytes: int | None
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
    )


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def run_now(request: Request, db: DbDep, principal: InteractiveDep) -> dict[str, int]:
    run_id = _runner(request).start("manual")
    if run_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "run_in_progress")
    with db.write() as session:
        audit.record(session, principal.actor, "run.started", str(run_id))
    return {"run_id": run_id}


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
    progress = _runner(request).progress
    apps = discovery.discover(settings.templates_dir, settings.data_root)
    with db.read() as session:
        last = session.scalars(
            select(Run).where(Run.status != "running").order_by(Run.id.desc()).limit(1)
        ).first()
        good = session.scalars(
            select(Run).where(Run.verified.is_(True)).order_by(Run.id.desc()).limit(1)
        ).first()
        checks = (
            session.scalars(
                select(DbCheck).where(DbCheck.run_id == last.id).order_by(DbCheck.id)
            ).all()
            if last is not None
            else []
        )
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
        last_run=None if last is None else _view(last),
        last_good_run=None if good is None else _view(good),
        apps=len(apps),
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        missing_shares=[
            MissingShareView(app=a.name, target=m.target, share=m.share)
            for a in apps
            for m in a.missing
        ],
        backups_free_bytes=free,
        database_health=[_db_view(c) for c in checks],
    )
