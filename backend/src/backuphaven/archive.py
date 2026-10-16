"""The archive: one streamed POSIX pax tar compressed with zstd per run (ADR-0008).

Writing never follows symbolic links: regular files are opened with ``O_NOFOLLOW``
and their metadata comes from the open file, so a file swapped for a link between
choosing and reading it can't redirect the read. A file that grows or shrinks while
it is read is stored exactly as read (cut or zero-padded to the size the tar header
announced, so the archive stays valid) and reported as changed.

Every stored file gets a SHA-256 of the bytes written. ``verify`` reads the finished
archive back and checks every entry against those records.
"""

import contextlib
import hashlib
import io
import json
import os
import stat
import tarfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any, Literal

import zstandard

MANIFEST_NAME = "manifest.json"
PARTIAL_SUFFIX = ".partial"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class FileRecord:
    path: str  # name inside the archive
    kind: Literal["file", "symlink"]
    size: int
    sha256: str | None  # None for links
    mode: int
    uid: int
    gid: int
    mtime: int
    link: str | None = None
    changed: bool = False  # grew or shrank while it was read


class _ExactReader(io.RawIOBase):
    """Reads exactly ``size`` bytes from ``source``: pads with zeros if it ends early,
    stops at ``size`` if it grew. Hashes what it returns."""

    def __init__(self, source: io.BufferedReader, size: int) -> None:
        self._source = source
        self._left = size
        self.digest = hashlib.sha256()
        self.changed = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self._left <= 0:
            return 0
        view = memoryview(buffer)[: min(len(buffer), self._left)]
        got = self._source.readinto(view) or 0
        if got == 0:  # the file got shorter while reading it
            self.changed = True
            got = len(view)
            view[:got] = bytes(got)
        self.digest.update(view[:got])
        self._left -= got
        return got

    def grew(self) -> bool:
        return bool(self._source.read(1))


class ArchiveWriter:
    """Writes ``<path>.partial``; ``close`` finishes the stream. The caller renames it
    into place once ``verify`` has passed."""

    def __init__(self, path: Path, level: int = 3, threads: int = -1) -> None:
        self.path = path
        self.partial = path.with_name(path.name + PARTIAL_SUFFIX)
        fd = os.open(self.partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        self._file = os.fdopen(fd, "wb")
        self._zstd = zstandard.ZstdCompressor(level=level, threads=threads).stream_writer(
            self._file, closefd=False
        )
        self._tar = tarfile.open(fileobj=self._zstd, mode="w|", format=tarfile.PAX_FORMAT)  # noqa: SIM115
        self.records: list[FileRecord] = []

    def _info(
        self, name: str, st: os.stat_result, kind: Literal["file", "symlink"]
    ) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type = tarfile.REGTYPE if kind == "file" else tarfile.SYMTYPE
        info.mode = stat.S_IMODE(st.st_mode)
        info.uid, info.gid = st.st_uid, st.st_gid
        info.mtime = int(st.st_mtime)
        info.size = st.st_size if kind == "file" else 0
        return info

    def add_file(self, name: str, source: Path, meta: os.stat_result | None = None) -> FileRecord:
        """A regular file. ``meta`` overrides owner, mode and time (for snapshot copies,
        which should be restored with the original database's owner)."""
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0))
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"{source} is not a regular file")
            info = self._info(name, st, "file")
            if meta is not None:
                info.mode, info.uid, info.gid = stat.S_IMODE(meta.st_mode), meta.st_uid, meta.st_gid
                info.mtime = int(meta.st_mtime)
            reader = _ExactReader(handle, info.size)
            self._tar.addfile(info, io.BufferedReader(reader, _CHUNK))
            changed = reader.changed or reader.grew()
        record = FileRecord(
            name, "file", info.size, reader.digest.hexdigest(), info.mode, info.uid, info.gid,
            int(info.mtime), changed=changed,
        )  # fmt: skip
        self.records.append(record)
        return record

    def add_symlink(self, name: str, source: Path) -> FileRecord:
        st = os.lstat(source)
        if not stat.S_ISLNK(st.st_mode):
            raise ValueError(f"{source} is not a symbolic link")
        info = self._info(name, st, "symlink")
        info.linkname = os.readlink(source)  # noqa: PTH115 - the raw link text, unchanged
        self._tar.addfile(info)
        record = FileRecord(
            name,
            "symlink",
            0,
            None,
            info.mode,
            info.uid,
            info.gid,
            int(info.mtime),
            link=info.linkname,
        )
        self.records.append(record)
        return record

    def add_bytes(self, name: str, data: bytes, mode: int = 0o600) -> FileRecord:
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = len(data), mode, int(time.time())
        self._tar.addfile(info, io.BytesIO(data))
        record = FileRecord(
            name, "file", len(data), hashlib.sha256(data).hexdigest(), mode, 0, 0, info.mtime
        )
        self.records.append(record)
        return record

    def close(self, manifest: dict[str, Any]) -> bytes:
        """Write ``manifest.json`` last (with every record so far) and finish the file.
        Returns the manifest bytes, which are also saved beside the archive."""
        manifest = {**manifest, "entries": [asdict(r) for r in self.records]}
        data = json.dumps(manifest, indent=1, sort_keys=True).encode()
        self.add_bytes(MANIFEST_NAME, data)
        self._tar.close()
        self._zstd.close()  # type: ignore[no-untyped-call]  # ends the zstd frame; the file stays open (closefd=False)
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        return data

    def abort(self) -> None:
        for closer in (self._tar.close, self._zstd.close, self._file.close):
            with contextlib.suppress(OSError, ValueError, tarfile.TarError):
                closer()
        self.partial.unlink(missing_ok=True)


def safe_name(value: str, fallback: str = "app") -> str:
    """A single archive path component from an app or folder name."""
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in value).strip("._")
    return cleaned[:100] or fallback


def folder_key(target: str) -> str:
    """``/config`` -> ``config``, ``/app/config`` -> ``app_config``, ``/`` -> ``root``."""
    return safe_name(target.strip("/").replace("/", "_"), fallback="root")


# --- reading back -------------------------------------------------------------------------


@dataclass
class VerifyResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    entries: int = 0

    def problem(self, text: str) -> None:
        self.ok = False
        if len(self.problems) < 50:
            self.problems.append(text)


def _members(path: Path) -> Iterator[tuple[tarfile.TarInfo, IO[bytes] | None]]:
    with path.open("rb") as handle:
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for info in tar:
                yield info, tar.extractfile(info) if info.isreg() else None


def verify(path: Path, manifest_bytes: bytes) -> VerifyResult:
    """Read the whole archive and compare every entry with the manifest."""
    manifest = json.loads(manifest_bytes)
    expected: dict[str, dict[str, Any]] = {e["path"]: e for e in manifest["entries"]}
    result = VerifyResult(ok=True)
    seen: set[str] = set()
    try:
        for info, data in _members(path):
            result.entries += 1
            name = info.name
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                result.problem(f"unsafe name in archive: {name}")
                continue
            if name == MANIFEST_NAME:
                if data is None or data.read() != manifest_bytes:
                    result.problem("manifest inside the archive doesn't match")
                seen.add(name)
                continue
            record = expected.get(name)
            if record is None:
                result.problem(f"not in the manifest: {name}")
                continue
            seen.add(name)
            if record["kind"] == "symlink":
                if not info.issym() or info.linkname != record["link"]:
                    result.problem(f"link differs: {name}")
                continue
            if data is None:
                result.problem(f"not a file: {name}")
                continue
            digest = hashlib.sha256()
            while chunk := data.read(_CHUNK):
                digest.update(chunk)
            if digest.hexdigest() != record["sha256"] or info.size != record["size"]:
                result.problem(f"content differs: {name}")
    except (OSError, EOFError, tarfile.TarError, zstandard.ZstdError) as exc:
        result.problem(f"archive can't be read to the end: {exc}")
    for name in expected.keys() - seen:
        result.problem(f"missing from the archive: {name}")
    if MANIFEST_NAME not in seen:
        result.problem("manifest missing from the archive")
    return result
