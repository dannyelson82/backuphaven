"""Dumping and loading databases that live in other containers (ADR-0014).

BackupHaven connects to the database server as a client and uses the server's own
tools, run as argument lists (never a shell):

- **MariaDB/MySQL:** ``mariadb-dump --single-transaction`` (a consistent copy while
  the apps keep writing, for InnoDB tables). A dump is complete only when its last
  line is the tool's ``-- Dump completed`` marker. Loading first drops the
  database's tables, so tables created after the backup don't linger.
- **PostgreSQL:** ``pg_dump --format=custom`` (always consistent). A dump is
  complete when ``pg_restore --list`` can read its table of contents. Loading uses
  ``pg_restore --clean --if-exists``.

Passwords reach the tools through the environment (``MYSQL_PWD``,
``PGPASSWORD``), never the command line, and are registered for log redaction.
Every tool runs with a timeout; its error output is kept short and redacted.
"""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backuphaven.logsetup import SecretRegistry, redact

Engine = Literal["mariadb", "postgres"]
DUMP_TIMEOUT_S = 6 * 3600
LOAD_TIMEOUT_S = 6 * 3600
QUERY_TIMEOUT_S = 60
MARIADB_MARKER = b"-- Dump completed"


class DumpError(Exception):
    """A dump or load didn't work; the message says why (redacted)."""


@dataclass(frozen=True)
class Target:
    engine: Engine
    host: str
    port: int
    database: str
    user: str
    password: str = ""

    def __post_init__(self) -> None:
        if self.password:
            SecretRegistry.register(self.password)

    def describe(self) -> str:
        return f"{self.engine} database {self.database} on {self.host}:{self.port}"


@dataclass(frozen=True)
class DumpResult:
    size: int
    seconds: float


def tools_available(engine: Engine) -> bool:
    needed = ("mariadb-dump", "mariadb") if engine == "mariadb" else ("pg_dump", "pg_restore")
    return all(shutil.which(t) for t in needed)


def _env(target: Target) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MYSQL", "PG"))}
    env["LC_ALL"] = "C.UTF-8"
    if target.engine == "mariadb":
        env["MYSQL_PWD"] = target.password
    else:
        env["PGPASSWORD"] = target.password
        env["PGCONNECT_TIMEOUT"] = "15"
    return env


def _check_name(value: str, what: str) -> None:
    # Values come from app templates; keep them to plain names so no argument can
    # look like an option.
    if not value or value.startswith("-") or any(c in value for c in "\x00\n\r"):
        raise DumpError(f"the {what} {value!r} isn't usable")


def _run(
    args: list[str],
    target: Target,
    timeout: float,
    stdin: Path | None = None,
    stdout: Path | None = None,
) -> str:
    """Run a tool; return its output (unless written to ``stdout``). Raises DumpError."""
    in_handle = stdin.open("rb") if stdin else None
    out_handle = stdout.open("wb") if stdout else None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed tool, argument list, no shell
            args,
            stdin=in_handle if in_handle else subprocess.DEVNULL,
            stdout=out_handle if out_handle else subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_env(target),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise DumpError(f"{args[0]} isn't installed in this BackupHaven image") from None
    except subprocess.TimeoutExpired:
        raise DumpError(f"{args[0]} took longer than {int(timeout)} s") from None
    finally:
        for handle in (in_handle, out_handle):
            if handle is not None:
                handle.close()
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        message = detail[-1] if detail else f"exit status {completed.returncode}"
        raise DumpError(redact(f"{args[0]} failed for {target.describe()}: {message}")[:400])
    return completed.stdout.decode("utf-8", "replace") if completed.stdout else ""


def _mariadb_base(target: Target, tool: str) -> list[str]:
    return [
        tool,
        f"--host={target.host}",
        f"--port={target.port}",
        f"--user={target.user}",
        "--protocol=TCP",
        "--default-character-set=utf8mb4",
        # Only the client takes a connect timeout; mariadb-dump refuses it.
        *(["--connect-timeout=15"] if tool == "mariadb" else []),
    ]


def _postgres_base(target: Target, tool: str) -> list[str]:
    return [
        tool,
        f"--host={target.host}",
        f"--port={target.port}",
        f"--username={target.user}",
        "--no-password",
    ]


def dump(target: Target, dest: Path, timeout: float = DUMP_TIMEOUT_S) -> DumpResult:
    """Dump the database to ``dest`` (a new file BackupHaven owns) and check it."""
    for value, what in (
        (target.database, "database name"),
        (target.user, "user name"),
        (target.host, "address"),
    ):
        _check_name(value, what)
    started = time.monotonic()
    dest.unlink(missing_ok=True)
    try:
        if target.engine == "mariadb":
            args = [
                *_mariadb_base(target, "mariadb-dump"),
                "--single-transaction",
                "--quick",
                "--routines",
                "--triggers",
                "--events",
                "--hex-blob",
                "--add-drop-table",
                "--comments",
                "--dump-date",
                "--",
                target.database,
            ]
            _run(args, target, timeout, stdout=dest)
            with dest.open("rb") as handle:
                handle.seek(max(0, dest.stat().st_size - 256))
                if MARIADB_MARKER not in handle.read():
                    raise DumpError(f"the dump of {target.describe()} is incomplete")
        else:
            args = [
                *_postgres_base(target, "pg_dump"),
                "--format=custom",
                f"--file={dest}",
                f"--dbname={target.database}",
            ]
            _run(args, target, timeout)
            _run(["pg_restore", "--list", str(dest)], target, QUERY_TIMEOUT_S)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return DumpResult(dest.stat().st_size, time.monotonic() - started)


def load(target: Target, src: Path, timeout: float = LOAD_TIMEOUT_S) -> None:
    """Replace the database's contents with the dump at ``src``."""
    for value, what in (
        (target.database, "database name"),
        (target.user, "user name"),
        (target.host, "address"),
    ):
        _check_name(value, what)
    if target.engine == "mariadb":
        client = [*_mariadb_base(target, "mariadb"), "--batch", "--skip-column-names"]
        # Drop every table first (tables made after the backup would otherwise stay).
        tables = _run(
            [
                *client,
                "--execute=SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'",
                "--",
                target.database,
            ],
            target,
            QUERY_TIMEOUT_S,
        ).split()
        if tables:
            names = ", ".join("`" + t.replace("`", "``") + "`" for t in tables)
            _run(
                [
                    *client,
                    f"--execute=SET FOREIGN_KEY_CHECKS=0; DROP TABLE IF EXISTS {names}; "
                    "SET FOREIGN_KEY_CHECKS=1",
                    "--",
                    target.database,
                ],
                target,
                QUERY_TIMEOUT_S,
            )
        _run([*client, "--", target.database], target, timeout, stdin=src)
    else:
        _run(
            [
                *_postgres_base(target, "pg_restore"),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                f"--dbname={target.database}",
                str(src),
            ],
            target,
            timeout,
        )
