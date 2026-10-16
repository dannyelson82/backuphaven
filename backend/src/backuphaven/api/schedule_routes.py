"""Settings → Schedule: when scheduled backups run (ARCHITECTURE.md §6.11)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backuphaven import audit, schedule
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.scheduler import Scheduler

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class MissedView(BaseModel):
    due: datetime
    reason: str


class ScheduleView(schedule.ScheduleSettings):
    timezone: str
    next_run: datetime | None
    apps: list[str]
    missed: list[MissedView]


def _view(request: Request, db: DbDep) -> ScheduleView:
    scheduler: Scheduler = request.app.state.scheduler
    with db.read() as session:
        plan = schedule.load_settings(session)
        apps = schedule.load_apps(session)
    return ScheduleView(
        **plan.model_dump(),
        timezone=scheduler.timezone,
        next_run=scheduler.next_run(),
        apps=apps,
        missed=[MissedView(due=m.due, reason=m.reason) for m in scheduler.missed()],
    )


@router.get("/settings/schedule")
def get_schedule(request: Request, db: DbDep, _principal: InteractiveDep) -> ScheduleView:
    return _view(request, db)


@router.put("/settings/schedule")
def put_schedule(
    body: schedule.ScheduleSettings, request: Request, db: DbDep, principal: InteractiveDep
) -> ScheduleView:
    with db.write() as session:
        before = schedule.load_settings(session)
        schedule.save_settings(session, body)
        if before != body:
            audit.record(session, principal.actor, "schedule.updated", None, body.model_dump())
    if before != body:
        request.app.state.scheduler.reset()
    return _view(request, db)
