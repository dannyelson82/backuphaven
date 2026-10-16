"""Security settings and API key endpoints. The API key can't use these."""

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.utils import get_openapi

from backuphaven import audit
from backuphaven.api.deps import (
    GATEWAY_WARNING_WINDOW_S,
    AnyPrincipalDep,
    ContextDep,
    DbDep,
    InteractiveDep,
    csrf_protect,
    require_setup_done,
)
from backuphaven.api.schemas import ApiKeyInfo, NewApiKey, SecurityView
from backuphaven.auth import network, security_settings
from backuphaven.auth.security_settings import SecuritySettings, StoredApiKey
from backuphaven.auth.tokens import hash_token, new_api_key
from backuphaven.db.types import utcnow
from backuphaven.logsetup import SecretRegistry

router = APIRouter(dependencies=[Depends(require_setup_done), Depends(csrf_protect)])


def _view(request: Request, ctx: ContextDep, db: DbDep) -> SecurityView:
    with db.read() as session:
        security = security_settings.load(session)
        key = security_settings.load_api_key(session)
    seen: float | None = request.app.state.gateway_seen_at
    return SecurityView(
        local_bypass=security.local_bypass,
        trusted_proxies=security.trusted_proxies,
        session_idle_days=security.session_idle_days,
        api_key=None if key is None else ApiKeyInfo(prefix=key.prefix, created_at=key.created_at),
        client_ip=ctx.ip_text,
        bypass_applies_to_you=network.bypass_applies(ctx.ip, True, request.app.state.gateways),
        gateway_warning=(
            security.local_bypass
            and seen is not None
            and time.time() - seen < GATEWAY_WARNING_WINDOW_S
        ),
    )


@router.get("/settings/security")
def get_security(
    request: Request, ctx: ContextDep, db: DbDep, _principal: InteractiveDep
) -> SecurityView:
    return _view(request, ctx, db)


@router.put("/settings/security")
def put_security(
    body: SecuritySettings,
    request: Request,
    ctx: ContextDep,
    db: DbDep,
    principal: InteractiveDep,
) -> SecurityView:
    with db.write() as session:
        before = security_settings.load(session)
        security_settings.save(session, body)
        changed = {k: v for k, v in body.model_dump().items() if getattr(before, k) != v}
        if changed:
            audit.record(session, principal.actor, "security.updated", None, changed)
    return _view(request, ctx, db)


@router.post("/settings/security/api-key")
def rotate_api_key(db: DbDep, principal: InteractiveDep) -> NewApiKey:
    """Create or replace the API key. The full key is only ever returned here."""
    key = new_api_key()
    stored = StoredApiKey(hash=hash_token(key), prefix=key[:6], created_at=utcnow())
    with db.write() as session:
        security_settings.save_api_key(session, stored)
        audit.record(session, principal.actor, "api_key.rotated", None, {"prefix": stored.prefix})
    SecretRegistry.register(key)
    return NewApiKey(api_key=key, prefix=stored.prefix)


@router.get("/openapi.json", include_in_schema=False)
def openapi(request: Request, _principal: AnyPrincipalDep) -> dict[str, Any]:
    """OpenAPI schema, for authenticated users only (SECURITY.md)."""
    app = request.app
    return get_openapi(title=app.title, version=app.version, routes=app.routes)
