"""Settings → Alerts API (phase 0.4 chunk 3)."""

from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from tests.helpers import API, admin_client, client_at
from tests.test_alert_channels import Recorder, server  # noqa: F401 - fixture
from tests.test_runner import unraid  # noqa: F401 - fixture

BASE = f"{API}/settings/alerts"


def test_channels_add_test_edit_remove_without_leaking_secrets(
    unraid: Settings, server: str
) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        view = admin.get(BASE).json()
        assert view["channels"] == [] and "run_failed" in view["kinds"]

        hook = f"{server}/api/webhook/my-secret-hook"
        added = admin.post(
            f"{BASE}/channels",
            json={"type": "homeassistant", "name": "Home Assistant", "secret": hook},
        )
        assert added.status_code == 201
        [channel] = added.json()["channels"]
        assert channel["has_secret"] is True and "my-secret-hook" not in added.text
        assert "my-secret-hook" not in admin.get(BASE).text

        result = admin.post(f"{BASE}/channels/{channel['id']}/test").json()
        assert result == {"ok": True, "error": None}
        assert Recorder.received[-1]["path"] == "/api/webhook/my-secret-hook"

        # Editing without a new secret keeps the stored one.
        edited = admin.put(
            f"{BASE}/channels/{channel['id']}",
            json={"type": "homeassistant", "name": "HA", "kinds": ["run_failed"]},
        ).json()["channels"][0]
        assert edited["name"] == "HA" and edited["kinds"] == ["run_failed"] and edited["has_secret"]
        assert admin.post(f"{BASE}/channels/{channel['id']}/test").json()["ok"] is True

        Recorder.status = 500
        failed = admin.post(f"{BASE}/channels/{channel['id']}/test").json()
        assert failed["ok"] is False and "500" in failed["error"]

        assert admin.delete(f"{BASE}/channels/{channel['id']}").json()["channels"] == []
        assert admin.delete(f"{BASE}/channels/{channel['id']}").status_code == 404


def test_bad_channels_are_refused(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        for body in (
            {"type": "discord", "name": "no address"},
            {"type": "discord", "name": "bad", "secret": "file:///etc/passwd"},
            {"type": "ntfy", "name": "bad topic", "settings": {"topic": "a b"}},
            {"type": "ntfy", "name": "x", "settings": {"topic": "ok"}, "kinds": ["nope"]},
            {"type": "pigeon", "name": "x"},
        ):
            assert admin.post(f"{BASE}/channels", json=body).status_code == 422, body
        assert client_at(app, "203.0.113.5").get(BASE).status_code == 401


def test_summary_settings_history_and_open_alerts_on_the_dashboard(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        view = admin.put(
            f"{BASE}/summary", json={"summary_enabled": True, "summary_time": "07:30"}
        ).json()
        assert view["summary_enabled"] is True and view["summary_time"] == "07:30"
        assert (
            admin.put(
                f"{BASE}/summary", json={"summary_enabled": True, "summary_time": "7am"}
            ).status_code
            == 422
        )
        app.state.alerts.raise_("disk_low", "disk_low", "2 GB left")
        status = admin.get(f"{API}/status").json()
        assert [a["title"] for a in status["open_alerts"]] == ["Disk nearly full"]
        assert admin.get(f"{API}/alerts").json()[0]["message"] == "2 GB left"
