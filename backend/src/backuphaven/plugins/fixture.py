"""Plugin fixtures: proof that a plugin's backup restores (ADR-0013).

A fixture is a small, made-up sample of an app's data folders, in YAML::

    plugin: sonarr
    image: lscr.io/linuxserver/sonarr:latest
    folders:
      - target: /config            # path inside the container
        files:                     # path: text
          config.xml: "<Config><ApiKey>example</ApiKey></Config>"
          logs/sonarr.txt: "log line"
        sqlite: [sonarr.db]        # small SQLite databases to create
        links:                     # path: link target
          latest: config.xml
      - target: /data
        whole_share: true          # mapped to a whole share (plugin share_folders)
        files: {...}
    must_restore: [/config/config.xml, /config/sonarr.db]   # target + path
    must_not_backup: [/config/logs/sonarr.txt]

``round_trip`` builds a pretend Unraid server from it (template, data shares),
backs the app up with the plugin, deletes the app's folders as if the server were
new, restores, and returns every problem found. An empty list means the plugin
keeps its promise for this sample.
"""

import fnmatch
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated
from xml.sax.saxutils import escape

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backuphaven.dbdump import DumpError, Target, execute
from backuphaven.docker_proxy import DockerProxy
from backuphaven.plugins.fake_docker import fake_docker
from backuphaven.plugins.manifest import Manifest

if TYPE_CHECKING:
    from backuphaven.db import Database

APP_SHARE = "appdata"
SAMPLE_ROWS = 25

Rel = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class FixtureFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Annotated[str, StringConstraints(pattern=r"^/.*$")]
    whole_share: bool = False
    files: dict[Rel, str] = Field(default_factory=dict)
    sqlite: list[Rel] = Field(default_factory=list)
    links: dict[Rel, str] = Field(default_factory=dict)


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: str
    image: str
    folders: list[FixtureFolder] = Field(min_length=1)
    must_restore: list[str] = Field(min_length=1)
    must_not_backup: list[str] = Field(default_factory=list)
    # Template variables (Type="Variable"), e.g. what the app's real template sets.
    variables: dict[str, str] = Field(default_factory=dict)


class DatabaseServerMissingError(RuntimeError):
    """The plugin has external databases and no test server is configured
    (BH_TEST_MARIADB / BH_TEST_POSTGRES = host:port:user:password)."""


TEST_SERVERS = {"mariadb": "BH_TEST_MARIADB", "postgres": "BH_TEST_POSTGRES"}


def load(path: Path) -> Fixture:
    return Fixture.model_validate(yaml.safe_load(path.read_text()))


def _host(app: str, folder: FixtureFolder) -> str:
    if folder.whole_share:
        return f"/mnt/user/{app}-share"
    return f"/mnt/user/{APP_SHARE}/{app}" + folder.target.rstrip("/").replace("/", "-")


def _template(app: str, image: str, folders: list[FixtureFolder], variables: dict[str, str]) -> str:
    configs = "\n".join(
        [
            f'  <Config Name="{escape(f.target)}" Target="{escape(f.target)}" Default="" '
            f'Mode="rw" Type="Path">{escape(_host(app, f))}</Config>'
            for f in folders
        ]
        + [
            f'  <Config Name="{escape(k)}" Target="{escape(k)}" Default="" Mode="" '
            f'Type="Variable">{escape(v)}</Config>'
            for k, v in variables.items()
        ]
    )
    return (
        '<?xml version="1.0"?>\n<Container version="2">\n'
        f"  <Name>{escape(app)}</Name>\n  <Repository>{escape(image)}</Repository>\n"
        f"{configs}\n</Container>\n"
    )


def _make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO sample(value) VALUES (?)", [(f"row {i}",) for i in range(SAMPLE_ROWS)]
    )
    con.commit()
    con.close()


def _rows(path: Path) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT count(*) FROM sample").fetchone()[0])
    finally:
        con.close()


def _split(item: str, folders: list[FixtureFolder]) -> tuple[FixtureFolder, str] | None:
    """``/config/a/b.txt`` → (the /config folder, ``a/b.txt``)."""
    for folder in sorted(folders, key=lambda f: -len(f.target)):
        prefix = folder.target.rstrip("/") + "/"
        if item.startswith(prefix):
            return folder, item[len(prefix) :]
    return None


def _must_split(item: str, folders: list[FixtureFolder]) -> tuple[FixtureFolder, str]:
    split = _split(item, folders)
    if split is None:
        raise ValueError(f"{item} isn't inside one of the fixture's folders")
    return split


def round_trip(manifest: Manifest, fixture: Fixture) -> list[str]:
    """Back up, wipe and restore the fixture with the plugin; the problems found."""
    # Imported here: these import the plugin registry, which imports this package.
    from backuphaven import retention
    from backuphaven.config import Settings
    from backuphaven.db import Database, Restore, RunApp
    from backuphaven.plugins.registry import Registry
    from backuphaven.restore import Restorer
    from backuphaven.runner import Runner
    from backuphaven.unpack import members

    problems: list[str] = []
    if fixture.plugin != manifest.id:
        problems.append(f"the fixture is for {fixture.plugin}, not {manifest.id}")
    from backuphaven.plugins.registry import image_name

    name = image_name(fixture.image)
    if not any(fnmatch.fnmatchcase(name, p.lower()) for p in manifest.recognise.images):
        problems.append(f"the plugin doesn't recognise the image {fixture.image}")
    for item in [*fixture.must_restore, *fixture.must_not_backup]:
        if _split(item, fixture.folders) is None:
            problems.append(f"{item} isn't inside one of the fixture's folders")
    if problems:
        return problems
    app = manifest.id
    with tempfile.TemporaryDirectory(prefix="bh-fixture-") as tmp:
        base = Path(tmp)
        templates = base / "templates"
        templates.mkdir()
        data_root = base / "mnt-user"
        (data_root / APP_SHARE).mkdir(parents=True)
        variables, servers = _database_variables(manifest, fixture)
        (templates / f"my-{app}.xml").write_text(
            _template(app, fixture.image, fixture.folders, variables)
        )
        roots: dict[str, Path] = {}
        for folder in fixture.folders:
            root = data_root / PurePosixPath(_host(app, folder)).relative_to("/mnt/user")
            root.mkdir(parents=True, exist_ok=True)
            roots[folder.target] = root
            for rel, text in folder.files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            for rel in folder.sqlite:
                _make_db(root / rel)
            for rel, target in folder.links.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).symlink_to(target)
        settings = Settings(
            config_dir=base / "config",
            backups_dir=base / "backups",
            templates_dir=templates,
            data_root=data_root,
            min_free_bytes=0,
        )
        db = Database(settings.config_dir / "backuphaven.db")
        db.migrate()
        with db.write() as session:
            retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
        registry = Registry({app: manifest})
        stop_mode = manifest.backup.mode == "stop"
        proxy = None
        if stop_mode:  # a pretend Docker with the app's container, running
            docker_ctx = fake_docker()
            docker, address = docker_ctx.__enter__()
            docker.add(app, fixture.image)
            proxy = DockerProxy(address)
        runner = Runner(db, settings, registry, proxy=proxy)
        seeded = _seed_databases(db, app, manifest, servers)
        try:
            run_id = runner.run_now(only=[app], scheduled=True)
        finally:
            if stop_mode:
                calls = list(docker.calls)
                running = docker.state(app) == "running"
                docker_ctx.__exit__(None, None, None)
        if stop_mode:
            if not any("/stop" in c for c in calls):
                problems.append("the container wasn't stopped for its backup")
            if not running:
                problems.append("the container wasn't started again after its backup")
        with db.read() as session:
            result = session.query(RunApp).filter_by(run_id=run_id).one_or_none()
        if result is None or result.status != "ok":
            detail = result.problems if result is not None else "no result"
            return [f"backup wasn't OK: {result.status if result else '-'} {detail}"]
        archive = next(settings.backups_dir.glob("backuphaven-*.tar.zst"))
        stored = {info.name for info, _ in members(archive)}

        def in_backup(item: str) -> bool:
            split = _must_split(item, fixture.folders)
            folder, rel = split
            key = folder.target.strip("/").replace("/", "_") or "root"
            return any(
                name in (f"apps/{app}/files/{key}/{rel}", f"apps/{app}/databases/{key}/{rel}")
                for name in stored
            )

        for item in fixture.must_not_backup:
            if in_backup(item):
                problems.append(f"{item} is in the backup but shouldn't be")
        for item in fixture.must_restore:
            if not in_backup(item):
                problems.append(f"{item} isn't in the backup")

        # A new server: the app's folders are gone (whole shares emptied).
        originals = {
            item: (
                (roots[s[0].target] / s[1]).read_bytes()
                if (roots[s[0].target] / s[1]).is_file()
                else None
            )
            for item in fixture.must_restore
            if (s := _split(item, fixture.folders)) is not None
        }
        for folder in fixture.folders:
            root = roots[folder.target]
            if folder.whole_share:
                for child in root.iterdir():
                    shutil.rmtree(
                        child
                    ) if child.is_dir() and not child.is_symlink() else child.unlink()
            else:
                shutil.rmtree(root)
        for seed in seeded:  # a new server: the app's database is empty too
            execute(seed, "DROP TABLE bh_fixture")
        restorer = Restorer(
            db, settings, registry, runner.lock, quiet_seconds=0, sleep=lambda _s: None
        )
        restore_id = restorer.run(run_id, app)
        with db.read() as session:
            row = session.get(Restore, restore_id)
        if row is None or row.status != "done":
            return [*problems, f"restore failed: {row.error if row else 'no record'}"]
        for item, before in originals.items():
            split = _must_split(item, fixture.folders)
            path = roots[split[0].target] / split[1]
            if not path.exists() and not path.is_symlink():
                problems.append(f"{item} wasn't restored")
            elif split[1] in split[0].sqlite:
                if _rows(path) != SAMPLE_ROWS:
                    problems.append(f"{item}: the restored database is incomplete")
            elif before is not None and path.read_bytes() != before:
                problems.append(f"{item} came back different")
        for item in fixture.must_not_backup:
            split = _must_split(item, fixture.folders)
            if (roots[split[0].target] / split[1]).exists():
                problems.append(f"{item} came back after a restore from scratch")
        for seed in seeded:
            try:
                rows = execute(seed, "SELECT count(*) FROM bh_fixture").strip()
            except DumpError as exc:
                problems.append(f"the {seed.engine} database wasn't restored: {exc}")
                continue
            if rows != str(SAMPLE_ROWS):
                problems.append(f"the {seed.engine} database came back with {rows} rows")
            execute(seed, "DROP TABLE bh_fixture")
    return problems


def _database_variables(
    manifest: Manifest, fixture: Fixture
) -> tuple[dict[str, str], dict[str, tuple[str, int, str, str]]]:
    """Template variables pointing the plugin's external databases at the test
    servers. Raises DatabaseServerMissingError when a server isn't configured."""
    from backuphaven import templating

    variables = dict(fixture.variables)
    servers: dict[str, tuple[str, int, str, str]] = {}
    for spec in manifest.backup.external_databases:
        value = os.environ.get(TEST_SERVERS[spec.engine])
        if not value:
            raise DatabaseServerMissingError(
                f"the {spec.engine} part needs a test server: set {TEST_SERVERS[spec.engine]}"
                "=host:port:user:password"
            )
        host, port, user, password = value.split(":", 3)
        servers[spec.id] = (host, int(port), user, password)
        for expression, real in (
            (spec.host, host),
            (spec.port, port),
            (spec.user, user),
            (spec.password, password),
            (spec.database, "bhtest"),
        ):
            for name in templating.names(expression):
                variables[name] = real
    return variables, servers


def _seed_databases(
    db: "Database", app: str, manifest: Manifest, servers: dict[str, tuple[str, int, str, str]]
) -> list[Target]:
    """Point each external database at its test server and put sample rows in it."""
    from backuphaven import external

    targets: list[Target] = []
    with db.write() as session:
        for spec in manifest.backup.external_databases:
            host, port, user, password = servers[spec.id]
            external.save_override(session, app, spec.id, external.Address(host=host, port=port))
            targets.append(Target(spec.engine, host, port, "bhtest", user, password))
    for target in targets:
        execute(target, "DROP TABLE IF EXISTS bh_fixture")
        execute(target, "CREATE TABLE bh_fixture (id INT PRIMARY KEY, v VARCHAR(20))")
        values = ", ".join(f"({i}, 'row {i}')" for i in range(SAMPLE_ROWS))
        execute(target, f"INSERT INTO bh_fixture VALUES {values}")  # noqa: S608 - fixed sample rows
    return targets
