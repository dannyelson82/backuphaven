"""Restoring an app and undoing it (ARCHITECTURE.md §6.9, ADR-0007)."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backuphaven import retention
from backuphaven.config import Settings
from backuphaven.db import Database, Restore
from backuphaven.restore import ASIDE_DIR, RestoreError, Restorer, changing_files
from backuphaven.runner import Runner
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


def tree(root: Path) -> dict[str, bytes | str]:
    """Every file's bytes and every link's target, relative to ``root``."""
    out: dict[str, bytes | str] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            out[rel] = str(path.readlink()) if path.is_symlink() else path.read_bytes()
    return out


@pytest.fixture
def backed_up(unraid: Settings, db: Database) -> tuple[Runner, Restorer, int]:
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    runner = Runner(db, unraid, plugins_of(unraid))
    run_id = runner.run_now(only=["Tool", "plex", "sonarr"], scheduled=True)
    restorer = Restorer(
        db, unraid, plugins_of(unraid), runner.lock, quiet_seconds=0, sleep=lambda _s: None
    )
    return runner, restorer, run_id


def test_restore_puts_files_back_as_they_were_and_undo_reverses_it(
    unraid: Settings,
    db: Database,
    backed_up: tuple[Runner, Restorer, int],
) -> None:
    _runner, restorer, run_id = backed_up
    tool = unraid.data_root / "appdata" / "tool"
    as_backed_up = tree(tool)
    # Things go wrong after the backup.
    (tool / "settings.json").write_text('{"broken": true}')
    (tool / "state.sqlite").write_bytes(b"garbage")
    (tool / "new-file.txt").write_text("made later")
    broken = tree(tool)

    restore_id = restorer.run(run_id, "Tool")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "done", row.error if row else None
    assert row.undo_until is not None and row.undo_until > datetime.now(UTC) + timedelta(days=6)
    restored = tree(tool)
    assert restored["settings.json"] == as_backed_up["settings.json"]
    assert "new-file.txt" not in restored
    con = sqlite3.connect(tool / "state.sqlite")
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    con.close()
    aside = Path(row.detail["folders"][0]["aside"])  # type: ignore[index]
    assert ASIDE_DIR in aside.parts and tree(aside) == broken

    restorer.undo(restore_id)
    assert tree(tool) == broken
    assert not aside.exists()
    with db.read() as session:
        assert session.get(Restore, restore_id).status == "undone"  # type: ignore[union-attr]
    with pytest.raises(RestoreError, match="can't be undone"):
        restorer.undo(restore_id)


def test_plex_keeps_its_posters_and_caches_but_not_old_database_side_files(
    unraid: Settings, backed_up: tuple[Runner, Restorer, int]
) -> None:
    _runner, restorer, run_id = backed_up
    pms = (
        unraid.data_root
        / "appdata"
        / "plex"
        / "Library"
        / "Application Support"
        / "Plex Media Server"
    )
    (pms / "Metadata" / "Movies" / "a").mkdir(parents=True)
    (pms / "Metadata" / "Movies" / "a" / "poster.jpg").write_bytes(b"poster")
    db_dir = pms / "Plug-in Support" / "Databases"
    (db_dir / "com.plexapp.plugins.library.db-wal").write_bytes(b"old wal")
    restorer.run(run_id, "plex")
    assert (pms / "Metadata" / "Movies" / "a" / "poster.jpg").read_bytes() == b"poster"
    assert (pms / "Cache" / "huge.bin").read_text() == "cache"
    assert not (db_dir / "com.plexapp.plugins.library.db-wal").exists()
    con = sqlite3.connect(db_dir / "com.plexapp.plugins.library.db")
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 500
    con.close()


def test_an_app_that_is_still_running_is_refused_and_nothing_moves(
    unraid: Settings,
    db: Database,
    backed_up: tuple[Runner, Restorer, int],
) -> None:
    runner, _restorer, run_id = backed_up
    tool = unraid.data_root / "appdata" / "tool"
    before = tree(tool)

    def app_keeps_writing(_seconds: float) -> None:
        (tool / "settings.json").write_text(f'{{"tick": {datetime.now(UTC).timestamp()}}}')

    restorer = Restorer(
        db, unraid, plugins_of(unraid), runner.lock, quiet_seconds=0, sleep=app_keeps_writing
    )
    assert restorer.check(run_id, "Tool")["stopped"] is False
    restore_id = restorer.run(run_id, "Tool")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "failed" and "stop its container" in (row.error or "")
    assert set(tree(tool)) == set(before)
    assert not (unraid.data_root / "appdata" / ASIDE_DIR).exists() or not any(
        (unraid.data_root / "appdata" / ASIDE_DIR).iterdir()
    )


def test_a_damaged_backup_puts_everything_back(
    unraid: Settings,
    db: Database,
    backed_up: tuple[Runner, Restorer, int],
) -> None:
    _runner, restorer, run_id = backed_up
    tool = unraid.data_root / "appdata" / "tool"
    (tool / "settings.json").write_text("current")
    before = tree(tool)
    archive = next(unraid.backups_dir.glob("backuphaven-*.tar.zst"))
    sidecar = archive.with_name(archive.name + ".manifest.json")
    manifest = json.loads(sidecar.read_text())
    for entry in manifest["entries"]:
        if entry["path"].endswith("settings.json"):
            entry["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(manifest))
    restore_id = restorer.run(run_id, "Tool")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "failed" and "checksum" in (row.error or "")
    assert tree(tool) == before


def test_set_aside_folders_are_deleted_when_undo_runs_out(
    unraid: Settings,
    db: Database,
    backed_up: tuple[Runner, Restorer, int],
) -> None:
    _runner, restorer, run_id = backed_up
    restore_id = restorer.run(run_id, "Tool")
    with db.read() as session:
        aside = Path(session.get(Restore, restore_id).detail["folders"][0]["aside"])  # type: ignore[union-attr,index]
    assert aside.exists()
    assert restorer.expire(datetime.now(UTC) + timedelta(days=1)) == []
    assert restorer.expire(datetime.now(UTC) + timedelta(days=8)) == [restore_id]
    assert not aside.exists()
    with pytest.raises(RestoreError):
        restorer.undo(restore_id)


def test_no_restore_during_a_backup(backed_up: tuple[Runner, Restorer, int]) -> None:
    runner, restorer, run_id = backed_up
    with runner.lock, pytest.raises(RestoreError, match="running"):
        restorer.start(run_id, "Tool", "admin")


def test_unknown_apps_and_backups_are_refused(backed_up: tuple[Runner, Restorer, int]) -> None:
    _runner, restorer, run_id = backed_up
    with pytest.raises(RestoreError, match="isn't in this backup"):
        restorer.run(run_id, "nothere")
    with pytest.raises(RestoreError, match="isn't available"):
        restorer.run(9999, "Tool")


def test_a_backup_never_writes_into_data_shares(unraid: Settings, db: Database) -> None:
    """ADR-0007: writes into data shares only during a restore."""

    def snapshot() -> dict[str, tuple[int, int]]:
        out = {}
        for dirpath, dirs, files in os.walk(unraid.data_root):
            for name in [*dirs, *files]:
                info = (Path(dirpath) / name).lstat()
                out[str(Path(dirpath) / name)] = (info.st_size, info.st_mtime_ns)
        return out

    before = snapshot()
    Runner(db, unraid, plugins_of(unraid)).run_now(only=["Tool", "plex", "sonarr"], scheduled=True)
    assert snapshot() == before


def test_changing_files_sees_new_changed_and_removed_files(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a")
    (tmp_path / "b").write_text("b")

    def meddle(_s: float) -> None:
        (tmp_path / "a").write_text("changed!")
        (tmp_path / "b").unlink()
        (tmp_path / "c").write_text("new")

    changed = changing_files([tmp_path], 0, meddle)
    assert sorted(Path(p).name for p in changed) == ["a", "b", "c"]
    assert changing_files([tmp_path], 0, lambda _s: None) == []


def test_restore_api(unraid: Settings) -> None:
    import time

    from fastapi.testclient import TestClient

    from backuphaven.app import create_app
    from tests.helpers import API, admin_client

    app = create_app(unraid.model_copy(update={"restore_quiet_s": 0}), gateways=frozenset())
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
        body = {"run_id": run_id, "app": "Tool"}
        check = admin.post(f"{API}/restores/check", json=body).json()
        assert check["stopped"] is True
        assert check["folders"] == [
            {
                "target": "/data/config",
                "host": "/mnt/user/appdata/tool",
                "exists": True,
                "in_backup": True,
            }
        ]
        assert admin.post(f"{API}/restores", json=body).status_code == 409  # not confirmed
        started = admin.post(f"{API}/restores", json={**body, "confirmed_stopped": True})
        assert started.status_code == 202
        restore_id = started.json()["restore_id"]
        for _ in range(200):
            if admin.get(f"{API}/restores/current").json() is None:
                break
            time.sleep(0.05)
        row = admin.get(f"{API}/restores/{restore_id}").json()
        assert row["status"] == "done" and row["actor"] == "admin"
        assert [r["id"] for r in admin.get(f"{API}/restores").json()] == [restore_id]
        assert admin.post(f"{API}/restores/{restore_id}/undo").json()["status"] == "undone"
        assert admin.post(f"{API}/restores/{restore_id}/undo").status_code == 409
        bad = admin.post(f"{API}/restores/check", json={"run_id": run_id, "app": "nope"})
        assert bad.status_code == 409
