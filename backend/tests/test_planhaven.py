"""PlanHaven: its own database dumps and an attachments share (whole share)."""

from pathlib import Path

import pytest

from backuphaven import discovery, retention
from backuphaven.config import Settings
from backuphaven.db import Database, Restore, RunApp
from backuphaven.restore import ASIDE_DIR, Restorer
from backuphaven.runner import Runner
from tests.test_discovery import template_xml
from tests.test_restore import tree
from tests.test_runner import contents, db, plugins_of, unraid  # noqa: F401 - fixtures


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def planhaven(unraid: Settings, db: Database) -> Settings:
    (unraid.templates_dir / "my-planhaven.xml").write_text(
        template_xml(
            "planhaven",
            "ghcr.io/dannyelson82/planhaven:latest",
            ("/config", "/mnt/user/appdata/planhaven", "rw"),
            ("/data", "/mnt/user/planhaven", "rw"),
        )
    )
    config = unraid.data_root / "appdata" / "planhaven"
    write(config / "secrets" / "app.key", "secret")
    write(config / "backups" / "planhaven-20261016-030000.dump", "PGDMP dump")
    write(config / "pgdata" / "PG_VERSION", "18")
    write(config / "pgdata" / "global" / "pg_control", "live")
    write(config / "logs" / "app.log", "log")
    share = unraid.data_root / "planhaven"
    write(share / "attachments" / "a1" / "receipt.pdf", "pdf")
    write(share / "attachments" / "a2" / "photo.jpg", "jpg")
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    return unraid


def test_discovery_keeps_whole_shares_for_plugins_to_claim(planhaven: Settings) -> None:
    [app] = [
        a
        for a in discovery.discover(planhaven.templates_dir, planhaven.data_root)
        if a.name == "planhaven"
    ]
    assert [f.target for f in app.folders] == ["/config"]
    assert [(w.target, w.share, w.mounted) for w in app.whole_shares] == [
        ("/data", "planhaven", True)
    ]
    claimed = discovery.with_plugin_shares(app, ["/data"])
    assert [f.host for f in claimed.folders] == [
        "/mnt/user/appdata/planhaven",
        "/mnt/user/planhaven",
    ]
    unmounted = discovery.with_plugin_shares(
        discovery.App(
            app.name,
            app.image,
            app.folders,
            (),
            None,
            (discovery.WholeShare("/data", "/mnt/user/planhaven", "planhaven", False),),
        ),
        ["/data"],
    )
    assert [m.share for m in unmounted.missing] == ["planhaven"]


def test_backup_takes_settings_dumps_and_attachments_but_not_live_pgdata(
    planhaven: Settings, db: Database
) -> None:
    run_id = Runner(db, planhaven, plugins_of(planhaven)).run_now(
        only=["planhaven"], scheduled=True
    )
    with db.read() as session:
        app = session.query(RunApp).filter_by(run_id=run_id).one()
    assert app.status == "ok", app.problems
    archive = next(planhaven.backups_dir.glob("backuphaven-*.tar.zst"))
    names = set(contents(archive))
    base = "apps/planhaven/files"
    assert f"{base}/config/secrets/app.key" in names
    assert f"{base}/config/backups/planhaven-20261016-030000.dump" in names
    assert f"{base}/data/attachments/a1/receipt.pdf" in names
    assert not any("/pgdata/" in n or "/logs/" in n for n in names)


def test_missing_dumps_fail_the_backup(planhaven: Settings, db: Database) -> None:
    (
        planhaven.data_root / "appdata" / "planhaven" / "backups" / "planhaven-20261016-030000.dump"
    ).unlink()
    run_id = Runner(db, planhaven, plugins_of(planhaven)).run_now(
        only=["planhaven"], scheduled=True
    )
    with db.read() as session:
        app = session.query(RunApp).filter_by(run_id=run_id).one()
    assert app.status == "failed"
    assert "backups/planhaven-*.dump" in app.problems["errors"][0]  # type: ignore[index]


def test_a_database_server_folder_in_any_app_is_flagged(unraid: Settings, db: Database) -> None:
    tool = unraid.data_root / "appdata" / "tool"
    write(tool / "db" / "PG_VERSION", "16")
    write(tool / "db" / "global" / "pg_control", "x")
    run_id = Runner(db, unraid, plugins_of(unraid)).run_now(only=["Tool"], scheduled=True)
    with db.read() as session:
        app = session.query(RunApp).filter_by(run_id=run_id).one()
    assert app.status == "warning"
    assert "db holds a PostgreSQL database" in app.problems["warnings"][0]  # type: ignore[index]


def test_restore_brings_back_attachments_and_keeps_the_live_database(
    planhaven: Settings, db: Database
) -> None:
    runner = Runner(db, planhaven, plugins_of(planhaven))
    run_id = runner.run_now(only=["planhaven"], scheduled=True)
    share = planhaven.data_root / "planhaven"
    config = planhaven.data_root / "appdata" / "planhaven"
    before_share, before_config = tree(share), tree(config)
    # Afterwards: an attachment deleted, one added, a secret changed, Postgres moved on.
    (share / "attachments" / "a1" / "receipt.pdf").unlink()
    write(share / "attachments" / "a3" / "new.pdf", "new")
    write(config / "secrets" / "app.key", "changed")
    write(config / "pgdata" / "global" / "pg_control", "newer live state")
    broken_share, broken_config = tree(share), tree(config)

    restorer = Restorer(
        db, planhaven, plugins_of(planhaven), runner.lock, quiet_seconds=0, sleep=lambda _s: None
    )
    restore_id = restorer.run(run_id, "planhaven")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "done", row.error if row else None
    restored_share = tree(share)
    assert {k: v for k, v in restored_share.items() if not k.startswith(ASIDE_DIR)} == before_share
    assert tree(config)["secrets/app.key"] == b"secret"
    # The live database wasn't in the backup: the current one stays.
    assert tree(config)["pgdata/global/pg_control"] == b"newer live state"
    assert before_config["pgdata/global/pg_control"] == b"live"

    restorer.undo(restore_id)
    assert tree(share) == broken_share
    assert tree(config) == broken_config
    assert not (share / ASIDE_DIR).exists() or not any((share / ASIDE_DIR).iterdir())


def test_backups_never_include_set_aside_folders(planhaven: Settings, db: Database) -> None:
    write(planhaven.data_root / "planhaven" / ASIDE_DIR / "old" / "x.pdf", "old")
    Runner(db, planhaven, plugins_of(planhaven)).run_now(only=["planhaven"], scheduled=True)
    archive = next(planhaven.backups_dir.glob("backuphaven-*.tar.zst"))
    assert not any(ASIDE_DIR in n for n in contents(archive))
