import logging
import threading
from collections.abc import Iterator
from datetime import timedelta
from ipaddress import ip_address

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backuphaven.app import create_app
from backuphaven.auth.throttle import FREE_ATTEMPTS
from backuphaven.config import Settings
from backuphaven.db import AuditLog, AuthSession, Database
from backuphaven.db.types import utcnow
from tests.helpers import ADMIN, API, client_at, csrf, do_setup

GATEWAY = "172.17.0.1"


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    application = create_app(settings, gateways=frozenset({ip_address(GATEWAY)}))
    with TestClient(application):  # run startup once (migrations)
        yield application


def login(client: TestClient, password: str = ADMIN["password"]) -> int:
    body = {"username": ADMIN["username"], "password": password}
    return client.post(f"{API}/auth/login", json=body, headers=csrf(client)).status_code


def audit_actions(app: FastAPI) -> list[str]:
    db: Database = app.state.db
    with db.read() as session:
        return [a.action for a in session.scalars(select(AuditLog).order_by(AuditLog.id))]


# --- setup -------------------------------------------------------------------


def test_fresh_install_requires_setup(app: FastAPI) -> None:
    client = client_at(app)
    state = client.get(f"{API}/auth/state").json()
    assert state["setup_required"] is True
    assert state["authenticated"] is False
    assert client.cookies.get("backuphaven_csrf") == state["csrf_token"]
    assert client.get(f"{API}/settings/security").status_code == 409
    assert client.post(f"{API}/auth/login", json=ADMIN, headers=csrf(client)).status_code == 409


def test_setup_requires_csrf(app: FastAPI) -> None:
    client = client_at(app)
    client.get(f"{API}/auth/state")
    response = client.post(f"{API}/setup", json=ADMIN)
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf_failed"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "admin", "password": "short"},
        {"username": "bad name!", "password": "long enough password"},
        {"username": "", "password": "long enough password"},
        {"username": "admin", "password": "long enough password", "is_admin": True},
    ],
)
def test_setup_validation(app: FastAPI, body: dict[str, object]) -> None:
    client = client_at(app)
    assert client.post(f"{API}/setup", json=body, headers=csrf(client)).status_code == 422


def test_setup_creates_admin_once(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    state = client.get(f"{API}/auth/state").json()
    assert state == {**state, "authenticated": True, "username": "admin", "method": "session"}
    other = client_at(app, "192.168.1.99")
    second = other.post(
        f"{API}/setup", json={"username": "evil", "password": "evil password!"}, headers=csrf(other)
    )
    assert second.status_code == 409
    assert audit_actions(app) == ["setup.admin_created"]


def test_concurrent_setup_creates_one_admin(app: FastAPI) -> None:
    results: list[int] = []

    def attempt(n: int) -> None:
        client = client_at(app, f"192.168.1.{n + 10}")
        body = {"username": f"user{n}", "password": "long enough password"}
        results.append(client.post(f"{API}/setup", json=body, headers=csrf(client)).status_code)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [201] + [409] * 5


# --- login, logout, sessions --------------------------------------------------


def test_login_and_session_cookie_flags(app: FastAPI) -> None:
    do_setup(client_at(app))
    client = client_at(app)
    body = {"username": "ADMIN", "password": ADMIN["password"]}  # case-insensitive
    response = client.post(f"{API}/auth/login", json=body, headers=csrf(client))
    assert response.status_code == 200
    cookie = next(h for h in response.headers.get_list("set-cookie") if "backuphaven_session" in h)
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie  # plain HTTP
    assert client.get(f"{API}/settings/security").status_code == 200


def session_cookie_header(set_cookie: list[str]) -> str:
    return next(h for h in set_cookie if "backuphaven_session" in h)


def test_keep_me_logged_in(app: FastAPI) -> None:
    do_setup(client_at(app))
    client = client_at(app)
    remembered = client.post(f"{API}/auth/login", json=ADMIN, headers=csrf(client))
    assert "Max-Age=604800" in session_cookie_header(remembered.headers.get_list("set-cookie"))

    client = client_at(app)
    body = {**ADMIN, "remember": False}
    response = client.post(f"{API}/auth/login", json=body, headers=csrf(client))
    assert response.status_code == 200
    assert "Max-Age" not in session_cookie_header(
        response.headers.get_list("set-cookie")
    )  # gone when the browser closes
    # The sliding refresh keeps it a browser-session cookie.
    db: Database = app.state.db
    with db.write() as session:
        session.execute(
            update(AuthSession)
            .where(AuthSession.remember.is_(False))
            .values(last_seen_at=utcnow() - timedelta(hours=1))
        )
    refreshed = client.get(f"{API}/settings/security")
    assert refreshed.status_code == 200
    assert "Max-Age" not in session_cookie_header(refreshed.headers.get_list("set-cookie"))


def test_logs_why_a_page_load_was_not_logged_in(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    client = client_at(app)
    do_setup(client)
    token = client.cookies.get("backuphaven_session") or ""
    db: Database = app.state.db
    with db.write() as session:
        session.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)))

    def reason(cookie: str | None) -> str:
        caplog.clear()
        fresh = client_at(app)
        if cookie is not None:
            fresh.cookies.set("backuphaven_session", cookie)
        fresh.cookies.set("other_app", "x")
        with caplog.at_level(logging.INFO, logger="backuphaven.api.auth_routes"):
            assert fresh.get(f"{API}/auth/state").json()["authenticated"] is False
        (record,) = [r for r in caplog.records if r.getMessage() == "not logged in"]
        assert token not in str(record.__dict__)  # never the cookie itself
        assert "other_app" in record.__dict__["cookie_names"]
        return str(record.__dict__["reason"])

    assert reason(None) == "no_cookie"
    assert reason("made-up") == "unknown"
    assert reason(token) == "expired"


def test_wrong_password_and_unknown_user_look_identical(app: FastAPI) -> None:
    do_setup(client_at(app))
    client = client_at(app)
    wrong = client.post(
        f"{API}/auth/login", json={**ADMIN, "password": "nope nope nope"}, headers=csrf(client)
    )
    client2 = client_at(app, "192.168.1.30")
    unknown = client2.post(
        f"{API}/auth/login",
        json={"username": "ghost", "password": "nope nope nope"},
        headers=csrf(client2),
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid_credentials"}


def test_login_throttling_and_audit(app: FastAPI) -> None:
    do_setup(client_at(app))
    client = client_at(app, "192.168.1.66")
    for _ in range(FREE_ATTEMPTS):
        assert login(client, "wrong password") == 401
    response = client.post(f"{API}/auth/login", json=ADMIN, headers=csrf(client))
    assert response.status_code == 429  # even the right password must wait
    assert int(response.headers["Retry-After"]) >= 1
    assert "login.throttled" in audit_actions(app)


def test_logout_revokes_session(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    stolen = client.cookies.get("backuphaven_session")
    assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204
    assert client.get(f"{API}/auth/state").json()["authenticated"] is False
    replay = client_at(app)
    replay.cookies.set("backuphaven_session", stolen or "")
    assert replay.get(f"{API}/settings/security").status_code == 401


def test_logout_everywhere(app: FastAPI) -> None:
    first = client_at(app)
    do_setup(first)
    second = client_at(app, "192.168.1.31")
    assert login(second) == 200
    assert first.post(f"{API}/auth/logout-all", headers=csrf(first)).status_code == 204
    assert second.get(f"{API}/settings/security").status_code == 401
    assert first.get(f"{API}/settings/security").status_code == 401
    assert "sessions.revoked_all" in audit_actions(app)


def test_expired_session_rejected(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    db: Database = app.state.db
    with db.write() as session:
        session.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get(f"{API}/settings/security").status_code == 401


def test_change_password(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    other = client_at(app, "192.168.1.32")
    assert login(other) == 200
    headers = csrf(client)
    bad = {"current_password": "not it at all", "new_password": "a brand new password"}
    assert client.post(f"{API}/auth/password", json=bad, headers=headers).status_code == 400
    good = {"current_password": ADMIN["password"], "new_password": "a brand new password"}
    assert client.post(f"{API}/auth/password", json=good, headers=headers).status_code == 204
    assert client.get(f"{API}/settings/security").status_code == 200  # this session kept
    assert other.get(f"{API}/settings/security").status_code == 401  # others revoked
    fresh = client_at(app, "192.168.1.33")
    assert login(fresh, "a brand new password") == 200


# --- security settings and API key --------------------------------------------


def test_security_settings_require_login(app: FastAPI) -> None:
    do_setup(client_at(app))
    assert client_at(app).get(f"{API}/settings/security").status_code == 401


def test_update_security_settings(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    body = {"local_bypass": True, "trusted_proxies": ["192.168.1.50"], "session_idle_days": 3}
    response = client.put(f"{API}/settings/security", json=body, headers=csrf(client))
    assert response.status_code == 200
    view = response.json()
    assert view["trusted_proxies"] == ["192.168.1.50/32"]
    assert view["client_ip"] == "192.168.1.20"
    assert view["bypass_applies_to_you"] is True
    assert "security.updated" in audit_actions(app)


@pytest.mark.parametrize(
    "body",
    [
        {"trusted_proxies": ["not-a-network"]},
        {"session_idle_days": 0},
        {"local_bypass": True, "disable_login": True},
    ],
)
def test_invalid_security_settings(app: FastAPI, body: dict[str, object]) -> None:
    client = client_at(app)
    do_setup(client)
    assert (
        client.put(f"{API}/settings/security", json=body, headers=csrf(client)).status_code == 422
    )


def test_api_key_lifecycle(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    created = client.post(f"{API}/settings/security/api-key", headers=csrf(client)).json()
    key = created["api_key"]
    assert len(key) == 64
    view = client.get(f"{API}/settings/security").json()
    assert view["api_key"]["prefix"] == key[:6]
    assert key not in str(view)  # never returned again

    script = client_at(app, "203.0.113.5")
    assert script.get(f"{API}/openapi.json", headers={"X-Api-Key": key}).status_code == 200
    assert script.get(f"{API}/openapi.json", params={"apikey": key}).status_code == 200
    # The API key can't touch security settings, even without needing CSRF.
    assert script.get(f"{API}/settings/security", headers={"X-Api-Key": key}).status_code == 403
    assert (
        script.post(f"{API}/settings/security/api-key", headers={"X-Api-Key": key}).status_code
        == 403
    )

    rotated = client.post(f"{API}/settings/security/api-key", headers=csrf(client)).json()
    assert script.get(f"{API}/openapi.json", headers={"X-Api-Key": key}).status_code == 401
    ok = script.get(f"{API}/openapi.json", headers={"X-Api-Key": rotated["api_key"]})
    assert ok.status_code == 200


def test_wrong_api_key_never_falls_back_to_session(app: FastAPI) -> None:
    client = client_at(app)
    do_setup(client)
    response = client.get(f"{API}/openapi.json", headers={"X-Api-Key": "wrong"})
    assert response.status_code == 401


def test_openapi_requires_auth(app: FastAPI) -> None:
    do_setup(client_at(app))
    assert client_at(app).get(f"{API}/openapi.json").status_code == 401


# --- local-address bypass -----------------------------------------------------


def enable_bypass(app: FastAPI, trusted: list[str] | None = None) -> None:
    admin = client_at(app)
    do_setup(admin)
    body = {"local_bypass": True, "trusted_proxies": trusted or []}
    assert admin.put(f"{API}/settings/security", json=body, headers=csrf(admin)).status_code == 200


def test_bypass_for_lan_clients_only(app: FastAPI) -> None:
    enable_bypass(app)
    lan = client_at(app, "192.168.1.77").get(f"{API}/auth/state").json()
    assert (lan["authenticated"], lan["method"], lan["username"]) == (True, "bypass", "admin")
    internet = client_at(app, "203.0.113.7").get(f"{API}/auth/state").json()
    assert internet["authenticated"] is False


def test_bypass_off_by_default(app: FastAPI) -> None:
    do_setup(client_at(app))
    assert client_at(app, "192.168.1.77").get(f"{API}/auth/state").json()["authenticated"] is False


def test_bypass_never_for_docker_gateway(app: FastAPI) -> None:
    enable_bypass(app)
    gateway = client_at(app, GATEWAY)
    assert gateway.get(f"{API}/auth/state").json()["authenticated"] is False
    admin = client_at(app)
    login(admin)
    assert admin.get(f"{API}/settings/security").json()["gateway_warning"] is True


def test_bypass_requests_still_need_csrf(app: FastAPI) -> None:
    enable_bypass(app)
    lan = client_at(app, "192.168.1.77")
    response = lan.post(f"{API}/settings/security/api-key")  # e.g. from a malicious web page
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf_failed"
    assert lan.post(f"{API}/settings/security/api-key", headers=csrf(lan)).status_code == 200


def test_spoofed_forwarded_for_ignored(app: FastAPI) -> None:
    enable_bypass(app)
    attacker = client_at(app, "203.0.113.7")
    state = attacker.get(f"{API}/auth/state", headers={"X-Forwarded-For": "192.168.1.5"}).json()
    assert state["authenticated"] is False


def test_trusted_proxy(app: FastAPI) -> None:
    enable_bypass(app, trusted=["192.168.1.50"])
    proxy = client_at(app, "192.168.1.50")
    internet = proxy.get(f"{API}/auth/state", headers={"X-Forwarded-For": "203.0.113.7"}).json()
    assert internet["authenticated"] is False  # proxied internet traffic: login required
    lan = proxy.get(f"{API}/auth/state", headers={"X-Forwarded-For": "192.168.1.8"}).json()
    assert lan["authenticated"] is True
    no_header = proxy.get(f"{API}/auth/state").json()
    assert no_header["authenticated"] is False  # unknown client is never "local"


def test_secure_cookie_behind_https_proxy(app: FastAPI) -> None:
    enable_bypass(app, trusted=["192.168.1.50"])
    proxy = client_at(app, "192.168.1.50")
    headers = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
    state = proxy.get(f"{API}/auth/state", headers=headers)
    assert "Secure" in state.headers["set-cookie"]  # CSRF cookie too
    token = state.json()["csrf_token"]
    # The test client speaks plain HTTP and so withholds Secure cookies; a
    # browser on HTTPS would send it back.
    proxy.cookies.set("backuphaven_csrf", token)
    response = proxy.post(
        f"{API}/auth/login", json=ADMIN, headers={**headers, "X-CSRF-Token": token}
    )
    assert response.status_code == 200
    cookie = next(h for h in response.headers.get_list("set-cookie") if "backuphaven_session" in h)
    assert "Secure" in cookie


# --- headers ------------------------------------------------------------------


def test_security_headers(app: FastAPI) -> None:
    client = client_at(app)
    for path in ("/healthz", f"{API}/auth/state"):
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["x-frame-options"] == "DENY"
    assert client.get(f"{API}/auth/state").headers["cache-control"] == "no-store"
