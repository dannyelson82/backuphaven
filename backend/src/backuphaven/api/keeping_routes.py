"""Settings → Keeping backups (ARCHITECTURE.md §6.8)."""

from fastapi import APIRouter, Depends

from backuphaven import audit, retention
from backuphaven.api.deps import DbDep, InteractiveDep, csrf_protect, require_setup_done

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


@router.get("/settings/keeping")
def get_keeping(db: DbDep, _principal: InteractiveDep) -> retention.KeepingSettings:
    with db.read() as session:
        return retention.load_settings(session)


@router.put("/settings/keeping")
def put_keeping(
    body: retention.KeepingSettings, db: DbDep, principal: InteractiveDep
) -> retention.KeepingSettings:
    """Saved now; the rules are applied after the next backup."""
    with db.write() as session:
        before = retention.load_settings(session)
        retention.save_settings(session, body)
        if before != body:
            audit.record(session, principal.actor, "keeping.updated", None, body.model_dump())
    return body
