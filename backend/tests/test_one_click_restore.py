"""One-click restore (phase 0.5 chunk 6): with the socket proxy, a restore stops the
app's container and starts it again itself."""

import time

from fastapi.testclient import TestClient

from backuphaven import retention
from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.plugins.fake_docker import fake_docker
from tests.helpers import API, admin_client
from tests.test_runner import unraid  # noqa: F401 - fixture


def _wait(admin, url: str) -> None:  # type: ignore[no-untyped-def]
    for _ in range(200):
        if admin.get(url).json() is None:
            return
        time.sleep(0.05)


def test_restore_and_undo_stop_and_start_the_container(unraid: Settings) -> None:
    tool = unraid.data_root / "appdata" / "tool" / "settings.json"
    with fake_docker() as (docker, address):
        docker.add("Tool", "someone/tool")
        settings = unraid.model_copy(update={"docker_proxy": address, "restore_quiet_s": 0})
        app = create_app(settings, gateways=frozenset())
        with TestClient(app):
            admin = admin_client(app)
            with app.state.db.write() as session:
                retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
            admin.put(f"{API}/schedule/apps", json={"apps": ["Tool"]})
            run_id = admin.post(f"{API}/runs").json()["run_id"]
            for _ in range(200):
                if not app.state.runner.running:
                    break
                time.sleep(0.05)
            tool.write_text('{"changed": true}')
            body = {"run_id": run_id, "app": "Tool"}
            check = admin.post(f"{API}/restores/check", json=body).json()
            assert check["auto_stop"] is True and check["stopped"] is True
            docker.calls.clear()
            started = admin.post(f"{API}/restores", json=body)  # no confirmation needed
            assert started.status_code == 202
            _wait(admin, f"{API}/restores/current")
            restore = admin.get(f"{API}/restores/{started.json()['restore_id']}").json()
            assert restore["status"] == "done", restore["error"]
            assert tool.read_text() == "{}"
            posts = [c.split("/")[-1].split("?")[0] for c in docker.calls if c.startswith("POST")]
            assert posts == ["stop", "start"] and docker.state("Tool") == "running"

            docker.calls.clear()
            undone = admin.post(f"{API}/restores/{restore['id']}/undo").json()
            assert undone["status"] == "undone" and tool.read_text() == '{"changed": true}'
            posts = [c.split("/")[-1].split("?")[0] for c in docker.calls if c.startswith("POST")]
            assert posts == ["stop", "start"]


def test_a_container_that_does_not_start_after_a_restore_is_reported(unraid: Settings) -> None:
    with fake_docker() as (docker, address):
        docker.add("Tool", "someone/tool")
        settings = unraid.model_copy(update={"docker_proxy": address, "restore_quiet_s": 0})
        app = create_app(settings, gateways=frozenset())
        with TestClient(app):
            admin = admin_client(app)
            with app.state.db.write() as session:
                retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
            admin.put(f"{API}/schedule/apps", json={"apps": ["Tool"]})
            run_id = admin.post(f"{API}/runs").json()["run_id"]
            for _ in range(200):
                if not app.state.runner.running:
                    break
                time.sleep(0.05)
            docker.fail_start = True
            restore_id = admin.post(
                f"{API}/restores", json={"run_id": run_id, "app": "Tool"}
            ).json()["restore_id"]
            _wait(admin, f"{API}/restores/current")
            restore = admin.get(f"{API}/restores/{restore_id}").json()
            assert "didn't start again" in (restore["error"] or "")
            assert any(
                a["kind"] == "container_down"
                for a in admin.get(f"{API}/status").json()["open_alerts"]
            )
