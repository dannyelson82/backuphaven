"""The Backups page API: list, reasons, delete by hand."""

import time

from fastapi.testclient import TestClient

from backuphaven import retention
from backuphaven.app import create_app
from backuphaven.config import Settings
from tests.helpers import API, admin_client, client_at
from tests.test_runner import unraid  # noqa: F401 - fixture


def _wait(app) -> None:  # type: ignore[no-untyped-def]
    for _ in range(200):
        if not app.state.runner.running:
            return
        time.sleep(0.05)


def test_list_and_delete_backups(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        with app.state.db.write() as session:
            retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
        admin.put(f"{API}/schedule/apps", json={"apps": ["Tool", "sonarr"]})
        first = admin.post(f"{API}/runs").json()["run_id"]
        _wait(app)
        chosen = admin.post(f"{API}/runs", json={"apps": ["Tool"]}).json()["run_id"]
        _wait(app)
        last = admin.post(f"{API}/runs").json()["run_id"]
        _wait(app)
        listed = admin.get(f"{API}/backups").json()
        # The first went by the keep rules: same day as a newer full backup.
        assert [b["run_id"] for b in listed] == [last, chosen]
        newest, older = listed
        assert newest["protected"] and "newest good backup" in newest["keep_reasons"]
        assert newest["on_disk"] and newest["databases"] == 2 and newest["healthy"] == 2
        assert older["keep_reasons"] == ["chosen apps"] and not older["protected"]
        assert first not in [b["run_id"] for b in listed]

        refused = admin.delete(f"{API}/backups/{newest['run_id']}")
        assert refused.status_code == 409 and refused.json()["detail"] == "backup_protected"
        assert admin.delete(f"{API}/backups/99999").status_code == 404
        assert client_at(app, "203.0.113.5").delete(
            f"{API}/backups/{older['run_id']}"
        ).status_code in (401, 403)

        assert admin.delete(f"{API}/backups/{older['run_id']}").status_code == 204
        assert not (unraid.backups_dir / older["archive"]).exists()
        assert [b["run_id"] for b in admin.get(f"{API}/backups").json()] == [newest["run_id"]]
        assert admin.delete(f"{API}/backups/{older['run_id']}").status_code == 404
