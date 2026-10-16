"""Request context, authentication and CSRF dependencies."""

import time
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import exists, select

from backuphaven.auth import network, security_settings, sessions
from backuphaven.auth.network import IPAddress
from backuphaven.auth.security_settings import SecuritySettings
from backuphaven.auth.throttle import LoginThrottle
from backuphaven.auth.tokens import hash_token, tokens_equal
from backuphaven.db import Database, User

SESSION_COOKIE = "backuphaven_session"
CSRF_COOKIE = "backuphaven_csrf"
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-Api-Key"
API_KEY_QUERY = "apikey"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GATEWAY_WARNING_WINDOW_S = 24 * 3600


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.throttle
    return throttle


DbDep = Annotated[Database, Depends(get_db)]


@dataclass(frozen=True)
class RequestContext:
    ip: IPAddress | None
    https: bool
    security: SecuritySettings
    setup_required: bool

    @property
    def ip_text(self) -> str | None:
        return None if self.ip is None else str(self.ip)


def request_context(request: Request, db: DbDep) -> RequestContext:
    with db.read() as session:
        security = security_settings.load(session)
        has_user = session.scalar(select(exists().where(User.id.is_not(None))))
    trusted = network.parse_networks(security.trusted_proxies)
    socket_ip = request.client.host if request.client else None
    return RequestContext(
        ip=network.resolve_client_ip(socket_ip, request.headers.get("x-forwarded-for"), trusted),
        https=network.forwarded_https(
            request.url.scheme, socket_ip, request.headers.get("x-forwarded-proto"), trusted
        ),
        security=security,
        setup_required=not has_user,
    )


ContextDep = Annotated[RequestContext, Depends(request_context)]


@dataclass(frozen=True)
class Principal:
    kind: Literal["session", "bypass", "api_key"]
    user_id: int | None
    username: str | None
    session_hash: str | None = None

    @property
    def actor(self) -> str:
        return self.username or self.kind


def _api_key_from(request: Request) -> str | None:
    return request.headers.get(API_KEY_HEADER) or request.query_params.get(API_KEY_QUERY)


def set_session_cookie(
    response: Response, token: str, ctx: RequestContext, remember: bool = True
) -> None:
    """Unticked "Keep me logged in" gives a browser-session cookie (ReelHaven ADR-0033)."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ctx.security.session_idle_days * 86400 if remember else None,
        httponly=True,
        samesite="lax",
        secure=ctx.https,
        path="/",
    )


def current_principal(
    request: Request, response: Response, db: DbDep, ctx: ContextDep
) -> Principal | None:
    """Who is calling: API key, then session cookie, then local-address bypass."""
    supplied_key = _api_key_from(request)
    if supplied_key is not None:
        with db.read() as session:
            stored = security_settings.load_api_key(session)
        if stored is None or not tokens_equal(hash_token(supplied_key), stored.hash):
            # A wrong key never falls back to other methods.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_api_key")
        return Principal("api_key", None, None)

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db.read() as session:
            found = sessions.find(session, token)
        if found is not None:
            auth_session, user = found
            if sessions.needs_refresh(auth_session):
                with db.write() as session:
                    sessions.refresh(session, auth_session.id_hash, ctx.security.session_idle_days)
                set_session_cookie(response, token, ctx, auth_session.remember)
            return Principal("session", user.id, user.username, auth_session.id_hash)

    if ctx.security.local_bypass and ctx.ip is not None:
        gateways: frozenset[IPAddress] = request.app.state.gateways
        if ctx.ip in gateways:
            request.app.state.gateway_seen_at = time.time()
        elif network.bypass_applies(ctx.ip, True, gateways):
            with db.read() as session:
                admin = session.scalars(select(User).order_by(User.id).limit(1)).first()
            if admin is not None:
                return Principal("bypass", admin.id, admin.username)
    return None


PrincipalDep = Annotated[Principal | None, Depends(current_principal)]


def require_setup_done(ctx: ContextDep) -> None:
    if ctx.setup_required:
        raise HTTPException(status.HTTP_409_CONFLICT, "setup_required")


def require_principal(principal: PrincipalDep) -> Principal:
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    return principal


def require_interactive(principal: Annotated[Principal, Depends(require_principal)]) -> Principal:
    """A person in the UI (session or bypass), not a script with the API key."""
    if principal.kind == "api_key":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "api_key_not_allowed")
    return principal


def csrf_protect(request: Request) -> None:
    """Double-submit CSRF check on every state-changing request.

    Applies to session *and* local-address-bypass requests: without it, any
    website could make a LAN user's browser drive BackupHaven. API-key requests
    are exempt because a browser never attaches the key on its own.
    """
    if request.method not in UNSAFE_METHODS or _api_key_from(request) is not None:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not tokens_equal(cookie, header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_failed")


InteractiveDep = Annotated[Principal, Depends(require_interactive)]
AnyPrincipalDep = Annotated[Principal, Depends(require_principal)]
