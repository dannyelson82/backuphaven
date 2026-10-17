"""The socket proxy client (ADR-0005) and container status."""

import pytest
from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database, RunApp
from backuphaven.docker_proxy import DockerProxy, ProxyError, base_url
from backuphaven.plugins.fake_docker import fake_docker
from backuphaven.runner import Runner
from tests.helpers import API, admin_client
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


def test_list_stop_start() -> None:
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr")
        proxy = DockerProxy(address)
        [sonarr] = proxy.containers()
        assert (sonarr.name, sonarr.running) == ("sonarr", True)
        proxy.stop(sonarr)
        assert docker.state("sonarr") == "exited"
        proxy.stop(sonarr)  # already stopped: fine
        proxy.start(sonarr)
        assert docker.state("sonarr") == "running"
        assert proxy.find("SONARR") is not None and proxy.find("nope") is None


def test_never_stops_itself_or_the_proxy() -> None:
    with fake_docker() as (docker, address):
        docker.add("BackupHaven", "ghcr.io/dannyelson82/backuphaven:latest")
        docker.add("socket-proxy", "lscr.io/linuxserver/socket-proxy:latest")
        proxy = DockerProxy(address)
        for container in proxy.containers():
            with pytest.raises(ProxyError, match="never stops"):
                proxy.stop(container)
        assert not [c for c in docker.calls if c.startswith("POST")]


def test_refusals_and_unreachable_proxies_are_explained() -> None:
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr")
        docker.refuse = True
        proxy = DockerProxy(address)
        with pytest.raises(ProxyError, match="refused"):
            proxy.stop(proxy.containers()[0])
    with pytest.raises(ProxyError, match="can't reach"):
        DockerProxy("tcp://127.0.0.1:9").containers()
    for bad in ("unix:///var/run/docker.sock", "socket-proxy:2375", "file:///etc"):
        with pytest.raises(ProxyError):
            base_url(bad)
    assert base_url("tcp://socket-proxy:2375") == "http://socket-proxy:2375"


def test_with_the_proxy_removed_apps_are_known_exactly(unraid: Settings, db: Database) -> None:
    with fake_docker() as (docker, address):
        docker.add("Tool", "someone/tool")  # sonarr's container is gone, its folder isn't
        runner = Runner(db, unraid, plugins_of(unraid), proxy=DockerProxy(address))
        run_id = runner.run_now(only=["sonarr", "Tool"], scheduled=True)
    with db.read() as session:
        apps = {a.app: a for a in session.query(RunApp).filter_by(run_id=run_id)}
    assert apps["sonarr"].status == "skipped"
    assert "no container named sonarr" in apps["sonarr"].problems["warnings"][0]  # type: ignore[index]
    assert apps["Tool"].status == "ok"


def test_status_api_and_container_state_on_the_apps_page(unraid: Settings) -> None:
    with fake_docker() as (docker, address):
        docker.add("sonarr", "lscr.io/linuxserver/sonarr", running=False)
        docker.add("plex", "lscr.io/linuxserver/plex")
        app = create_app(unraid.model_copy(update={"docker_proxy": address}), gateways=frozenset())
        with TestClient(app):
            admin = admin_client(app)
            status = admin.get(f"{API}/settings/docker").json()
            assert status["configured"] and status["ok"] and status["containers"] == 2
            found = {a["name"]: a for a in admin.get(f"{API}/apps/discovered").json()["apps"]}
            assert found["sonarr"]["container"] == "stopped"
            assert found["plex"]["container"] == "running"
            assert found["Tool"]["container"] == "missing"
            assert found["Tool"]["not_installed"].startswith("not installed")
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        assert admin_client(app).get(f"{API}/settings/docker").json()["configured"] is False
