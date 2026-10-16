"""Plugin development file (ADR-0013): a Markdown brief for an AI coding assistant.

It describes one app as BackupHaven sees it, then appends AUTHORING.md (the plugin
format, the promise, fixtures and pull request steps). Privacy: host paths and
share names are never included; folders are described by their container path;
file contents are never read except the first bytes of a file to recognise an
SQLite database. Names inside the folders are listed up to a small depth, with a
reminder not to copy them into the repository.
"""

import os
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backuphaven import __version__, discovery
from backuphaven.plugins.manifest import Manifest
from backuphaven.plugins.registry import image_name
from backuphaven.selection import (
    RESERVED_DIR,
    SQLITE_HEADER,
    OutsideShareError,
    resolve_app_folder,
    server_database,
)

AUTHORING = Path(__file__).with_name("AUTHORING.md")
MAX_DEPTH = 3
MAX_LINES = 250
FILES_PER_FOLDER = 40
HINTS = {
    "cache": "cache?",
    "caches": "cache?",
    ".cache": "cache?",
    "logs": "logs?",
    "log": "logs?",
    "tmp": "temporary?",
    "temp": "temporary?",
    "transcode": "transcodes?",
    "thumbnails": "thumbnails?",
    "thumbs": "thumbnails?",
    "metadata": "metadata/artwork?",
    "mediacover": "artwork?",
    "backups": "app's own backups?",
    "backup": "app's own backups?",
}


@dataclass
class Found:
    """Databases anywhere under a folder, whatever the depth."""

    sqlite: list[tuple[str, int]] = field(default_factory=list)
    servers: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Node:
    name: str
    files: int = 0
    bytes: int = 0
    notes: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    listed_files: list[tuple[str, int, str]] = field(default_factory=list)
    more_files: int = 0  # files in this folder beyond FILES_PER_FOLDER


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000  # type: ignore[assignment]
    return f"{n} B"  # pragma: no cover


def _is_sqlite(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as handle:
        return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def _scan(path: Path, name: str, depth: int, rel: str, found: Found) -> Node:
    node = Node(name)
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError as exc:
        node.notes.append(f"unreadable: {exc.strerror}")
        return node
    names = {e.name for e in entries}
    server = server_database(names)
    if server:
        node.notes.append(f"{server} DATA FOLDER: unsafe to copy while running")
        found.servers.append((rel or ".", server))
    for entry in entries:
        if entry.name == RESERVED_DIR:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            if len(node.listed_files) < FILES_PER_FOLDER:
                node.listed_files.append((entry.name, 0, "link"))
        elif stat.S_ISDIR(info.st_mode):
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            child = _scan(Path(entry.path), entry.name, depth + 1, child_rel, found)
            node.files += child.files
            node.bytes += child.bytes
            hint = HINTS.get(entry.name.lower())
            if hint:
                child.notes.insert(0, hint)
            node.children.append(child)
        elif stat.S_ISREG(info.st_mode):
            node.files += 1
            node.bytes += info.st_size
            kind = ""
            if entry.name.endswith(("-wal", "-shm", "-journal")):
                kind = "SQLite side file"
            elif _is_sqlite(Path(entry.path)):
                kind = "SQLite database"
                found.sqlite.append((f"{rel}/{entry.name}" if rel else entry.name, info.st_size))
            elif entry.name.endswith((".log", ".pid", ".sock")):
                kind = entry.name.rsplit(".", 1)[1]
            if len(node.listed_files) < FILES_PER_FOLDER:
                node.listed_files.append((entry.name, info.st_size, kind))
            else:
                node.more_files += 1
    return node


def _render(node: Node, depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    for name, size, kind in node.listed_files:
        if len(lines) >= MAX_LINES:
            return
        extra = f"  [{kind}]" if kind else ""
        size_text = "" if kind == "link" else f"  {_human(size)}"
        lines.append(f"{pad}{name}{size_text}{extra}")
    if node.more_files:
        lines.append(f"{pad}… and {node.more_files} more files")
    for child in node.children:
        if len(lines) >= MAX_LINES:
            lines.append(f"{pad}… (more not shown)")
            return
        # A chain of folders that each hold only one folder is shown on one line
        # (Plex: Library/Application Support/Plex Media Server/).
        label = child.name
        while (
            len(child.children) == 1
            and not child.listed_files
            and not child.more_files
            and not child.notes
        ):
            child = child.children[0]
            label += f"/{child.name}"
        notes = f"  ({'; '.join(child.notes)})" if child.notes else ""
        lines.append(f"{pad}{label}/  {child.files} files, {_human(child.bytes)}{notes}")
        if depth < MAX_DEPTH:
            _render(child, depth + 1, lines)


def folder_map(root: Path) -> str:
    found = Found()
    node = _scan(root, root.name, 0, "", found)
    lines: list[str] = [f"(folder root)  {node.files} files, {_human(node.bytes)}"]
    if node.notes:
        lines.append("  ! " + "; ".join(node.notes))
    _render(node, 1, lines)
    if found.sqlite or found.servers:
        lines += ["", "Databases found (any depth):"]
        lines += [f"  {rel}  {_human(size)}  [SQLite database]" for rel, size in found.sqlite]
        lines += [f"  {rel}/  [{kind} data folder]" for rel, kind in found.servers]
    return "\n".join(lines)


def build(
    app: discovery.App, plugin: Manifest | None, data_root: Path, now: datetime | None = None
) -> str:
    """The development file for one app."""
    now = now or datetime.now(UTC)
    image = image_name(app.image)
    out: list[str] = [
        f"# Plugin development file: {app.name}",
        "",
        f"Made by BackupHaven {__version__} on {now.strftime('%Y-%m-%d')}. Give this file to "
        "an AI coding assistant (or read it yourself) to write a BackupHaven plugin for this "
        "app and open a pull request. The second half is the full plugin guide.",
        "",
        "## Task",
        "",
        (
            f"Write a BackupHaven plugin for **{app.name}** (image `{image}`): a recipe whose "
            "backup restores the app in working order, plus a fixture, then open a pull "
            "request as described in *Open a pull request* below."
        )
        if plugin is None
        else (
            f"**{app.name}** already uses the **{plugin.name}** plugin (`{plugin.id}` "
            f"{plugin.version}). Improve it if it misses something the app needs, keeping its "
            "id, bump its version, update its fixture, and open a pull request."
        ),
        "",
        "Research the app first (its documentation, its Docker image, its source code) to learn "
        "which files hold its settings, databases, keys and user data, and which it rebuilds. "
        "Use the folder map below as evidence of a real installation, not as the only source.",
        "",
        "**Privacy:** names in the folder map come from someone's server. Don't copy them "
        "into the repository; fixtures use invented names and contents.",
        "",
        "## The app on this server",
        "",
        f"- Container name: `{app.name}`",
        f"- Image: `{image}`",
        "- Folders mapped into the container (from its Unraid template):",
    ]
    for f in app.folders:
        out.append(f"  - `{f.target}`: app data on a data share (BackupHaven can read it)")
    for w in app.whole_shares:
        mounted = "BackupHaven can read it" if w.mounted else "not added to BackupHaven"
        out.append(
            f"  - `{w.target}`: a **whole share** ({mounted}). Media or downloads unless the "
            "app keeps its own data there (then use `share_folders`)"
        )
    for m in app.missing:
        out.append(f"  - `{m.target}`: looks like app data, on a share not added to BackupHaven")
    if plugin is not None:
        out += ["", "## Current plugin", "", "```yaml", _plugin_yaml(plugin), "```"]
    out += ["", "## Folder map", ""]
    out.append(
        f"Up to {MAX_DEPTH} levels; sizes are totals. *Hints in brackets are guesses from "
        "folder names.* SQLite databases are recognised by their file header."
    )
    roots = [(f.target, f.host) for f in app.folders]
    roots += [(w.target, w.host) for w in app.whole_shares if w.mounted]
    for target, host in roots:
        out += ["", f"### `{target}`", ""]
        try:
            root = resolve_app_folder(data_root, host)
        except (OutsideShareError, ValueError) as exc:
            out.append(f"(not readable: {exc})")
            continue
        if not root.is_dir():
            out.append("(folder doesn't exist)")
            continue
        out += ["```", folder_map(root), "```"]
    out += ["", "---", "", AUTHORING.read_text(encoding="utf-8")]
    return "\n".join(out) + "\n"


def _plugin_yaml(plugin: Manifest) -> str:
    import yaml

    return yaml.safe_dump(plugin.model_dump(exclude_defaults=True), sort_keys=False).strip()
