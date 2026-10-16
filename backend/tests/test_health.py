from pathlib import Path

from fastapi.testclient import TestClient

from backuphaven import __version__
from backuphaven.app import create_app
from backuphaven.config import Settings


def test_healthz_returns_ok_without_details(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version(client: TestClient) -> None:
    assert client.get("/api/v1/version").json() == {
        "name": "BackupHaven",
        "version": __version__,
    }


def test_openapi_docs_not_public(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/api/v1/version").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"


def test_serves_the_built_frontend(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>BackupHaven</title>")
    settings = Settings(config_dir=tmp_path / "config", web_dir=web)
    with TestClient(create_app(settings, gateways=frozenset())) as client:
        assert "<title>BackupHaven</title>" in client.get("/").text
        assert client.get("/healthz").json() == {"status": "ok"}  # API still wins


def test_version_has_four_parts() -> None:
    """Four-part version numbers (ADR-0012)."""
    import re

    from backuphaven import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", __version__)
