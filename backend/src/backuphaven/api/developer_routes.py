"""Developer options (Settings → Developer): diagnostics files."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backuphaven import audit, settings_store
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done
from backuphaven.diagnostics import SETTING_KEY, Diagnostics

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


class DeveloperSettings(BaseModel):
    diagnostics: bool


class DiagnosticsFile(BaseModel):
    name: str
    size: int
    modified: datetime


class DeveloperView(DeveloperSettings):
    folder: str
    files: list[DiagnosticsFile]


def _view(diagnostics: Diagnostics) -> DeveloperView:
    return DeveloperView(
        diagnostics=diagnostics.enabled,
        folder=str(diagnostics.folder),
        files=[DiagnosticsFile(**f) for f in diagnostics.files()],
    )


@router.get("/settings/developer")
def get_developer(request: Request, _principal: InteractiveDep) -> DeveloperView:
    return _view(request.app.state.diagnostics)


@router.put("/settings/developer")
def put_developer(
    body: DeveloperSettings, request: Request, db: DbDep, principal: InteractiveDep
) -> DeveloperView:
    diagnostics: Diagnostics = request.app.state.diagnostics
    if body.diagnostics:
        diagnostics.enable()
    else:
        diagnostics.disable()
    with db.write() as session:
        settings_store.put(session, SETTING_KEY, body.model_dump())
        audit.record(session, principal.actor, "developer.updated", None, body.model_dump())
    return _view(diagnostics)
