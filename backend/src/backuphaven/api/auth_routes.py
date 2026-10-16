"""Setup, login, logout and password endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import exists, select

from backuphaven import audit
from backuphaven.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    ContextDep,
    DbDep,
    InteractiveDep,
    PrincipalDep,
    RequestContext,
    csrf_protect,
    get_throttle,
    require_setup_done,
    set_session_cookie,
)
from backuphaven.api.schemas import AuthState, Credentials, PasswordChange, SetupRequest
from backuphaven.auth import passwords, sessions
from backuphaven.auth.throttle import LoginThrottle
from backuphaven.auth.tokens import hash_token, new_csrf_token
from backuphaven.db import Database, User
from backuphaven.db.types import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(csrf_protect)])


def _csrf_token(request: Request, response: Response, ctx: RequestContext) -> str:
    token = request.cookies.get(CSRF_COOKIE)
    if not token or len(token) > 128:
        token = new_csrf_token()
        # Readable by the page's JavaScript on purpose (double-submit pattern).
        response.set_cookie(
            CSRF_COOKIE, token, httponly=False, samesite="strict", secure=ctx.https, path="/"
        )
    return token


def _log_not_logged_in(request: Request, db: Database, ctx: RequestContext) -> None:
    """Say why a page load wasn't logged in (ReelHaven ADR-0033). Never logs the cookie itself."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        reason = "no_cookie"
    else:
        with db.read() as session:
            reason = sessions.miss_reason(session, token)
    logger.info(
        "not logged in",
        extra={
            "reason": reason,
            "client": ctx.ip_text,
            "user_agent": (request.headers.get("user-agent") or "")[:200],
            # Names only: other apps on the same address share the cookie jar, and a
            # crowded jar or an oversized Cookie header can push ours out.
            "cookie_names": sorted(request.cookies)[:40],
            "cookie_header_bytes": len(request.headers.get("cookie") or ""),
        },
    )


@router.get("/auth/state")
def auth_state(
    request: Request,
    response: Response,
    db: DbDep,
    ctx: ContextDep,
    principal: PrincipalDep,
) -> AuthState:
    """Public: tells the UI whether to show setup, login or the app."""
    if principal is None and not ctx.setup_required:
        _log_not_logged_in(request, db, ctx)
    return AuthState(
        setup_required=ctx.setup_required,
        authenticated=principal is not None,
        username=principal.username if principal else None,
        method=principal.kind if principal else None,
        csrf_token=_csrf_token(request, response, ctx),
    )


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(
    body: SetupRequest, request: Request, response: Response, db: DbDep, ctx: ContextDep
) -> AuthState:
    """Create the admin account. Works only while no user exists (ReelHaven ADR-0013)."""
    password_hash = passwords.hash_password(body.password)  # slow: do it outside the lock
    with db.write() as session:
        # Checked inside the write transaction, so two racing requests can't both pass.
        if session.scalar(select(exists().where(User.id.is_not(None)))):
            raise HTTPException(status.HTTP_409_CONFLICT, "setup_already_done")
        user = User(username=body.username, password_hash=password_hash)
        session.add(user)
        session.flush()
        token = sessions.create(
            session,
            user,
            ctx.security.session_idle_days,
            ctx.ip_text,
            request.headers.get("user-agent"),
        )
        audit.record(session, body.username, "setup.admin_created", ctx.ip_text)
    set_session_cookie(response, token, ctx)
    return AuthState(
        setup_required=False,
        authenticated=True,
        username=user.username,
        method="session",
        csrf_token=_csrf_token(request, response, ctx),
    )


ThrottleDep = Annotated[LoginThrottle, Depends(get_throttle)]


@router.post("/auth/login", dependencies=[Depends(require_setup_done)])
def login(
    body: Credentials,
    request: Request,
    response: Response,
    db: DbDep,
    ctx: ContextDep,
    throttle: ThrottleDep,
) -> AuthState:
    keys = [f"user:{body.username}", f"ip:{ctx.ip_text or 'unknown'}"]
    wait = throttle.retry_after(keys)
    if wait > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too_many_attempts",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    with db.read() as session:
        user = session.scalars(select(User).where(User.username == body.username)).first()
    if user is None:
        passwords.burn_verify_time(body.password)
        ok = False
    else:
        ok = passwords.verify_password(user.password_hash, body.password)

    if not ok or user is None:
        locked = throttle.failure(keys)
        logger.warning("login failed", extra={"username": body.username, "client": ctx.ip_text})
        if locked:
            with db.write() as session:
                audit.record(
                    session,
                    ctx.ip_text or "unknown",
                    "login.throttled",
                    body.username,
                    {"keys": locked},
                )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    throttle.success(keys)
    new_hash = (
        passwords.hash_password(body.password)
        if passwords.needs_rehash(user.password_hash)
        else None
    )
    with db.write() as session:
        if new_hash is not None:
            fresh = session.get(User, user.id)
            if fresh is not None:
                fresh.password_hash = new_hash
        token = sessions.create(
            session,
            user,
            ctx.security.session_idle_days,
            ctx.ip_text,
            request.headers.get("user-agent"),
            remember=body.remember,
        )
        audit.record(session, user.username, "login.success", ctx.ip_text)
    set_session_cookie(response, token, ctx, body.remember)
    return AuthState(
        setup_required=False,
        authenticated=True,
        username=user.username,
        method="session",
        csrf_token=_csrf_token(request, response, ctx),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbDep) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db.write() as session:
            sessions.revoke(session, hash_token(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post(
    "/auth/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_setup_done)],
)
def logout_everywhere(principal: InteractiveDep, response: Response, db: DbDep) -> None:
    assert principal.user_id is not None  # noqa: S101 - interactive principals have a user
    with db.write() as session:
        count = sessions.revoke_all(session, principal.user_id)
        audit.record(session, principal.actor, "sessions.revoked_all", None, {"count": count})
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post(
    "/auth/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_setup_done)],
)
def change_password(body: PasswordChange, principal: InteractiveDep, db: DbDep) -> None:
    assert principal.user_id is not None  # noqa: S101
    with db.read() as session:
        user = session.get(User, principal.user_id)
    if user is None or not passwords.verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wrong_current_password")
    new_hash = passwords.hash_password(body.new_password)
    with db.write() as session:
        fresh = session.get(User, principal.user_id)
        if fresh is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
        fresh.password_hash = new_hash
        fresh.password_changed_at = utcnow()
        # Other devices must log in again with the new password.
        sessions.revoke_all(session, fresh.id, keep=principal.session_hash)
        audit.record(session, principal.actor, "password.changed")
