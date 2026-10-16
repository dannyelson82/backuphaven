"""Safe unpacking of backups (ARCHITECTURE.md §6.9, SECURITY.md).

A backup is unpacked only into a folder that is new and empty, through
``extract``. Nothing in the archive is trusted:

- Names must be plain relative paths under the part being restored: no absolute
  paths, no ``..``, no empty or ``.`` parts, no NUL bytes.
- Only regular files and symbolic links are written. Hard links, devices, FIFOs
  and anything else are refused.
- Folders are walked one level at a time with ``O_NOFOLLOW``, so a symbolic link
  (from the archive or put there by anyone else) is never followed while writing.
  Links are recreated as links and never followed.
- Every entry must be in the backup's contents list with the same type, size and
  link target, and every file's SHA-256 must match it as it is written.
- Owners, permissions and times are restored as recorded (owners only when
  running as root). Folders created along the way get the owner of the first
  entry put in them.

Any problem stops the unpacking with ``UnpackError``; the caller removes the
half-filled folder.
"""

import hashlib
import json
import os
import stat
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

import zstandard

from backuphaven.archive import MANIFEST_NAME

MANIFEST_SUFFIX = ".manifest.json"
_CHUNK = 1024 * 1024
DIR_MODE = 0o755


class UnpackError(Exception):
    """The backup can't be unpacked safely, or doesn't match its contents list."""


@dataclass(frozen=True)
class Part:
    """One prefix inside the archive and where its entries go.

    ``prefix`` ends with ``/``, e.g. ``apps/sonarr/files/config/``; an entry
    ``apps/sonarr/files/config/logs/a.txt`` goes to ``dest / "logs/a.txt"``.
    """

    prefix: str
    dest: Path
    # Top-level names in ``dest`` that are left alone and that the archive may not
    # use (a restore's set-aside folder when ``dest`` is a whole share).
    reserved: frozenset[str] = frozenset()


@dataclass
class UnpackResult:
    files: int = 0
    links: int = 0
    bytes: int = 0
    written: list[str] = field(default_factory=list)  # archive names, in order


def read_manifest(archive: Path) -> dict[str, Any]:
    """The contents list: the copy beside the archive, or the one inside it."""
    sidecar = archive.with_name(archive.name + MANIFEST_SUFFIX)
    if sidecar.is_file():
        data: dict[str, Any] = json.loads(sidecar.read_bytes())
        return data
    for info, handle in members(archive):
        if info.name == MANIFEST_NAME and handle is not None:
            data = json.loads(handle.read())
            return data
    raise UnpackError("the backup has no contents list")


def members(archive: Path) -> Iterator[tuple[tarfile.TarInfo, IO[bytes] | None]]:
    with archive.open("rb") as raw:
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for info in tar:
                yield info, tar.extractfile(info) if info.isreg() else None


def safe_relative(name: str) -> PurePosixPath:
    """A plain relative path, or ``UnpackError``."""
    if not name or "\x00" in name or name.startswith("/") or "\\" in name:
        raise UnpackError(f"unsafe name in the backup: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise UnpackError(f"unsafe name in the backup: {name!r}")
    return path


@contextmanager
def _open_dir(fd: int) -> Iterator[int]:
    try:
        yield fd
    finally:
        os.close(fd)


class _Writer:
    """Writes entries under one empty destination folder without following links."""

    def __init__(self, dest: Path, set_owner: bool) -> None:
        self._dest = dest
        self._set_owner = set_owner
        self._created: set[PurePosixPath] = set()

    def _walk(self, rel: PurePosixPath, uid: int, gid: int) -> int:
        """An open descriptor for ``rel``'s parent folder, creating folders."""
        fd = os.open(self._dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        so_far = PurePosixPath()
        try:
            for part in rel.parts[:-1]:
                so_far /= part
                try:
                    os.mkdir(part, DIR_MODE, dir_fd=fd)
                    if self._set_owner:
                        os.chown(part, uid, gid, dir_fd=fd, follow_symlinks=False)
                    self._created.add(so_far)
                except FileExistsError:
                    pass
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:  # a link or a file where a folder should be
                    raise UnpackError(f"{so_far} is not a plain folder: {exc.strerror}") from None
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd

    def file(self, rel: PurePosixPath, info: tarfile.TarInfo, data: IO[bytes]) -> str:
        with _open_dir(self._walk(rel, info.uid, info.gid)) as parent:
            try:
                out = os.open(
                    rel.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                raise UnpackError(f"{rel} appears twice in the backup") from None
            digest = hashlib.sha256()
            with os.fdopen(out, "wb") as handle:
                while chunk := data.read(_CHUNK):
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                if self._set_owner:
                    os.fchown(handle.fileno(), info.uid, info.gid)
                os.fchmod(handle.fileno(), stat.S_IMODE(info.mode))
                os.utime(handle.fileno(), (info.mtime, info.mtime))
            return digest.hexdigest()

    def link(self, rel: PurePosixPath, info: tarfile.TarInfo) -> None:
        with _open_dir(self._walk(rel, info.uid, info.gid)) as parent:
            try:
                os.symlink(info.linkname, rel.name, dir_fd=parent)
            except FileExistsError:
                raise UnpackError(f"{rel} appears twice in the backup") from None
            if self._set_owner:
                os.chown(rel.name, info.uid, info.gid, dir_fd=parent, follow_symlinks=False)
            os.utime(rel.name, (info.mtime, info.mtime), dir_fd=parent, follow_symlinks=False)


def extract(
    archive: Path, parts: list[Part], manifest: dict[str, Any] | None = None
) -> UnpackResult:
    """Unpack the entries under each part's prefix into its (new, empty) folder."""
    manifest = manifest if manifest is not None else read_manifest(archive)
    records = {e["path"]: e for e in manifest.get("entries", [])}
    for part in parts:
        if not part.prefix.endswith("/"):
            raise ValueError("a prefix ends with /")
        if (
            not part.dest.is_dir()
            or part.dest.is_symlink()
            or any(p.name not in part.reserved for p in part.dest.iterdir())
        ):
            raise UnpackError(f"{part.dest} must be an existing, empty folder")
    expected = {name for name in records for p in parts if name.startswith(p.prefix)}
    set_owner = os.geteuid() == 0
    writers = {p.prefix: _Writer(p.dest, set_owner) for p in parts}
    result = UnpackResult()
    try:
        for info, data in members(archive):
            match = next((p for p in parts if info.name.startswith(p.prefix)), None)
            if match is None:
                continue
            rel = safe_relative(info.name[len(match.prefix) :])
            if rel.parts[0] in match.reserved:
                raise UnpackError(f"{info.name} uses a reserved name")
            record = records.get(info.name)
            if record is None:
                raise UnpackError(f"{info.name} isn't in the backup's contents list")
            writer = writers[match.prefix]
            if info.isreg() and record["kind"] == "file":
                if data is None or info.size != record["size"]:
                    raise UnpackError(f"{info.name} differs from the contents list")
                digest = writer.file(rel, info, data)
                if digest != record["sha256"]:
                    raise UnpackError(f"{info.name} is damaged (checksum differs)")
                result.files += 1
                result.bytes += info.size
            elif info.issym() and record["kind"] == "symlink":
                if info.linkname != record["link"]:
                    raise UnpackError(f"{info.name} differs from the contents list")
                writer.link(rel, info)
                result.links += 1
            else:
                raise UnpackError(f"{info.name}: this kind of entry isn't restored")
            result.written.append(info.name)
    except (tarfile.TarError, zstandard.ZstdError, EOFError) as exc:
        raise UnpackError(f"the backup can't be read to the end: {exc}") from None
    missing = expected - set(result.written)
    if missing:
        raise UnpackError(f"{len(missing)} entries are missing, e.g. {sorted(missing)[0]}")
    return result
