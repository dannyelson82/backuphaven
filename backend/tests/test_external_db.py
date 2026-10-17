"""Apps with a database in another container (ADR-0014), end to end with a fake
MariaDB: a text file per database, and scripts in place of its tools."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backuphaven import discovery, retention, templating
from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database, Restore, RunApp
from backuphaven.restore import RESTORE_DUMPS, Restorer
from backuphaven.runner import Runner
from tests.helpers import API, admin_client
from tests.test_dbdump import fake_tool
from tests.test_runner import contents, db, plugins_of, unraid  # noqa: F401 - fixtures

PLUGIN = """
id: romm
name: RomM
version: 1.0.0
kind: app
plugin_api: 1
author: tests
recognise: {images: ["*/romm"]}
backup:
  folder: /romm/config
  include: ["**"]
  external_databases:
    - id: main
      engine: mariadb
      host: "${DB_HOST}"
      port: "${DB_PORT:-3306}"
      database: "${DB_NAME:-romm}"
      user: "${DB_USER:-romm-user}"
      password: "${DB_PASSWD}"
"""

TEMPLATE = """<?xml version="1.0"?>
<Container version="2">
  <Name>romm</Name>
  <Repository>rommapp/romm:latest</Repository>
  <Config Name="Config" Target="/romm/config" Default="" Mode="rw"
    Type="Path">/mnt/user/appdata/romm</Config>
  <Config Name="DB host" Target="DB_HOST" Default="" Mode="" Type="Variable">mariadb</Config>
  <Config Name="DB password" Target="DB_PASSWD" Default="" Mode=""
    Type="Variable">db-secret-77</Config>
</Container>
"""


@pytest.fixture
def romm(unraid: Settings, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A RomM app and a fake MariaDB; returns the file holding the 'romm' database."""
    (unraid.config_dir / "plugins" / "romm.yaml").write_text(PLUGIN)
    (unraid.templates_dir / "my-romm.xml").write_text(TEMPLATE)
    (unraid.data_root / "appdata" / "romm").mkdir(parents=True)
    (unraid.data_root / "appdata" / "romm" / "config.yml").write_text("exclude: []")
    store = tmp_path / "fakedb"
    store.mkdir()
    (store / "romm").write_text("games: Zelda, Metroid\n")
    bin_dir = tmp_path / "bin"
    # The last argument is the database; the "database" is store/<name>.
    fake_tool(
        bin_dir,
        "mariadb-dump",
        f'for a; do db=$a; done\necho "host=$1" > {store}/last-dump-args\n'
        f'[ "$MYSQL_PWD" = "db-secret-77" ] || exit 9\ncat {store}/$db\necho "-- Dump completed"\n',
    )
    fake_tool(
        bin_dir,
        "mariadb",
        "for a; do db=$a; case $a in --execute=*) q=1;; esac; done\n"
        '[ -n "$q" ] && exit 0\n'
        f"grep -v -- '-- Dump completed' > {store}/incoming\n"
        f"grep -q BAD {store}/incoming && exit 3\n"
        f"mv {store}/incoming {store}/$db\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    with db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    return store / "romm"


def test_template_values_fill_the_connection(unraid: Settings, romm: Path) -> None:
    [app] = [
        a for a in discovery.discover(unraid.templates_dir, unraid.data_root) if a.name == "romm"
    ]
    assert app.variables["DB_PASSWD"] == "db-secret-77"
    assert "db-secret-77" not in repr(app)  # never in logs through repr
    assert templating.fill("${DB_PORT:-3306}", app.variables) == "3306"
    with pytest.raises(templating.MissingVariableError):
        templating.fill("${NOPE}", {})


def test_backup_restore_and_undo_with_the_database(
    unraid: Settings, db: Database, romm: Path
) -> None:
    runner = Runner(db, unraid, plugins_of(unraid))
    run_id = runner.run_now(only=["romm"], scheduled=True)
    with db.read() as session:
        app = session.query(RunApp).filter_by(run_id=run_id).one()
    assert app.status == "ok", app.problems
    archive = next(unraid.backups_dir.glob("backuphaven-*.tar.zst"))
    stored = contents(archive)["apps/romm/external/main.sql"]
    assert b"games: Zelda, Metroid" in stored and b"-- Dump completed" in stored
    assert "host=--host=mariadb" in (romm.parent / "last-dump-args").read_text()

    romm.write_text("games: Zelda, Metroid, a game added later\n")
    restorer = Restorer(
        db, unraid, plugins_of(unraid), runner.lock, quiet_seconds=0, sleep=lambda _s: None
    )
    restore_id = restorer.run(run_id, "romm")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "done", row.error if row else None
    assert romm.read_text() == "games: Zelda, Metroid\n"
    pre = Path(row.detail["external"][0]["pre_restore"])  # type: ignore[index]
    assert pre.parent.parent.name == RESTORE_DUMPS and b"added later" in pre.read_bytes()

    restorer.undo(restore_id)
    assert romm.read_text() == "games: Zelda, Metroid, a game added later\n"


def test_a_failed_load_puts_the_database_and_files_back(
    unraid: Settings, db: Database, romm: Path
) -> None:
    romm.write_text("BAD dump content\n")
    runner = Runner(db, unraid, plugins_of(unraid))
    run_id = runner.run_now(only=["romm"], scheduled=True)
    romm.write_text("current data\n")
    restorer = Restorer(
        db, unraid, plugins_of(unraid), runner.lock, quiet_seconds=0, sleep=lambda _s: None
    )
    restore_id = restorer.run(run_id, "romm")
    with db.read() as session:
        row = session.get(Restore, restore_id)
    assert row is not None and row.status == "failed" and "mariadb failed" in (row.error or "")
    assert romm.read_text() == "current data\n"
    assert (unraid.data_root / "appdata" / "romm" / "config.yml").read_text() == "exclude: []"


def test_unreachable_or_unset_database_fails_the_app_with_advice(
    unraid: Settings, db: Database, romm: Path
) -> None:
    (unraid.templates_dir / "my-romm.xml").write_text(TEMPLATE.replace(">mariadb<", "><"))
    run_id = Runner(db, unraid, plugins_of(unraid)).run_now(only=["romm"], scheduled=True)
    with db.read() as session:
        app = session.query(RunApp).filter_by(run_id=run_id).one()
    assert app.status == "failed"
    assert "doesn't set DB_HOST" in app.problems["errors"][0]  # type: ignore[index]
    assert "set it on the Apps page" in app.problems["errors"][0]  # type: ignore[index]


def test_address_override_api(unraid: Settings, romm: Path) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        found = {a["name"]: a for a in admin.get(f"{API}/apps/discovered").json()["apps"]}
        assert found["romm"]["external_databases"] == ["main"]
        [view] = admin.get(f"{API}/apps/romm/databases").json()
        assert view["host"] == "mariadb" and view["port"] == 3306 and view["from_template"] is True
        assert "db-secret-77" not in admin.get(f"{API}/apps/romm/databases").text
        [view] = admin.put(
            f"{API}/apps/romm/databases/main", json={"host": "192.168.50.10", "port": 3307}
        ).json()
        assert (view["host"], view["port"], view["from_template"]) == ("192.168.50.10", 3307, False)
        assert (
            admin.put(
                f"{API}/apps/romm/databases/main", json={"host": "-bad host", "port": 1}
            ).status_code
            == 422
        )
        assert (
            admin.put(f"{API}/apps/romm/databases/nope", json={"host": "x", "port": 1}).status_code
            == 404
        )
        [view] = admin.delete(f"{API}/apps/romm/databases/main").json()
        assert view["host"] == "mariadb" and view["from_template"] is True
