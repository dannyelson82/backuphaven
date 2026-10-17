"""Stop mode (ADR-0005): stop the container, back up, always start it again."""

from fastapi.testclient import TestClient

from backuphaven import app_modes
from backuphaven.alerts import AlertService
from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database, RunApp
from backuphaven.docker_proxy import DockerProxy
from backuphaven.plugins.fake_docker import fake_docker
from backuphaven.runner import Runner
from tests.helpers import API, admin_client
from tests.test_alerts import FakeChannel
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


def stop(db: Database, app: str) -> None:
    with db.write() as session:
        app_modes.save(session, app, "stop")


def result(db: Database, run_id: int, app: str) -> RunApp:
    with db.read() as session:
        return session.query(RunApp).filter_by(run_id=run_id, app=app).one()


def test_stopped_backed_up_and_started_again(unraid: Settings, db: Database) -> None:
    stop(db, "sonarr")
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr")
        run_id = Runner(db, unraid, plugins_of(unraid), proxy=DockerProxy(address)).run_now(
            only=["sonarr"], scheduled=True
        )
        posts = [c for c in docker.calls if c.startswith("POST")]
        assert [p.split("/")[-1].split("?")[0] for p in posts] == ["stop", "start"]
        assert docker.state("sonarr") == "running"
    assert result(db, run_id, "sonarr").status == "ok"


def test_a_container_that_does_not_come_back_is_an_error_and_an_alert(
    unraid: Settings,
    db: Database,
) -> None:
    stop(db, "sonarr")
    channel = FakeChannel()
    alerts = AlertService(db, lambda: [channel], retry_wait_s=0)
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr")
        docker.fail_start = True
        run_id = Runner(
            db, unraid, plugins_of(unraid), alerts=alerts, proxy=DockerProxy(address)
        ).run_now(only=["sonarr"], scheduled=True)
    app = result(db, run_id, "sonarr")
    assert app.status == "failed"
    assert "didn't start again" in app.problems["errors"][0]  # type: ignore[index]
    alerts.flush()
    assert channel.sent[0][0] == "Container didn't start"


def test_an_already_stopped_container_is_left_stopped(unraid: Settings, db: Database) -> None:
    stop(db, "sonarr")
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr", running=False)
        run_id = Runner(db, unraid, plugins_of(unraid), proxy=DockerProxy(address)).run_now(
            only=["sonarr"], scheduled=True
        )
        assert not [c for c in docker.calls if c.startswith("POST")]
        assert docker.state("sonarr") == "exited"
    assert result(db, run_id, "sonarr").status == "ok"


def test_without_the_proxy_stop_mode_backs_up_while_running(unraid: Settings, db: Database) -> None:
    stop(db, "sonarr")
    run_id = Runner(db, unraid, plugins_of(unraid)).run_now(only=["sonarr"], scheduled=True)
    app = result(db, run_id, "sonarr")
    assert app.status == "warning"
    assert "needs the socket proxy" in app.problems["warnings"][0]  # type: ignore[index]


def test_mode_api(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        found = {a["name"]: a for a in admin.get(f"{API}/apps/discovered").json()["apps"]}
        assert found["sonarr"]["mode"] == "snapshot" and found["sonarr"]["mode_chosen"] is False
        assert admin.put(f"{API}/apps/sonarr/mode", json={"mode": "stop"}).json() == {
            "mode": "stop"
        }
        found = {a["name"]: a for a in admin.get(f"{API}/apps/discovered").json()["apps"]}
        assert found["sonarr"]["mode"] == "stop" and found["sonarr"]["mode_chosen"] is True
        assert admin.put(f"{API}/apps/sonarr/mode", json={"mode": None}).json() == {
            "mode": "snapshot"
        }
        assert admin.put(f"{API}/apps/sonarr/mode", json={"mode": "pause"}).status_code == 422
