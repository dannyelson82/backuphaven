"""Keep rules applied to real backup files (ARCHITECTURE.md §6.8)."""

from pathlib import Path

from backuphaven import retention
from backuphaven.config import Settings
from backuphaven.db import Database, Run
from backuphaven.runner import Runner
from tests.test_runner import db, plugins_of, unraid  # noqa: F401 - fixtures


def files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tar.zst"))


def keep(db: Database, **values: float) -> None:
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings.model_validate(values))


def test_old_backups_are_deleted_after_a_run(unraid: Settings, db: Database) -> None:
    keep(db, nightly=1, weekly=0, monthly=0, min_free_gb=0)
    runner = Runner(db, unraid, plugins_of(unraid))
    first = runner.run_now(only=["Tool"], scheduled=True)
    (unraid.backups_dir / "my-own-notes.tar.zst").write_text("not ours")
    second = runner.run_now(only=["Tool"], scheduled=True)
    with db.read() as session:
        old, new = session.get(Run, first), session.get(Run, second)
    assert old is not None and new is not None
    assert old.deleted_at is not None and new.deleted_at is None
    assert files(unraid.backups_dir) == sorted([str(new.archive), "my-own-notes.tar.zst"])
    assert not (unraid.backups_dir / f"{old.archive}.manifest.json").exists()


def test_an_app_taken_out_of_the_schedule_keeps_its_last_good_copy(
    unraid: Settings,
    db: Database,
) -> None:
    keep(db, nightly=1, weekly=0, monthly=0, min_free_gb=0)
    runner = Runner(db, unraid, plugins_of(unraid))
    both = runner.run_now(only=["Tool", "sonarr"], scheduled=True)
    newer = runner.run_now(only=["sonarr"], scheduled=True)
    with db.read() as session:
        first, second = session.get(Run, both), session.get(Run, newer)
    assert first is not None and second is not None and second.archive != first.archive
    assert first.deleted_at is None
    reasons = retention.reasons(db, "Etc/UTC")
    assert reasons[both] == ["last good copy of Tool/state.sqlite"]
    assert "nightly" in reasons[newer]


def test_the_owners_minimum_free_space_stops_a_run(unraid: Settings, db: Database) -> None:
    keep(db, min_free_gb=100_000)  # 100 TB: more than any test disk
    run_id = Runner(db, unraid, plugins_of(unraid)).run_now(only=["Tool"])
    with db.read() as session:
        run = session.get(Run, run_id)
    assert run is not None and run.status == "failed" and "free space" in (run.error or "")


def test_only_backuphaven_files_can_be_deleted(tmp_path: Path) -> None:
    import pytest

    for name in ("../etc/passwd", "notes.txt", "backuphaven-x/../../y.tar.zst"):
        with pytest.raises(ValueError):
            retention.delete_backup(tmp_path, Run(trigger="manual", status="ok", archive=name))
    target = tmp_path / "elsewhere.tar.zst"
    target.write_text("precious")
    (tmp_path / "backuphaven-2026.tar.zst").symlink_to(target)
    with pytest.raises(ValueError, match="link"):
        retention.delete_backup(
            tmp_path, Run(trigger="manual", status="ok", archive="backuphaven-2026.tar.zst")
        )
    assert target.read_text() == "precious"


def test_keeping_settings_api(unraid: Settings) -> None:
    from fastapi.testclient import TestClient

    from backuphaven.app import create_app
    from tests.helpers import API, admin_client

    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        assert admin.get(f"{API}/settings/keeping").json() == {
            "nightly": 7,
            "weekly": 4,
            "monthly": 6,
            "min_free_gb": 2.0,
        }
        for bad in ({"nightly": -1}, {"monthly": 500}, {"min_free_gb": -5}):
            assert admin.put(f"{API}/settings/keeping", json=bad).status_code == 422
        new = {"nightly": 14, "weekly": 8, "monthly": 12, "min_free_gb": 50}
        assert admin.put(f"{API}/settings/keeping", json=new).json()["nightly"] == 14
        assert admin.get(f"{API}/settings/keeping").json()["min_free_gb"] == 50
