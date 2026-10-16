"""Apps found from the Unraid templates (ARCHITECTURE.md §6.1)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backuphaven import audit, discovery, schedule
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.db import Run, RunApp
from backuphaven.plugins import registry
from backuphaven.runner import not_installed

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class DataFolderView(BaseModel):
    target: str
    host: str
    share: str


class LastBackupView(BaseModel):
    run_id: int
    started_at: datetime
    status: str
    bytes: int


class AppView(BaseModel):
    name: str
    image: str
    plugin: str
    folders: list[DataFolderView]
    missing: list[DataFolderView]
    # In the backup schedule (ADR-0011).
    scheduled: bool = False
    # Why it looks removed ("not installed: …"), or None.
    not_installed: str | None = None
    last_backup: LastBackupView | None = None


class DiscoveryView(BaseModel):
    data_shares: list[str]
    templates_found: bool
    apps: list[AppView]
    # Names in the backup schedule, including any whose template is gone.
    scheduled: list[str] = []


def _last_backups(db: DbDep) -> dict[str, LastBackupView]:
    """Each app's most recent result (skipped ones don't count)."""
    with db.read() as session:
        newest = (
            select(RunApp.app, func.max(RunApp.run_id).label("run_id"))
            .where(RunApp.status != "skipped")
            .group_by(RunApp.app)
            .subquery()
        )
        rows = session.execute(
            select(RunApp, Run.started_at)
            .join(newest, (RunApp.app == newest.c.app) & (RunApp.run_id == newest.c.run_id))
            .join(Run, Run.id == RunApp.run_id)
        ).all()
    return {
        row.app.lower(): LastBackupView(
            run_id=row.run_id, started_at=started, status=row.status, bytes=row.bytes
        )
        for row, started in rows
    }


@router.get("/apps/discovered")
def discovered(request: Request, db: DbDep, _principal: InteractiveDep) -> DiscoveryView:
    settings: Settings = request.app.state.settings
    apps = discovery.discover(settings.templates_dir, settings.data_root)
    known = registry.bundled()
    with db.read() as session:
        scheduled = schedule.load_apps(session)
    in_schedule = {n.lower() for n in scheduled}
    last = _last_backups(db)
    return DiscoveryView(
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        scheduled=scheduled,
        apps=[
            AppView(
                name=app.name,
                image=app.image,
                plugin=known.match(app.image).id,
                folders=[DataFolderView(**vars(f)) for f in app.folders],
                missing=[DataFolderView(**vars(m)) for m in app.missing],
                scheduled=app.name.lower() in in_schedule,
                not_installed=not_installed(settings.data_root, app),
                last_backup=last.get(app.name.lower()),
            )
            for app in apps
        ],
    )


class ScheduleApps(BaseModel):
    apps: list[str] = Field(max_length=1000)


@router.get("/schedule/apps")
def get_schedule_apps(db: DbDep, _principal: InteractiveDep) -> ScheduleApps:
    with db.read() as session:
        return ScheduleApps(apps=schedule.load_apps(session))


@router.put("/schedule/apps")
def put_schedule_apps(
    body: ScheduleApps, request: Request, db: DbDep, principal: InteractiveDep
) -> ScheduleApps:
    """Replace the list. Apps being added must exist; names already in the list may
    stay even if their template is gone (they show as skipped in runs)."""
    settings: Settings = request.app.state.settings
    found = {a.name.lower() for a in discovery.discover(settings.templates_dir, settings.data_root)}
    with db.write() as session:
        before = schedule.load_apps(session)
        kept = {n.lower() for n in before}
        unknown = [n for n in body.apps if n.lower() not in found and n.lower() not in kept]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "not found: " + ", ".join(unknown)
            )
        saved = schedule.save_apps(session, body.apps)
        added = sorted(set(saved) - set(before), key=str.lower)
        removed = sorted(set(before) - set(saved), key=str.lower)
        if added or removed:
            audit.record(
                session,
                principal.actor,
                "schedule.apps",
                None,
                {"added": added, "removed": removed},
            )
    return ScheduleApps(apps=saved)


class SettingView(BaseModel):
    id: str
    label: str
    help: str
    default: bool


class PluginView(BaseModel):
    id: str
    name: str
    version: str
    description: str
    mode: str
    settings: list[SettingView]


@router.get("/plugins")
def plugins(_principal: InteractiveDep) -> list[PluginView]:
    return [
        PluginView(
            id=m.id,
            name=m.name,
            version=m.version,
            description=m.description,
            mode=m.backup.mode,
            settings=[
                SettingView(id=s.id, label=s.label, help=s.help, default=s.default)
                for s in m.settings
            ],
        )
        for m in registry.bundled().plugins.values()
    ]
