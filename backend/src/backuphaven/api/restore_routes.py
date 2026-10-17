"""Restoring an app and undoing it (ARCHITECTURE.md §6.9)."""

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from backuphaven import audit, unpack
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.db import Restore
from backuphaven.restore import RestoreError, Restorer

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class RestoreRequest(BaseModel):
    run_id: int
    app: str
    # The owner says the container is stopped (no Docker access until stop mode).
    confirmed_stopped: bool = False


class RestoreView(BaseModel):
    id: int
    run_id: int
    app: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    undo_until: datetime | None
    error: str | None
    detail: dict[str, Any] | None
    actor: str


class RestoreProgressView(BaseModel):
    restore_id: int
    app: str
    phase: str
    seconds: float


def _restorer(request: Request) -> Restorer:
    restorer: Restorer = request.app.state.restorer
    return restorer


def _view(row: Restore) -> RestoreView:
    return RestoreView.model_validate(row, from_attributes=True)


@router.post("/restores/check")
def check_restore(
    body: RestoreRequest, request: Request, _principal: InteractiveDep
) -> dict[str, Any]:
    """What a restore would replace, and whether the app's files are still changing
    (takes a few seconds)."""
    try:
        return _restorer(request).check(body.run_id, body.app)
    except (RestoreError, unpack.UnpackError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/restores", status_code=status.HTTP_202_ACCEPTED)
def start_restore(
    body: RestoreRequest, request: Request, db: DbDep, principal: InteractiveDep
) -> dict[str, int]:
    # With the socket proxy BackupHaven stops and starts the container itself.
    if not body.confirmed_stopped and _restorer(request).container(body.app) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "stop the container first")
    try:
        restore_id = _restorer(request).start(body.run_id, body.app, principal.actor)
    except (RestoreError, unpack.UnpackError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    with db.write() as session:
        audit.record(session, principal.actor, "restore.started", body.app, {"run_id": body.run_id})
    return {"restore_id": restore_id}


@router.get("/restores")
def list_restores(db: DbDep, _principal: InteractiveDep, limit: int = 50) -> list[RestoreView]:
    with db.read() as session:
        rows = session.scalars(
            select(Restore).order_by(Restore.id.desc()).limit(max(1, min(limit, 200)))
        ).all()
    return [_view(r) for r in rows]


@router.get("/restores/current")
def current_restore(request: Request, _principal: InteractiveDep) -> RestoreProgressView | None:
    progress = _restorer(request).progress
    if progress is None:
        return None
    return RestoreProgressView(
        restore_id=progress.restore_id,
        app=progress.app,
        phase=progress.phase,
        seconds=round(time.monotonic() - progress.started, 1),
    )


@router.get("/restores/{restore_id}")
def get_restore(restore_id: int, db: DbDep, _principal: InteractiveDep) -> RestoreView:
    with db.read() as session:
        row = session.get(Restore, restore_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "restore_not_found")
    return _view(row)


@router.post("/restores/{restore_id}/undo")
def undo_restore(
    restore_id: int, request: Request, db: DbDep, principal: InteractiveDep
) -> RestoreView:
    try:
        _restorer(request).undo(restore_id)
    except RestoreError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    with db.write() as session:
        row = session.get(Restore, restore_id)
        audit.record(session, principal.actor, "restore.undone", row.app if row else None)
    return get_restore(restore_id, db, principal)
