"""Choosing what to back up from an app's data folder (ARCHITECTURE.md §6.2-6.3).

Read side of the file-access layer: every folder is resolved with ``realpath`` and
must stay inside its data share; walking never follows symbolic links; special files
(sockets, FIFOs, devices) are skipped. ``effective_rules`` and ``matches`` are pure.
"""

import fnmatch
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from backuphaven.plugins.manifest import Manifest

SQLITE_HEADER = b"SQLite format 3\x00"
# Files SQLite keeps next to a database; the snapshot already contains their contents.
SIDECARS = ("-wal", "-shm", "-journal")
# Where restores keep set-aside files; never backed up (restore.py).
RESERVED_DIR = ".backuphaven-restore"


def server_database(names: set[str]) -> str | None:
    """Whether a folder holding these entries is a database server's data folder,
    whose files can't be copied safely while the server runs."""
    if "PG_VERSION" in names and "global" in names:
        return "PostgreSQL"
    if names & {"ibdata1", "aria_log_control", "mysql.ibd"}:
        return "MariaDB/MySQL"
    return None


@dataclass(frozen=True)
class Rules:
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    databases: tuple[str, ...] | Literal["auto"]
    expect: tuple[str, ...]


def effective_rules(manifest: Manifest, values: dict[str, bool] | None = None) -> Rules:
    """The manifest's rules plus those its settings add when switched on."""
    values = values or {}
    include = list(manifest.backup.include) or ["**"]
    exclude = list(manifest.backup.exclude)
    for setting in manifest.settings:
        if values.get(setting.id, setting.default):
            include += setting.when_true.include
            # A setting that adds a folder also lifts the default exclusion of it.
            exclude = [p for p in exclude if p not in setting.when_true.include]
            exclude += setting.when_true.exclude
    databases = manifest.backup.databases
    return Rules(
        include=tuple(include),
        exclude=tuple(exclude),
        databases="auto" if databases == "auto" else tuple(databases),
        expect=tuple(manifest.backup.expect),
    )


def matches(rel: str, patterns: tuple[str, ...]) -> bool:
    path = PurePosixPath(rel)
    return any(path.full_match(p) for p in patterns)


def _dir_excluded(rel: str, exclude: tuple[str, ...]) -> bool:
    """Whether a whole folder is excluded (``logs/**``, ``**/Cache/**``), so it isn't walked."""
    path = PurePosixPath(rel)
    return any(p.endswith("/**") and path.full_match(p[:-3]) for p in exclude)


def could_contain(rel_dir: str, patterns: tuple[str, ...]) -> bool:
    """Whether any file under folder ``rel_dir`` could match one of ``patterns``.

    Compares the folder's parts with the start of each pattern; ``**`` matches any
    depth, so a pattern reaching one means yes. Pure; used to skip folders, like
    Plex's posters, that hold many files and nothing the plugin wants.
    """
    parts = PurePosixPath(rel_dir).parts
    for pattern in patterns:
        wanted = PurePosixPath(pattern).parts
        for index, part in enumerate(parts):
            if index >= len(wanted) - 1:
                # The pattern ends here (a file name) or before: nothing deeper,
                # unless its last part is "**".
                if index < len(wanted) and wanted[index] == "**":
                    return True
                break
            if wanted[index] == "**":
                return True
            if not fnmatch.fnmatchcase(part, wanted[index]):
                break
        else:
            return True
    return False


@dataclass(frozen=True)
class Entry:
    rel: str  # path relative to the app's folder, "/"-separated
    kind: Literal["file", "symlink"]
    size: int


@dataclass
class Selection:
    root: Path
    files: list[Entry] = field(default_factory=list)
    databases: list[Entry] = field(default_factory=list)
    skipped_special: list[str] = field(default_factory=list)
    missing_expected: list[str] = field(default_factory=list)
    # Database server folders found among the chosen files: (folder, kind).
    server_databases: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in (*self.files, *self.databases))


class OutsideShareError(ValueError):
    """A data folder that resolves outside its data share (e.g. through a symlink)."""


def resolve_app_folder(data_root: Path, host: str) -> Path:
    """Where a data folder (``/mnt/user/<share>/...``) is inside BackupHaven, checked.

    The data root is ``/mnt/user`` in the container, so the path is the same; in tests
    it is a temporary folder. Symbolic links are resolved first, then the result must
    still be inside the same share.
    """
    rel = PurePosixPath(host).relative_to("/mnt/user")
    share_root = os.path.realpath(data_root / rel.parts[0])
    resolved = os.path.realpath(data_root / rel)
    if os.path.commonpath([share_root, resolved]) != share_root:
        raise OutsideShareError(f"{host} resolves outside its share")
    return Path(resolved)


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as handle:  # noqa: PTH123 - os.walk gives str paths
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def select(root: Path, rules: Rules) -> Selection:
    """Walk ``root`` (never following links) and sort entries into files and databases."""
    selection = Selection(root=root)
    candidates: list[tuple[Entry, str]] = []
    # Folders worth entering: with a fixed list of databases, only those that could
    # hold an included file or one of the databases. ("auto" looks everywhere.)
    wanted = None if rules.databases == "auto" else (*rules.include, *rules.databases)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        server = server_database({*dirnames, *filenames})
        if server is not None:
            selection.server_databases.append((rel_dir or ".", server))
        kept_dirs = []
        for name in sorted(dirnames):
            if name == RESERVED_DIR:
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)  # noqa: PTH118
            if os.path.islink(full):  # noqa: PTH114 - os.walk gives str paths
                filenames.append(name)  # a link to a folder is archived as a link
            elif not _dir_excluded(rel, rules.exclude) and (
                wanted is None or could_contain(rel, wanted)
            ):
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)  # noqa: PTH118
            try:
                info = os.lstat(full)
            except OSError:
                continue  # vanished while walking
            if stat.S_ISLNK(info.st_mode):
                kind: Literal["file", "symlink"] = "symlink"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                selection.skipped_special.append(rel)
                continue
            if matches(rel, rules.exclude):
                continue
            candidates.append((Entry(rel, kind, info.st_size if kind == "file" else 0), full))

    database_rels: set[str] = set()
    for entry, full in candidates:
        if entry.kind != "file" or entry.rel.endswith(SIDECARS):
            continue
        if rules.databases == "auto":
            is_db = _is_sqlite(full)
        else:
            is_db = matches(entry.rel, rules.databases)
        if is_db:
            database_rels.add(entry.rel)
            selection.databases.append(entry)
    sidecars = {db + suffix for db in database_rels for suffix in SIDECARS}
    for entry, _full in candidates:
        if entry.rel in database_rels or entry.rel in sidecars:
            continue
        if matches(entry.rel, rules.include):
            selection.files.append(entry)

    chosen = [e.rel for e in (*selection.files, *selection.databases)]
    for pattern in rules.expect:
        if not any(PurePosixPath(rel).full_match(pattern) for rel in chosen):
            selection.missing_expected.append(pattern)
    return selection
