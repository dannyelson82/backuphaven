"""Database dumps (ADR-0014): with fake tools everywhere, with real servers in CI."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from backuphaven import dbdump
from backuphaven.dbdump import DumpError, Target


def fake_tool(bin_dir: Path, name: str, script: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fakebin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


TARGET = Target("mariadb", "db.example", 3306, "romm", "romm-user", "s3cret-pass-1")


def test_mariadb_dump_with_password_in_the_environment_only(fakebin: Path, tmp_path: Path) -> None:
    fake_tool(
        fakebin,
        "mariadb-dump",
        'echo "args: $*"\necho "pwd: $MYSQL_PWD"\necho "-- Dump completed on 2026-10-17"\n',
    )
    result = dbdump.dump(TARGET, tmp_path / "romm.sql")
    text = (tmp_path / "romm.sql").read_text()
    assert "--single-transaction" in text and "--host=db.example" in text and "-- romm" in text
    assert "s3cret-pass-1" not in text.split("pwd:")[0]  # not in the arguments
    assert "pwd: s3cret-pass-1" in text  # but given through MYSQL_PWD
    assert result.size > 0


def test_an_incomplete_dump_is_refused_and_removed(fakebin: Path, tmp_path: Path) -> None:
    fake_tool(fakebin, "mariadb-dump", 'echo "CREATE TABLE x (id int);"\n')
    with pytest.raises(DumpError, match="incomplete"):
        dbdump.dump(TARGET, tmp_path / "romm.sql")
    assert not (tmp_path / "romm.sql").exists()


def test_tool_errors_are_short_and_never_show_the_password(fakebin: Path, tmp_path: Path) -> None:
    fake_tool(
        fakebin,
        "mariadb-dump",
        'echo "Access denied for user romm-user (using password s3cret-pass-1)" >&2\nexit 2\n',
    )
    with pytest.raises(DumpError) as info:
        dbdump.dump(TARGET, tmp_path / "romm.sql")
    assert "Access denied" in str(info.value) and "s3cret-pass-1" not in str(info.value)


def test_missing_tools_and_timeouts(
    fakebin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(fakebin))
    with pytest.raises(DumpError, match="isn't installed"):
        dbdump.dump(TARGET, tmp_path / "x.sql")
    fake_tool(fakebin, "mariadb-dump", "exec /bin/sleep 5\n")
    with pytest.raises(DumpError, match="took longer"):
        dbdump.dump(TARGET, tmp_path / "x.sql", timeout=0.5)


@pytest.mark.parametrize("bad", ["-e", "db\nDROP", ""])
def test_values_that_could_be_options_are_refused(bad: str, tmp_path: Path) -> None:
    with pytest.raises(DumpError, match="isn't usable"):
        dbdump.dump(Target("mariadb", "h", 3306, bad, "u"), tmp_path / "x.sql")


def test_postgres_dump_is_checked_with_pg_restore(fakebin: Path, tmp_path: Path) -> None:
    fake_tool(
        fakebin,
        "pg_dump",
        'for a in "$@"; do case "$a" in --file=*) echo dump > "${a#--file=}";; esac; done\n',
    )
    fake_tool(
        fakebin, "pg_restore", 'echo "$PGPASSWORD" > "$(dirname "$2")/seen"; [ "$1" = "--list" ]\n'
    )
    target = Target("postgres", "pg.example", 5432, "immich", "postgres", "pg-pass-xyz")
    dbdump.dump(target, tmp_path / "immich.dump")
    assert (tmp_path / "immich.dump").read_text() == "dump\n"
    assert (tmp_path / "seen").read_text().strip() == "pg-pass-xyz"


# --- real servers (CI service containers; skipped elsewhere) ------------------------------


def _server(var: str) -> tuple[str, int, str, str]:
    value = os.environ.get(var)
    if not value:
        pytest.skip(f"{var} not set (CI runs real database servers)")
    host, port, user, password = value.split(":", 3)
    return host, int(port), user, password


def _client(target: Target, statement: str) -> str:
    """Run SQL with the server's own command-line client."""
    if target.engine == "mariadb":
        args = [
            "mariadb",
            f"--host={target.host}",
            f"--port={target.port}",
            f"--user={target.user}",
            "--protocol=TCP",
            "--batch",
            "--skip-column-names",
            f"--execute={statement}",
            target.database,
        ]
        env = {**os.environ, "MYSQL_PWD": target.password}
    else:
        args = [
            "psql",
            f"--host={target.host}",
            f"--port={target.port}",
            f"--username={target.user}",
            "--no-password",
            "--tuples-only",
            "--no-align",
            f"--command={statement}",
            target.database,
        ]
        env = {**os.environ, "PGPASSWORD": target.password}
    done = subprocess.run(args, env=env, capture_output=True, text=True, check=True)  # noqa: S603
    return done.stdout


SETUP = (
    "DROP TABLE IF EXISTS games; "
    "CREATE TABLE games (id INT PRIMARY KEY, name TEXT); "
    "INSERT INTO games VALUES (1, 'Zelda'), (2, 'Metroid')"
)


def test_real_mariadb_round_trip(tmp_path: Path) -> None:
    host, port, user, password = _server("BH_TEST_MARIADB")
    target = Target("mariadb", host, port, "bhtest", user, password)
    _client(target, "DROP TABLE IF EXISTS later; " + SETUP)
    dbdump.dump(target, tmp_path / "bhtest.sql")
    _client(target, "INSERT INTO games VALUES (3, 'later'); CREATE TABLE later (x INT)")
    dbdump.load(target, tmp_path / "bhtest.sql")
    assert _client(target, "SELECT name FROM games ORDER BY id").split() == ["Zelda", "Metroid"]
    assert "later" not in _client(target, "SHOW TABLES").split()


def test_real_postgres_round_trip(tmp_path: Path) -> None:
    host, port, user, password = _server("BH_TEST_POSTGRES")
    target = Target("postgres", host, port, "bhtest", user, password)
    _client(target, SETUP)
    dbdump.dump(target, tmp_path / "bhtest.dump")
    _client(target, "INSERT INTO games VALUES (3, 'later')")
    dbdump.load(target, tmp_path / "bhtest.dump")
    assert _client(target, "SELECT name FROM games ORDER BY id").split() == ["Zelda", "Metroid"]
