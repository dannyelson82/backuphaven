"""The archive writer and verifier (ADR-0008)."""

import json
import os
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
import zstandard

from backuphaven.archive import (
    MANIFEST_NAME,
    ArchiveWriter,
    folder_key,
    safe_name,
    verify,
)


def write_archive(tmp_path: Path, build: Callable[[ArchiveWriter], object]) -> tuple[Path, bytes]:
    archive = tmp_path / "backups" / "test.tar.zst"
    archive.parent.mkdir(exist_ok=True)
    writer = ArchiveWriter(archive, level=1, threads=0)
    build(writer)
    manifest = writer.close({"format": 1})
    writer.partial.rename(archive)
    return archive, manifest


def read_all(archive: Path) -> dict[str, tarfile.TarInfo | bytes]:
    out: dict[str, tarfile.TarInfo | bytes] = {}
    with archive.open("rb") as handle:
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for info in tar:
                data = tar.extractfile(info) if info.isreg() else None
                out[info.name] = data.read() if data else info
    return out


def test_round_trip_keeps_content_owner_mode_time_and_links(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "config.xml").write_text("<Config/>")
    (src / "config.xml").chmod(0o640)
    os.utime(src / "config.xml", (1_700_000_000, 1_700_000_000))
    (src / "big.bin").write_bytes(os.urandom(3 * 1024 * 1024))
    (src / "link").symlink_to("config.xml")

    def build(w: ArchiveWriter) -> None:
        w.add_file("apps/a/files/config/config.xml", src / "config.xml")
        w.add_file("apps/a/files/config/big.bin", src / "big.bin")
        w.add_symlink("apps/a/files/config/link", src / "link")
        w.add_bytes("apps/a/template.xml", b"<Container/>")

    archive, manifest = write_archive(tmp_path, build)
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    entries = read_all(archive)
    assert entries["apps/a/files/config/config.xml"] == b"<Config/>"
    assert entries["apps/a/files/config/big.bin"] == (src / "big.bin").read_bytes()
    link = entries["apps/a/files/config/link"]
    assert isinstance(link, tarfile.TarInfo) and link.issym() and link.linkname == "config.xml"
    assert list(entries)[-1] == MANIFEST_NAME  # written last
    records = {e["path"]: e for e in json.loads(manifest)["entries"]}
    xml = records["apps/a/files/config/config.xml"]
    assert (xml["mode"], xml["mtime"], xml["uid"]) == (0o640, 1_700_000_000, os.getuid())
    result = verify(archive, manifest)
    assert result.ok, result.problems
    assert result.entries == 5


def test_snapshot_copies_keep_the_original_databases_owner_and_mode(tmp_path: Path) -> None:
    original = tmp_path / "app.db"
    original.write_bytes(b"db")
    original.chmod(0o664)
    os.utime(original, (1_600_000_000, 1_600_000_000))
    copy = tmp_path / "snapshot.db"
    copy.write_bytes(b"snapshot")
    copy.chmod(0o600)
    archive, manifest = write_archive(
        tmp_path, lambda w: w.add_file("apps/a/databases/config/app.db", copy, original.stat())
    )
    record = json.loads(manifest)["entries"][0]
    assert (record["mode"], record["mtime"]) == (0o664, 1_600_000_000)
    assert verify(archive, manifest).ok


def test_links_are_never_followed_when_adding_files(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("password")
    (tmp_path / "sneaky").symlink_to(secret)
    archive = tmp_path / "x.tar.zst"
    writer = ArchiveWriter(archive, level=1, threads=0)
    with pytest.raises(OSError):  # O_NOFOLLOW refuses to open through the link
        writer.add_file("apps/a/files/config/sneaky", tmp_path / "sneaky")
    writer.abort()
    assert not writer.partial.exists()


def test_a_file_that_shrinks_while_read_keeps_the_archive_valid(tmp_path: Path) -> None:
    src = tmp_path / "log.txt"
    src.write_bytes(b"x" * 100_000)
    real_fstat = os.fstat

    def fstat_claims_bigger(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        values = list(st)
        values[6] = st.st_size + 5_000  # st_size: as if it shrank after the header was made
        return os.stat_result(values)

    archive = tmp_path / "x.tar.zst"
    writer = ArchiveWriter(archive, level=1, threads=0)
    os.fstat = fstat_claims_bigger
    try:
        record = writer.add_file("apps/a/files/config/log.txt", src)
    finally:
        os.fstat = real_fstat
    manifest = writer.close({})
    writer.partial.rename(archive)
    assert record.changed and record.size == 105_000
    assert verify(archive, manifest).ok


def test_verify_catches_damage_and_missing_entries(tmp_path: Path) -> None:
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(200_000))
    archive, manifest = write_archive(
        tmp_path, lambda w: w.add_file("apps/a/files/config/f.bin", src)
    )
    data = bytearray(archive.read_bytes())
    data[len(data) // 2] ^= 0xFF  # one flipped byte in the middle
    archive.write_bytes(bytes(data))
    broken = verify(archive, manifest)
    assert not broken.ok and broken.problems

    archive2, manifest2 = write_archive(
        tmp_path, lambda w: w.add_file("apps/a/files/config/f.bin", src)
    )
    tampered = json.loads(manifest2)
    tampered["entries"].append(dict(tampered["entries"][0], path="apps/a/files/config/extra"))
    result = verify(archive2, json.dumps(tampered).encode())
    assert not result.ok
    assert any("missing from the archive: apps/a/files/config/extra" in p for p in result.problems)


def test_truncated_archive_fails_verification(tmp_path: Path) -> None:
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(500_000))
    archive, manifest = write_archive(
        tmp_path, lambda w: w.add_file("apps/a/files/config/f.bin", src)
    )
    archive.write_bytes(archive.read_bytes()[:-1000])
    assert not verify(archive, manifest).ok


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Sonarr", "Sonarr"), ("my app/../x", "my_app_.._x"), ("..", "app"), ("", "app")],
)
def test_safe_name(value: str, expected: str) -> None:
    assert safe_name(value) == expected
    assert "/" not in safe_name(value)


@pytest.mark.parametrize(
    ("target", "key"), [("/config", "config"), ("/app/config", "app_config"), ("/", "root")]
)
def test_folder_key(target: str, key: str) -> None:
    assert folder_key(target) == key
