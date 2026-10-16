"""Safe unpacking (ARCHITECTURE.md §6.9): round trips and hostile archives."""

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path

import pytest
import zstandard

from backuphaven.archive import ArchiveWriter
from backuphaven.unpack import Part, UnpackError, extract, read_manifest, safe_relative


def real_backup(tmp_path: Path) -> Path:
    """A backup made the normal way: files, a link, a database copy."""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "config.xml").write_text("<Config/>")
    (src / "sub" / "data.bin").write_bytes(os.urandom(300_000))
    (src / "sub" / "data.bin").chmod(0o640)
    os.utime(src / "config.xml", (1_700_000_000, 1_700_000_000))
    (src / "latest").symlink_to("sub/data.bin")
    (src / "app.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    archive = tmp_path / "backuphaven-2026-10-17T030000.tar.zst"
    writer = ArchiveWriter(archive)
    for rel in ("config.xml", "sub/data.bin"):
        writer.add_file(f"apps/demo/files/config/{rel}", src / rel)
    writer.add_symlink("apps/demo/files/config/latest", src / "latest")
    writer.add_file("apps/demo/databases/config/app.db", src / "app.db")
    writer.add_file("apps/other/files/config/x.txt", src / "config.xml")
    manifest = writer.close({"format": 1})
    writer.partial.rename(archive)
    archive.with_name(archive.name + ".manifest.json").write_bytes(manifest)
    return archive


def parts(dest: Path) -> list[Part]:
    return [Part("apps/demo/files/config/", dest), Part("apps/demo/databases/config/", dest)]


def test_round_trip(tmp_path: Path) -> None:
    archive = real_backup(tmp_path)
    dest = tmp_path / "restored"
    dest.mkdir()
    result = extract(archive, parts(dest))
    assert (result.files, result.links) == (3, 1)
    src = tmp_path / "src"
    for rel in ("config.xml", "sub/data.bin", "app.db"):
        assert (dest / rel).read_bytes() == (src / rel).read_bytes()
    assert str((dest / "latest").readlink()) == "sub/data.bin"
    assert oct((dest / "sub" / "data.bin").stat().st_mode & 0o777) == "0o640"
    assert int((dest / "config.xml").stat().st_mtime) == 1_700_000_000
    assert not (dest / "x.txt").exists()  # another app's file


def test_the_contents_list_inside_the_archive_is_used_when_the_copy_is_gone(tmp_path: Path) -> None:
    archive = real_backup(tmp_path)
    archive.with_name(archive.name + ".manifest.json").unlink()
    assert read_manifest(archive)["format"] == 1


def test_destination_must_be_new_and_empty(tmp_path: Path) -> None:
    archive = real_backup(tmp_path)
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "old.txt").write_text("x")
    with pytest.raises(UnpackError, match="empty"):
        extract(archive, parts(dest))
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(UnpackError):
        extract(archive, parts(link))


# --- hostile archives ---------------------------------------------------------------------


def hostile(tmp_path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    """An archive with a matching contents list, so only the entries themselves are
    being judged."""
    raw = io.BytesIO()
    entries = []
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                kind = "file"
            else:
                tar.addfile(info)
                kind = "symlink" if info.issym() else "other"
            entries.append(
                {
                    "path": info.name,
                    "kind": kind,
                    "size": info.size,
                    "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                    "link": info.linkname or None,
                }
            )
    archive = tmp_path / "evil.tar.zst"
    archive.write_bytes(zstandard.ZstdCompressor().compress(raw.getvalue()))
    archive.with_name(archive.name + ".manifest.json").write_text(
        json.dumps({"format": 1, "entries": entries})
    )
    return archive


def entry(name: str, kind: bytes = tarfile.REGTYPE, link: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.mtime, info.mode = kind, link, int(time.time()), 0o644
    return info


PREFIX = "apps/demo/files/config/"


@pytest.mark.parametrize(
    "name",
    [
        f"{PREFIX}../escape.txt",
        f"{PREFIX}a/../../escape.txt",
        f"{PREFIX}./x",
        f"{PREFIX}a//b",
        f"{PREFIX}/etc/passwd",
    ],
)
def test_names_that_leave_the_folder_are_refused(tmp_path: Path, name: str) -> None:
    archive = hostile(tmp_path, [(entry(name), b"evil")])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="unsafe name"):
        extract(archive, [Part(PREFIX, dest)])
    assert not (tmp_path / "escape.txt").exists()


def test_a_link_in_the_archive_is_never_followed_while_writing(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    archive = hostile(
        tmp_path,
        [
            (entry(f"{PREFIX}sneaky", tarfile.SYMTYPE, str(outside)), None),
            (entry(f"{PREFIX}sneaky/pwned.txt"), b"evil"),
        ],
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="not a plain folder"):
        extract(archive, [Part(PREFIX, dest)])
    assert list(outside.iterdir()) == []


def test_a_file_written_through_a_dangling_link_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "victim.txt"
    archive = hostile(
        tmp_path,
        [
            (entry(f"{PREFIX}f", tarfile.SYMTYPE, str(target)), None),
            (entry(f"{PREFIX}f"), b"evil"),
        ],
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError):  # refused, one way or another
        extract(archive, [Part(PREFIX, dest)])
    assert not target.exists()


@pytest.mark.parametrize(
    "kind", [tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE]
)
def test_hard_links_devices_and_fifos_are_refused(tmp_path: Path, kind: bytes) -> None:
    info = entry(f"{PREFIX}dev", kind, "/etc/shadow" if kind == tarfile.LNKTYPE else "")
    archive = hostile(tmp_path, [(info, None)])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="isn't restored"):
        extract(archive, [Part(PREFIX, dest)])


def test_a_changed_file_is_caught_by_its_checksum(tmp_path: Path) -> None:
    archive = hostile(tmp_path, [(entry(f"{PREFIX}a.txt"), b"original")])
    sidecar = archive.with_name(archive.name + ".manifest.json")
    manifest = json.loads(sidecar.read_text())
    manifest["entries"][0]["sha256"] = hashlib.sha256(b"different").hexdigest()
    sidecar.write_text(json.dumps(manifest))
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="checksum"):
        extract(archive, [Part(PREFIX, dest)])


def test_entries_not_in_the_contents_list_or_missing_from_the_archive(tmp_path: Path) -> None:
    archive = hostile(tmp_path, [(entry(f"{PREFIX}a.txt"), b"a")])
    sidecar = archive.with_name(archive.name + ".manifest.json")
    manifest = json.loads(sidecar.read_text())
    sidecar.write_text(json.dumps({**manifest, "entries": []}))
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="contents list"):
        extract(archive, [Part(PREFIX, dest)])
    extra = {**manifest["entries"][0], "path": f"{PREFIX}gone.txt"}
    sidecar.write_text(json.dumps({**manifest, "entries": [*manifest["entries"], extra]}))
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    with pytest.raises(UnpackError, match="missing"):
        extract(archive, [Part(PREFIX, dest2)])


def test_a_cut_off_archive_is_reported(tmp_path: Path) -> None:
    archive = real_backup(tmp_path)
    archive.write_bytes(archive.read_bytes()[:-2000])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError):
        extract(archive, parts(dest))


@pytest.mark.parametrize("name", ["", "a\x00b", "/abs", "a/../b", "a\\b", "."])
def test_safe_relative(name: str) -> None:
    with pytest.raises(UnpackError):
        safe_relative(name)


def test_the_same_file_twice_is_refused(tmp_path: Path) -> None:
    archive = hostile(
        tmp_path, [(entry(f"{PREFIX}a.txt"), b"first"), (entry(f"{PREFIX}a.txt"), b"first")]
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(UnpackError, match="twice"):
        extract(archive, [Part(PREFIX, dest)])
    assert (dest / "a.txt").read_bytes() == b"first"
