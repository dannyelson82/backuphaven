"""Whole backup runs against a fake Unraid (ARCHITECTURE.md §3, §6.5)."""

import json
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest
import zstandard
from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database, DbCheck, Run, RunApp
from backuphaven.plugins.registry import bundled
from backuphaven.runner import NoSuchAppsError, Runner, remove_leftovers
from tests.helpers import API, admin_client
from tests.test_discovery import template_xml


def make_db(path: Path, rows: int = 50, keep_open: bool = False) -> sqlite3.Connection | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    if keep_open:
        return con
    con.close()
    return None


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def unraid(tmp_path: Path) -> Settings:
    """Templates on the flash drive and two data shares, like the owner's server."""
    templates = tmp_path / "boot" / "templates-user"
    templates.mkdir(parents=True)
    root = tmp_path / "mnt-user"
    (templates / "my-sonarr.xml").write_text(
        template_xml(
            "sonarr",
            "lscr.io/linuxserver/sonarr",
            ("/config", "/mnt/cache/fast/sonarr", "rw"),
            ("/tv", "/mnt/user/media/tv", "rw"),
        )
    )
    (templates / "my-plex.xml").write_text(
        template_xml(
            "plex", "lscr.io/linuxserver/plex", ("/config", "/mnt/user/appdata/plex", "rw")
        )
    )
    (templates / "my-tool.xml").write_text(
        template_xml("Tool", "someone/tool", ("/data/config", "/mnt/user/appdata/tool", "rw"))
    )
    (templates / "my-elsewhere.xml").write_text(
        template_xml("elsewhere", "someone/x", ("/config", "/mnt/user/other/x", "rw"))
    )
    sonarr = root / "fast" / "sonarr"
    write(sonarr / "config.xml", "<Config><ApiKey>abc</ApiKey></Config>")
    make_db(sonarr / "sonarr.db")
    write(sonarr / "logs" / "sonarr.txt", "log")
    pms = root / "appdata" / "plex" / "Library" / "Application Support" / "Plex Media Server"
    write(pms / "Preferences.xml", "<Preferences/>")
    make_db(pms / "Plug-in Support" / "Databases" / "com.plexapp.plugins.library.db", rows=500)
    write(pms / "Cache" / "huge.bin", "cache")
    make_db(root / "appdata" / "tool" / "state.sqlite")
    write(root / "appdata" / "tool" / "settings.json", "{}")
    (root / "media" / "tv").mkdir(parents=True)
    return Settings(
        config_dir=tmp_path / "config",
        backups_dir=tmp_path / "backups",
        templates_dir=templates,
        data_root=root,
        min_free_bytes=0,
    )


@pytest.fixture
def db(unraid: Settings) -> Database:
    database = Database(unraid.config_dir / "backuphaven.db")
    database.migrate()
    return database


def contents(archive: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with archive.open("rb") as handle:
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for info in tar:
                data = tar.extractfile(info)
                out[info.name] = data.read() if data else b""
    return out


def test_a_full_run(unraid: Settings, db: Database, tmp_path: Path) -> None:
    run_id = Runner(db, unraid, bundled()).run_now()
    with db.read() as session:
        run = session.get(Run, run_id)
        apps = {a.app: a for a in session.query(RunApp).filter_by(run_id=run_id)}
        checks = {(c.app, c.path): c for c in session.query(DbCheck).filter_by(run_id=run_id)}
    assert run is not None and run.verified and run.archive
    # The app on a share BackupHaven can't see makes the run a warning, not a failure.
    assert run.status == "warning"
    assert apps["elsewhere"].status == "warning"
    assert "'other' share" in apps["elsewhere"].problems["warnings"][0]  # type: ignore[index]
    assert {apps[n].status for n in ("sonarr", "plex", "Tool")} == {"ok"}
    assert {apps[n].plugin for n in ("sonarr", "plex", "Tool")} == {"sonarr", "plex", "generic"}

    archive = unraid.backups_dir / run.archive
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    leftovers = [p.name for p in unraid.backups_dir.iterdir()]
    assert sorted(leftovers) == sorted([archive.name, archive.name + ".manifest.json"])

    files = contents(archive)
    assert files["apps/sonarr/files/config/config.xml"].startswith(b"<Config>")
    assert "apps/sonarr/files/config/logs/sonarr.txt" not in files
    assert "apps/sonarr/template.xml" in files
    plex = "apps/plex/files/config/Library/Application Support/Plex Media Server"
    assert f"{plex}/Preferences.xml" in files and f"{plex}/Cache/huge.bin" not in files
    assert "apps/Tool/files/data_config/settings.json" in files
    db_copy = tmp_path / "restored.db"
    db_copy.write_bytes(
        files[
            "apps/plex/databases/config/Library/Application Support/Plex Media Server/"
            "Plug-in Support/Databases/com.plexapp.plugins.library.db"
        ]
    )
    con = sqlite3.connect(db_copy)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 500
    con.close()
    assert checks[("Tool", "state.sqlite")].status == "ok"
    assert checks[("sonarr", "sonarr.db")].mode == "full"
    timing = checks[("sonarr", "sonarr.db")].messages["timing"]  # type: ignore[index]
    assert timing["copy_method"] in ("live", "immutable")
    assert timing["copy_seconds"] >= 0 and timing["check_seconds"] >= 0
    manifest = json.loads(files["manifest.json"])
    assert [a["name"] for a in manifest["apps"]] == ["elsewhere", "plex", "sonarr", "Tool"]


def test_a_running_apps_latest_changes_are_included(unraid: Settings, db: Database) -> None:
    sonarr_db = unraid.data_root / "fast" / "sonarr" / "sonarr.db"
    sonarr_db.unlink()
    con = make_db(sonarr_db, rows=10, keep_open=True)
    assert con is not None
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("INSERT INTO t VALUES ('newest')")
    con.commit()
    try:
        run_id = Runner(db, unraid, bundled()).run_now()
    finally:
        con.close()
    with db.read() as session:
        check = session.query(DbCheck).filter_by(run_id=run_id, app="sonarr").one()
    assert check.status == "ok"


def test_a_damaged_database_fails_its_app_but_keeps_the_archive(
    unraid: Settings, db: Database
) -> None:
    broken = unraid.data_root / "appdata" / "tool" / "state.sqlite"
    con = sqlite3.connect(broken)
    con.execute("PRAGMA journal_mode=DELETE")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5000)])
    con.execute("CREATE INDEX t_x ON t(x)")
    con.commit()
    con.close()
    data = bytearray(broken.read_bytes())
    data[4096 * 3 : 4096 * 3 + 100] = b"\xff" * 100
    broken.write_bytes(bytes(data))
    run_id = Runner(db, unraid, bundled()).run_now()
    with db.read() as session:
        run = session.get(Run, run_id)
        tool = session.query(RunApp).filter_by(run_id=run_id, app="Tool").one()
        check = session.query(DbCheck).filter_by(run_id=run_id, app="Tool").one()
    assert run is not None and run.status == "failed" and run.verified and run.archive
    assert tool.status == "failed"
    assert check.status == "damaged"


def test_low_disk_space_stops_before_writing(unraid: Settings, db: Database) -> None:
    settings = unraid.model_copy(update={"min_free_bytes": 1 << 60})
    run_id = Runner(db, settings, bundled()).run_now()
    with db.read() as session:
        run = session.get(Run, run_id)
    assert run is not None and run.status == "failed" and run.archive is None
    assert "free space" in (run.error or "")
    assert list(settings.backups_dir.iterdir()) == []


def test_run_now_api_and_status(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        assert admin.get(f"{API}/status").json()["last_run"] is None
        # Nothing is backed up until apps are added to the schedule (ADR-0011).
        empty = admin.post(f"{API}/runs")
        assert empty.status_code == 409 and empty.json()["detail"] == "schedule_empty"
        assert admin.get(f"{API}/status").json()["missing_shares"] == []
        apps = ["elsewhere", "plex", "sonarr", "Tool"]
        assert admin.put(f"{API}/schedule/apps", json={"apps": apps}).status_code == 200
        run_id = admin.post(f"{API}/runs").json()["run_id"]
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        detail = admin.get(f"{API}/runs/{run_id}").json()
        assert detail["status"] == "warning" and detail["verified"]
        assert {a["app"] for a in detail["apps"]} == {"elsewhere", "plex", "sonarr", "Tool"}
        state = admin.get(f"{API}/status").json()
        assert state["last_good_run"]["id"] == run_id
        assert state["data_shares"] == ["appdata", "fast", "media"]
        assert state["missing_shares"] == [
            {"app": "elsewhere", "target": "/config", "share": "other"}
        ]
        assert {d["app"] for d in state["database_health"]} == {"plex", "sonarr", "Tool"}
        assert all("copy_seconds" in d["timing"] for d in state["database_health"])
        assert [r["id"] for r in admin.get(f"{API}/runs").json()] == [run_id]


def test_only_one_run_at_a_time(unraid: Settings, db: Database) -> None:
    runner = Runner(db, unraid, bundled())
    with runner._lock:  # as if a run were going
        assert runner.start("manual") is None


def test_runs_cut_off_by_a_restart_are_marked_failed(unraid: Settings, db: Database) -> None:
    with db.write() as session:
        session.add(Run(trigger="manual", status="running"))
    with TestClient(create_app(unraid, gateways=frozenset())):
        pass
    with db.read() as session:
        run = session.query(Run).one()
    assert run.status == "failed" and run.error == "interrupted by a restart"


def test_run_now_for_chosen_apps_only(unraid: Settings, db: Database) -> None:
    run_id = Runner(db, unraid, bundled()).run_now(only=["Sonarr", "Tool"])
    with db.read() as session:
        run = session.get(Run, run_id)
        apps = [a.app for a in session.query(RunApp).filter_by(run_id=run_id)]
    assert run is not None and run.status == "ok" and run.verified and run.archive
    assert sorted(apps) == ["Tool", "sonarr"]
    assert run.summary is not None and run.summary["only"] == ["Sonarr", "Tool"]
    files = contents(unraid.backups_dir / run.archive)
    assert not any(name.startswith("apps/plex/") for name in files)
    assert json.loads(files["manifest.json"])["only"] == ["Sonarr", "Tool"]


def test_run_now_rejects_apps_that_arent_there(unraid: Settings, db: Database) -> None:
    runner = Runner(db, unraid, bundled())
    with pytest.raises(NoSuchAppsError, match="nothere"):
        runner.run_now(only=["sonarr", "nothere"])
    with pytest.raises(NoSuchAppsError):
        runner.run_now(only=[])
    assert not runner.running
    with db.read() as session:
        assert session.query(Run).count() == 0


class _CancelDuringFirstApp(Runner):
    def _backup_app(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.cancel()  # as if Cancel were pressed while this app is being read
        return super()._backup_app(*args, **kwargs)


def test_cancel_stops_the_run_and_leaves_nothing_behind(unraid: Settings, db: Database) -> None:
    runner = _CancelDuringFirstApp(db, unraid, bundled())
    run_id = runner.run_now()
    with db.read() as session:
        run = session.get(Run, run_id)
    assert run is not None and run.status == "cancelled"
    assert run.archive is None and run.error is None and not run.verified
    assert list(unraid.backups_dir.iterdir()) == []
    assert not runner.cancel()  # nothing running any more
    # The next run isn't cancelled by the old request.
    assert Runner.run_now(runner, only=["Tool"]) != run_id


def test_run_api_choose_apps_and_cancel(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        bad = admin.post(f"{API}/runs", json={"apps": ["nothere"]})
        assert bad.status_code == 422 and "nothere" in bad.json()["detail"]
        assert admin.post(f"{API}/runs/cancel").status_code == 409
        run_id = admin.post(f"{API}/runs", json={"apps": ["plex"]}).json()["run_id"]
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        detail = admin.get(f"{API}/runs/{run_id}").json()
        assert [a["app"] for a in detail["apps"]] == ["plex"]
        assert detail["summary"]["only"] == ["plex"]


def test_leftovers_of_an_interrupted_run_are_removed_at_start(unraid: Settings) -> None:
    backups = unraid.backups_dir
    (backups / ".work" / "7").mkdir(parents=True)
    (backups / ".work" / "7" / "plex-0.db").write_text("snapshot")
    (backups / "backuphaven-2026-10-16T190000.tar.zst.partial").write_text("half")
    (backups / "backuphaven-2026-10-15T020000.tar.zst").write_text("a good backup")
    (backups / "my-notes.partial").write_text("not ours")
    with TestClient(create_app(unraid, gateways=frozenset())):
        pass
    assert sorted(p.name for p in backups.iterdir()) == [
        "backuphaven-2026-10-15T020000.tar.zst",
        "my-notes.partial",
    ]
    assert remove_leftovers(backups) == []


def test_an_app_whose_folder_is_gone_is_skipped_not_failed(unraid: Settings, db: Database) -> None:
    """Unraid keeps the template of a removed container (ADR-0011)."""
    (unraid.templates_dir / "my-old.xml").write_text(
        template_xml("old", "someone/old", ("/config", "/mnt/user/appdata/old", "rw"))
    )
    run_id = Runner(db, unraid, bundled()).run_now(only=["old", "Tool"])
    with db.read() as session:
        run = session.get(Run, run_id)
        apps = {a.app: a for a in session.query(RunApp).filter_by(run_id=run_id)}
    assert run is not None and run.status == "ok" and run.verified
    assert run.summary is not None and run.summary["skipped"] == 1
    assert apps["old"].status == "skipped" and apps["old"].files == 0
    assert "not installed" in apps["old"].problems["warnings"][0]  # type: ignore[index]
    assert apps["Tool"].status == "ok"
    files = contents(unraid.backups_dir / run.archive)  # type: ignore[operator]
    assert not any(name.startswith("apps/old/") for name in files)


def test_backup_schedule_list_and_apps_page_data(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        assert admin.get(f"{API}/schedule/apps").json() == {"apps": []}
        bad = admin.put(f"{API}/schedule/apps", json={"apps": ["nothere"]})
        assert bad.status_code == 422
        saved = admin.put(f"{API}/schedule/apps", json={"apps": ["Tool", "plex", "PLEX"]})
        assert saved.json() == {"apps": ["plex", "Tool"]}
        run_id = admin.post(f"{API}/runs").json()["run_id"]
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        found = admin.get(f"{API}/apps/discovered").json()
        assert found["scheduled"] == ["plex", "Tool"]
        apps = {a["name"]: a for a in found["apps"]}
        assert apps["plex"]["scheduled"] and not apps["sonarr"]["scheduled"]
        assert apps["plex"]["last_backup"]["run_id"] == run_id
        assert apps["plex"]["last_backup"]["status"] == "ok"
        assert apps["sonarr"]["last_backup"] is None
        assert apps["plex"]["not_installed"] is None


def test_scheduled_apps_whose_template_is_gone_are_reported_as_skipped(
    unraid: Settings,
) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        admin.put(f"{API}/schedule/apps", json={"apps": ["Tool", "sonarr"]})
        (unraid.templates_dir / "my-sonarr.xml").unlink()  # container and template removed
        # Still listed (so it can be removed by hand), and runs report it.
        assert admin.get(f"{API}/schedule/apps").json()["apps"] == ["sonarr", "Tool"]
        assert (
            admin.put(f"{API}/schedule/apps", json={"apps": ["sonarr", "Tool"]}).status_code == 200
        )
        run_id = admin.post(f"{API}/runs").json()["run_id"]
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        detail = admin.get(f"{API}/runs/{run_id}").json()
        assert detail["status"] == "ok"
        apps = {a["app"]: a for a in detail["apps"]}
        assert apps["sonarr"]["status"] == "skipped"
        assert "not found" in apps["sonarr"]["warnings"][0]
        assert apps["Tool"]["status"] == "ok"


def test_backups_started_in_the_same_second_get_different_names() -> None:
    from datetime import UTC, datetime

    from backuphaven.runner import archive_name

    now = datetime(2026, 10, 17, 3, 0, 0, tzinfo=UTC)
    taken = {"backuphaven-2026-10-17T030000.tar.zst", "backuphaven-2026-10-17T030000-2.tar.zst"}
    assert archive_name(now) == "backuphaven-2026-10-17T030000.tar.zst"
    assert archive_name(now, taken.__contains__) == "backuphaven-2026-10-17T030000-3.tar.zst"


def test_dashboard_totals_and_latest_health_of_each_scheduled_database(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        admin.put(f"{API}/schedule/apps", json={"apps": ["Tool", "sonarr"]})
        admin.post(f"{API}/runs")
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        # A test backup of one app afterwards doesn't hide the others' health.
        admin.post(f"{API}/runs", json={"apps": ["Tool"]})
        for _ in range(200):
            if not app.state.runner.running:
                break
            time.sleep(0.05)
        state = admin.get(f"{API}/status").json()
        assert [(d["app"], d["path"]) for d in state["database_health"]] == [
            ("sonarr", "sonarr.db"),
            ("Tool", "state.sqlite"),
        ]
        assert state["backups_count"] == 2 and state["backups_bytes"] > 0


def test_a_folder_inside_another_is_stored_once() -> None:
    from backuphaven.discovery import DataFolder
    from backuphaven.runner import _nested_once

    config = DataFolder("/config", "/mnt/user/appdata/abs", "appdata")
    metadata = DataFolder("/metadata", "/mnt/user/appdata/abs/metadata", "appdata")
    other = DataFolder("/data", "/mnt/user/appdata/abs-data", "appdata")
    assert _nested_once([config, metadata, other]) == [config, other]
