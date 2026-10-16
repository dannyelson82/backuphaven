"""The Backups page: every backup file, why it's kept, delete by hand (§9.2)."""

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from backuphaven import audit, downloads, retention, unpack
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.archive import safe_name
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


# --- what's inside, and downloads (phase 0.3) ---------------------------------------------


class ContentAppView(BaseModel):
    key: str  # folder name inside the backup (apps/<key>/)
    name: str
    plugin: str
    status: str
    files: int
    bytes: int
    databases: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]


class ContentsView(BaseModel):
    run_id: int
    archive: str
    created: str | None
    only: list[str] | None
    apps: list[ContentAppView]


class EntryView(BaseModel):
    path: str  # inside the app: files/<folder>/…, databases/<folder>/…, template.xml
    kind: str
    size: int


def _archive_of(run_id: int, request: Request, db: DbDep) -> tuple[Path, dict[str, Any]]:
    """The backup file of a run (checked name, must exist) and its contents list."""
    settings: Settings = request.app.state.settings
    with db.read() as session:
        run = session.get(Run, run_id)
    if run is None or run.archive is None or run.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup_not_found")
    name = str(run.archive)
    if not retention.is_ours(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup_not_found")
    path = settings.backups_dir / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup_file_missing")
    try:
        manifest = unpack.read_manifest(path)
    except (OSError, ValueError, unpack.UnpackError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"contents list unreadable: {exc}") from None
    return path, manifest


def _app_keys(manifest: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for entry in manifest.get("entries", []):
        parts = entry["path"].split("/")
        if len(parts) > 2 and parts[0] == "apps" and parts[1] not in keys:
            keys.append(parts[1])
    return keys


@router.get("/backups/{run_id}/contents")
def contents(run_id: int, request: Request, db: DbDep, _principal: InteractiveDep) -> ContentsView:
    path, manifest = _archive_of(run_id, request, db)
    keys = set(_app_keys(manifest))
    apps: list[ContentAppView] = []
    for app in manifest.get("apps", []):
        # Newer backups record each app's folder; older ones used safe_name(name).
        key = app.get("key") or safe_name(app["name"])
        if key not in keys:
            key = ""  # skipped, or nothing stored
        apps.append(
            ContentAppView(
                key=key,
                name=app["name"],
                plugin=app.get("plugin", ""),
                status=app.get("status", ""),
                files=app.get("files", 0),
                bytes=app.get("bytes", 0),
                databases=app.get("databases", []),
                warnings=app.get("warnings", []),
                errors=app.get("errors", []),
            )
        )
    return ContentsView(
        run_id=run_id,
        archive=path.name,
        created=manifest.get("created"),
        only=manifest.get("only"),
        apps=apps,
    )


@router.get("/backups/{run_id}/apps/{key}/entries")
def app_entries(
    run_id: int, key: str, request: Request, db: DbDep, _principal: InteractiveDep
) -> list[EntryView]:
    _path, manifest = _archive_of(run_id, request, db)
    prefix = f"apps/{key}/"
    return [
        EntryView(path=e["path"][len(prefix) :], kind=e["kind"], size=e.get("size", 0))
        for e in downloads.app_entries(manifest, key)
    ]


def _attachment(name: str) -> dict[str, str]:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:150] or "download"
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


@router.get("/backups/{run_id}/download")
def download_backup(
    run_id: int, request: Request, db: DbDep, principal: InteractiveDep
) -> FileResponse:
    path, _manifest = _archive_of(run_id, request, db)
    with db.write() as session:
        audit.record(session, principal.actor, "backup.downloaded", path.name)
    return FileResponse(path, media_type="application/zstd", headers=_attachment(path.name))


@router.get("/backups/{run_id}/apps/{key}/download")
def download_app(
    run_id: int, key: str, request: Request, db: DbDep, principal: InteractiveDep
) -> StreamingResponse:
    path, manifest = _archive_of(run_id, request, db)
    if not downloads.app_entries(manifest, key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app_not_in_backup")
    with db.write() as session:
        audit.record(session, principal.actor, "backup.app_downloaded", f"{path.name}:{key}")
    stamp = path.name.removeprefix("backuphaven-").removesuffix(".tar.zst")
    return StreamingResponse(
        downloads.stream_app(path, manifest, key),
        media_type="application/zstd",
        headers=_attachment(f"{key}-{stamp}.tar.zst"),
    )


@router.get("/backups/{run_id}/apps/{key}/file")
def download_file(
    run_id: int,
    key: str,
    path: str,
    request: Request,
    db: DbDep,
    principal: InteractiveDep,
) -> StreamingResponse:
    archive, manifest = _archive_of(run_id, request, db)
    name = f"apps/{key}/{path}"
    try:
        unpack.safe_relative(name)
    except unpack.UnpackError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_path") from None
    if not any(e["path"] == name and e["kind"] == "file" for e in manifest.get("entries", [])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_not_in_backup")
    with db.write() as session:
        audit.record(session, principal.actor, "backup.file_downloaded", f"{archive.name}:{name}")
    return StreamingResponse(
        downloads.stream_file(archive, manifest, name),
        media_type="application/octet-stream",
        headers=_attachment(PurePosixPath(path).name),
    )
