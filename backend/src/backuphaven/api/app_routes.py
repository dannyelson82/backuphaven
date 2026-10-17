"""Apps found from the Unraid templates (ARCHITECTURE.md §6.1)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backuphaven import app_modes, audit, discovery, external, schedule
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.config import Settings
from backuphaven.db import Run, RunApp
from backuphaven.docker_proxy import DockerProxy, ProxyError
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
    # None: no plugin yet, so the app can't be backed up (ADR-0013).
    plugin: str | None
    plugin_name: str | None = None
    plugin_local: bool = False
    # The plugin's databases in other containers (ids), if any (ADR-0014).
    external_databases: list[str] = []
    # Through the socket proxy: running, stopped, or missing (no such container);
    # None without the proxy.
    container: str | None = None
    # "snapshot" or "stop": the owner's choice, or the plugin's default.
    mode: str | None = None
    mode_chosen: bool = False
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
    known: registry.PluginSource = request.app.state.plugins
    with db.read() as session:
        scheduled = schedule.load_apps(session)
    in_schedule = {n.lower() for n in scheduled}
    last = _last_backups(db)
    containers = _containers(request)
    with db.read() as session:
        modes = app_modes.load(session)
    pairs = []
    for found in apps:
        plugin = known.match(found.image)
        app = discovery.with_plugin_shares(found, plugin.backup.share_folders) if plugin else found
        pairs.append((app, plugin))
    return DiscoveryView(
        data_shares=sorted(discovery.mounted_data_shares(settings.data_root)),
        templates_found=settings.templates_dir.is_dir(),
        scheduled=scheduled,
        apps=[
            AppView(
                name=app.name,
                image=app.image,
                plugin=plugin.id if plugin else None,
                plugin_name=plugin.name if plugin else None,
                plugin_local=plugin is not None and plugin.id in known.local,
                external_databases=[d.id for d in plugin.backup.external_databases]
                if plugin
                else [],
                folders=[DataFolderView(**vars(f)) for f in app.folders],
                missing=[DataFolderView(**vars(m)) for m in app.missing],
                scheduled=app.name.lower() in in_schedule,
                not_installed=not_installed(
                    settings.data_root, app, None if containers is None else set(containers)
                ),
                container=None
                if containers is None
                else containers.get(app.name.lower(), "missing"),
                mode=app_modes.effective(modes, app.name, plugin.backup.mode) if plugin else None,
                mode_chosen=app.name.lower() in modes,
                last_backup=last.get(app.name.lower()),
            )
            for app, plugin in pairs
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
    """Replace the list. Apps being added must exist and have a plugin (ADR-0013);
    names already in the list may stay even if their template is gone (they show as
    skipped in runs)."""
    settings: Settings = request.app.state.settings
    known: registry.PluginSource = request.app.state.plugins
    apps = {
        a.name.lower(): a for a in discovery.discover(settings.templates_dir, settings.data_root)
    }
    found = set(apps)
    with db.write() as session:
        before = schedule.load_apps(session)
        kept = {n.lower() for n in before}
        unknown = [n for n in body.apps if n.lower() not in found and n.lower() not in kept]
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "not found: " + ", ".join(unknown)
            )
        no_plugin = [
            n
            for n in body.apps
            if n.lower() not in kept
            and n.lower() in apps
            and known.match(apps[n.lower()].image) is None
        ]
        if no_plugin:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "no plugin yet: " + ", ".join(no_plugin)
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
    local: bool = False  # from the local plugins folder, not part of a release
    id: str
    name: str
    version: str
    description: str
    mode: str
    settings: list[SettingView]


@router.get("/plugins")
def plugins(request: Request, _principal: InteractiveDep) -> list[PluginView]:
    known: registry.PluginSource = request.app.state.plugins
    return [
        PluginView(
            id=m.id,
            name=m.name,
            version=m.version,
            description=m.description,
            mode=m.backup.mode,
            local=m.id in known.local,
            settings=[
                SettingView(id=s.id, label=s.label, help=s.help, default=s.default)
                for s in m.settings
            ],
        )
        for m in known.plugins.values()
    ]


@router.get("/apps/{name}/plugin-brief")
def plugin_brief(name: str, request: Request, db: DbDep, principal: InteractiveDep) -> Response:
    """The plugin development file for one app (ADR-0013): Markdown for an AI coder."""
    from backuphaven.plugins import brief

    settings: Settings = request.app.state.settings
    known: registry.PluginSource = request.app.state.plugins
    found = {
        a.name.lower(): a for a in discovery.discover(settings.templates_dir, settings.data_root)
    }
    app = found.get(name.lower())
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app_not_found")
    plugin = known.match(app.image)
    if plugin is not None:
        app = discovery.with_plugin_shares(app, plugin.backup.share_folders)
    text = brief.build(app, plugin, settings.data_root)
    with db.write() as session:
        audit.record(session, principal.actor, "plugin.brief", app.name)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in app.name.lower())[:60]
    return Response(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="backuphaven-plugin-{safe}.md"'},
    )


# --- an app's databases in other containers (ADR-0014) -----------------------------------


class ExternalDatabaseView(BaseModel):
    id: str
    engine: str
    host: str | None
    port: int | None
    database: str | None
    user: str | None
    from_template: bool  # False: the owner set the address here
    problem: str | None  # e.g. a template variable that isn't set


class AddressInput(BaseModel):
    host: str
    port: int


def _app_and_plugin(request: Request, name: str) -> tuple[discovery.App, Any]:
    settings: Settings = request.app.state.settings
    known: registry.PluginSource = request.app.state.plugins
    found = {
        a.name.lower(): a for a in discovery.discover(settings.templates_dir, settings.data_root)
    }
    app = found.get(name.lower())
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app_not_found")
    return app, known.match(app.image)


@router.get("/apps/{name}/databases")
def app_databases(
    name: str, request: Request, db: DbDep, _principal: InteractiveDep
) -> list[ExternalDatabaseView]:
    """Where BackupHaven reaches the app's databases (never the password)."""
    app, plugin = _app_and_plugin(request, name)
    if plugin is None:
        return []
    with db.read() as session:
        overrides = external.load_overrides(session)
    views = []
    for spec in plugin.backup.external_databases:
        try:
            resolved = external.resolve(app, spec, overrides)
        except ValueError as exc:
            views.append(
                ExternalDatabaseView(
                    id=spec.id,
                    engine=spec.engine,
                    host=None,
                    port=None,
                    database=None,
                    user=None,
                    from_template=True,
                    problem=str(exc),
                )
            )
            continue
        t = resolved.target
        views.append(
            ExternalDatabaseView(
                id=spec.id,
                engine=spec.engine,
                host=t.host,
                port=t.port,
                database=t.database,
                user=t.user,
                from_template=resolved.from_template,
                problem=None,
            )
        )
    return views


@router.put("/apps/{name}/databases/{db_id}")
def set_database_address(
    name: str,
    db_id: str,
    body: AddressInput,
    request: Request,
    db: DbDep,
    principal: InteractiveDep,
) -> list[ExternalDatabaseView]:
    app, plugin = _app_and_plugin(request, name)
    if plugin is None or db_id not in {s.id for s in plugin.backup.external_databases}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "database_not_found")
    try:
        address = external.Address(host=body.host, port=body.port)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with db.write() as session:
        external.save_override(session, app.name, db_id, address)
        audit.record(
            session, principal.actor, "app.database_address", app.name, address.model_dump()
        )
    return app_databases(name, request, db, principal)


@router.delete("/apps/{name}/databases/{db_id}")
def clear_database_address(
    name: str, db_id: str, request: Request, db: DbDep, principal: InteractiveDep
) -> list[ExternalDatabaseView]:
    app, _plugin = _app_and_plugin(request, name)
    with db.write() as session:
        external.save_override(session, app.name, db_id, None)
        audit.record(session, principal.actor, "app.database_address", app.name, {"cleared": db_id})
    return app_databases(name, request, db, principal)


def _containers(request: Request) -> dict[str, str] | None:
    """Container name (lower case) -> "running"/"stopped", or None without a proxy."""
    proxy: DockerProxy | None = request.app.state.proxy
    if proxy is None:
        return None
    try:
        return {c.name.lower(): "running" if c.running else "stopped" for c in proxy.containers()}
    except ProxyError:
        return None


class DockerStatusView(BaseModel):
    configured: bool
    address: str | None
    ok: bool
    error: str | None
    containers: int


@router.get("/settings/docker")
def docker_status(request: Request, _principal: InteractiveDep) -> DockerStatusView:
    """Whether the socket proxy is set up and answering (ADR-0005)."""
    settings: Settings = request.app.state.settings
    proxy: DockerProxy | None = request.app.state.proxy
    if proxy is None:
        return DockerStatusView(configured=False, address=None, ok=False, error=None, containers=0)
    try:
        count = len(proxy.containers())
    except ProxyError as exc:
        return DockerStatusView(
            configured=True, address=settings.docker_proxy, ok=False, error=str(exc), containers=0
        )
    return DockerStatusView(
        configured=True, address=settings.docker_proxy, ok=True, error=None, containers=count
    )


class ModeInput(BaseModel):
    # None: back to the plugin's default.
    mode: app_modes.Mode | None


@router.put("/apps/{name}/mode")
def set_mode(
    name: str, body: ModeInput, request: Request, db: DbDep, principal: InteractiveDep
) -> dict[str, str | None]:
    """Stop during backup, or back up while running (ADR-0005)."""
    app, plugin = _app_and_plugin(request, name)
    if plugin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no plugin yet")
    with db.write() as session:
        app_modes.save(session, app.name, body.mode)
        audit.record(session, principal.actor, "app.mode", app.name, {"mode": body.mode})
        mode = app_modes.effective(app_modes.load(session), app.name, plugin.backup.mode)
    return {"mode": mode}
