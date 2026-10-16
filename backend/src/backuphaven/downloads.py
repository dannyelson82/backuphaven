"""Downloads from a backup (ARCHITECTURE.md §6.9): one app, or one file.

Both stream: an app is repacked on the fly into its own ``.tar.zst`` by a worker
thread feeding a small queue, so memory stays bounded whatever the size, and one
file is read straight out of the archive. Only entries recorded in the backup's
contents list are sent.
"""

import io
import queue
import tarfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any, cast

import zstandard

from backuphaven.unpack import UnpackError, members, safe_relative

_QUEUE_CHUNKS = 16
_END = object()


class _QueueWriter(io.RawIOBase):
    """A write-only file whose bytes go to a queue (blocking when it's full)."""

    def __init__(self, out: "queue.Queue[object]") -> None:
        self._out = out

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        chunk = bytes(data)
        if chunk:
            self._out.put(chunk)
        return len(chunk)


def app_entries(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    prefix = f"apps/{key}/"
    return [e for e in manifest.get("entries", []) if e["path"].startswith(prefix)]


def stream_app(archive: Path, manifest: dict[str, Any], key: str) -> Iterator[bytes]:
    """One app's entries as a new ``.tar.zst``, with names starting at ``<key>/``."""
    wanted = {e["path"] for e in app_entries(manifest, key)}
    out: queue.Queue[object] = queue.Queue(maxsize=_QUEUE_CHUNKS)
    stop = threading.Event()

    def produce() -> None:
        try:
            sink = _QueueWriter(out)
            with (
                zstandard.ZstdCompressor(level=3).stream_writer(
                    cast("IO[bytes]", sink), closefd=False
                ) as zst,
                tarfile.open(fileobj=zst, mode="w|", format=tarfile.PAX_FORMAT) as tar,
            ):
                for info, data in members(archive):
                    if stop.is_set():
                        return
                    if info.name not in wanted or not (info.isreg() or info.issym()):
                        continue
                    safe_relative(info.name)
                    info.name = info.name[len("apps/") :]
                    tar.addfile(info, data)
        except Exception as exc:  # reported to the reader
            out.put(exc)
        finally:
            out.put(_END)

    worker = threading.Thread(target=produce, name="download", daemon=True)
    worker.start()
    try:
        while True:
            item = out.get()
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            yield item  # type: ignore[misc]
    finally:
        stop.set()
        while worker.is_alive():  # unblock the producer if the client went away
            try:
                out.get_nowait()
            except queue.Empty:
                worker.join(timeout=0.1)


def stream_file(archive: Path, manifest: dict[str, Any], name: str) -> Iterator[bytes]:
    """One regular file's bytes, straight out of the archive."""
    record = next((e for e in manifest.get("entries", []) if e["path"] == name), None)
    if record is None or record["kind"] != "file":
        raise UnpackError("no such file in the backup")
    for info, data in members(archive):
        if info.name == name and data is not None:
            while chunk := data.read(1024 * 1024):
                yield chunk
            return
    raise UnpackError("the file wasn't found in the backup file")
