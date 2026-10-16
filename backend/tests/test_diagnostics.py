"""Diagnostics files, the developer option (Settings → Developer)."""

import json
import logging
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.diagnostics import KEEP_RUNS, Diagnostics
from backuphaven.logsetup import SecretRegistry, configure_logging
from tests.helpers import API, admin_client, client_at
from tests.test_runner import unraid  # noqa: F401 - fixture

DEV = f"{API}/settings/developer"


def _wait(app) -> None:  # type: ignore[no-untyped-def]
    for _ in range(200):
        if not app.state.runner.running:
            return
        time.sleep(0.05)


def test_switching_on_writes_debug_log_run_file_and_system_info(unraid: Settings) -> None:
    settings = unraid.model_copy(update={"diagnostics_dir": unraid.config_dir / "diag"})
    app = create_app(settings, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        view = admin.get(DEV).json()
        assert view["diagnostics"] is False and view["files"] == []
        assert view["folder"] == str(settings.diagnostics_dir)
        assert admin.put(DEV, json={"diagnostics": True}).json()["diagnostics"] is True
        run_id = admin.post(f"{API}/runs", json={"apps": ["plex"]}).json()["run_id"]
        _wait(app)
        names = {f["name"] for f in admin.get(DEV).json()["files"]}
        assert {"debug.log", "system.json", f"run-{run_id}.json"} <= names

    folder = settings.diagnostics_dir
    assert folder is not None
    lines = [json.loads(line) for line in (folder / "debug.log").read_text().splitlines()]
    messages = {line["message"] for line in lines}
    assert {"run starting", "app starting", "database copy starting", "database copied"} <= messages
    assert {"database check", "archive verified", "diagnostics files on"} <= messages
    run = json.loads((folder / f"run-{run_id}.json").read_text())
    assert run["only"] == ["plex"] and run["apps"][0]["name"] == "plex"
    assert run["apps"][0]["databases"][0]["timing"]["copy_seconds"] >= 0
    system = json.loads((folder / "system.json").read_text())
    assert system["data_shares"] == ["appdata", "fast", "media"]
    assert "sqlite" in system and "capabilities" in system
    assert oct((folder / "debug.log").stat().st_mode & 0o777) == "0o640"


def test_the_choice_is_kept_across_restarts_and_can_be_switched_off(unraid: Settings) -> None:
    settings = unraid.model_copy(update={"diagnostics_dir": unraid.config_dir / "diag"})
    app = create_app(settings, gateways=frozenset())
    with TestClient(app):
        admin_client(app).put(DEV, json={"diagnostics": True})
    app = create_app(settings, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        assert admin.get(DEV).json()["diagnostics"] is True
        assert admin.put(DEV, json={"diagnostics": False}).json()["diagnostics"] is False
        assert not app.state.diagnostics.enabled
    assert logging.getLogger("backuphaven").level == logging.NOTSET


def test_needs_login(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin_client(app)
        stranger = client_at(app, "203.0.113.5")
        assert stranger.get(DEV).status_code == 401
        assert stranger.put(DEV, json={"diagnostics": True}).status_code in (401, 403)


def test_only_the_newest_run_files_are_kept(tmp_path: Path, unraid: Settings) -> None:
    diagnostics = Diagnostics(unraid.model_copy(update={"diagnostics_dir": tmp_path / "d"}))
    diagnostics.enable()
    try:
        for run_id in range(1, KEEP_RUNS + 4):
            diagnostics.write_run(run_id, {"run_id": run_id})
    finally:
        diagnostics.disable()
    runs = sorted(int(p.stem[4:]) for p in (tmp_path / "d").glob("run-*.json"))
    assert runs == list(range(4, KEEP_RUNS + 4))


def test_secrets_never_reach_the_files(tmp_path: Path, unraid: Settings) -> None:
    SecretRegistry.register("supersecretvalue123")
    diagnostics = Diagnostics(unraid.model_copy(update={"diagnostics_dir": tmp_path / "d"}))
    diagnostics.enable()
    try:
        logging.getLogger("backuphaven.test").debug("key is supersecretvalue123")
        diagnostics.write_run(1, {"note": "apikey=supersecretvalue123"})
    finally:
        diagnostics.disable()
        SecretRegistry.unregister("supersecretvalue123")
    for name in ("debug.log", "run-1.json"):
        assert "supersecretvalue123" not in (tmp_path / "d" / name).read_text()


def test_debug_lines_stay_out_of_the_normal_log(tmp_path: Path, unraid: Settings) -> None:
    configure_logging("info", tmp_path / "logs", 1 << 20, 1)
    diagnostics = Diagnostics(unraid.model_copy(update={"diagnostics_dir": tmp_path / "d"}))
    diagnostics.enable()
    try:
        logging.getLogger("backuphaven.test").debug("only for diagnostics")
    finally:
        diagnostics.disable()
        for handler in logging.getLogger().handlers:
            handler.flush()
    assert "only for diagnostics" in (tmp_path / "d" / "debug.log").read_text()
    assert "only for diagnostics" not in (tmp_path / "logs" / "backuphaven.log").read_text()
