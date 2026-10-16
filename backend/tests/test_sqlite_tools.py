"""Live SQLite snapshots and checks (ARCHITECTURE.md §6.2, §6.7)."""

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from backuphaven.sqlite_tools import SnapshotError, check, snapshot


def make_db(path: Path, rows: int = 200, wal: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("CREATE INDEX items_name ON items(name)")
    con.executemany("INSERT INTO items(name) VALUES (?)", [(f"item {i}",) for i in range(rows)])
    con.commit()
    con.close()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return int(con.execute("SELECT count(*) FROM items").fetchone()[0])
    finally:
        con.close()


def test_stopped_app_database_is_copied_without_touching_its_folder(tmp_path: Path) -> None:
    app = tmp_path / "app"
    make_db(app / "app.db")
    before_files = sorted(p.name for p in app.iterdir())
    before_hash = digest(app / "app.db")
    result = snapshot(app / "app.db", tmp_path / "copy.db")
    assert result.method == "immutable"
    assert sorted(p.name for p in app.iterdir()) == before_files == ["app.db"]
    assert digest(app / "app.db") == before_hash
    assert count(tmp_path / "copy.db") == 200
    assert check(tmp_path / "copy.db").status == "ok"


def test_running_app_database_includes_changes_still_in_its_wal(tmp_path: Path) -> None:
    db = tmp_path / "app" / "app.db"
    make_db(db)
    app = sqlite3.connect(db)  # the app, still running: its -wal and -shm exist
    app.execute("PRAGMA wal_autocheckpoint=0")
    app.execute("INSERT INTO items(name) VALUES ('only in the wal')")
    app.commit()
    try:
        result = snapshot(db, tmp_path / "copy.db")
        assert result.method == "live"
        assert count(tmp_path / "copy.db") == 201
    finally:
        app.close()


def test_snapshot_is_consistent_while_the_app_keeps_writing(tmp_path: Path) -> None:
    db = tmp_path / "app" / "app.db"
    make_db(db, rows=5_000)
    stop = threading.Event()

    def writer() -> None:
        con = sqlite3.connect(db)
        while not stop.is_set():
            con.execute("INSERT INTO items(name) VALUES ('new')")
            con.execute("UPDATE items SET name = name || '.' WHERE id % 97 = 0")
            con.commit()
        con.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for i in range(5):
            copy = tmp_path / f"copy{i}.db"
            snapshot(db, copy)
            assert check(copy).status == "ok"
            assert count(copy) >= 5_000
    finally:
        stop.set()
        thread.join()


def test_paths_with_spaces_and_url_characters(tmp_path: Path) -> None:
    db = tmp_path / "Plex Media Server" / "Plug-in Support #1" / "lib?rary.db"
    make_db(db)
    snapshot(db, tmp_path / "copy.db")
    assert check(tmp_path / "copy.db").status == "ok"


def test_damaged_database_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    make_db(db, rows=5_000, wal=False)
    data = bytearray(db.read_bytes())
    page = 4096
    data[page * 3 : page * 3 + 200] = b"\xde\xad" * 100  # scribble over a b-tree page
    db.write_bytes(bytes(data))
    try:
        snapshot(db, tmp_path / "copy.db")
    except SnapshotError as exc:  # damage the copy itself can't get past
        assert exc.kind == "damaged"
        return
    result = check(tmp_path / "copy.db")
    assert result.status == "damaged"
    assert result.messages


def test_a_file_that_is_not_a_database(tmp_path: Path) -> None:
    fake = tmp_path / "fake.db"
    fake.write_bytes(b"definitely not sqlite" * 300)
    with pytest.raises(SnapshotError) as info:
        snapshot(fake, tmp_path / "copy.db")
    assert info.value.kind == "damaged"
    assert not (tmp_path / "copy.db").exists()


def test_missing_database(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError) as info:
        snapshot(tmp_path / "gone.db", tmp_path / "copy.db")
    assert info.value.kind == "unreadable"


def test_unknown_collation_falls_back_to_the_structure_check(tmp_path: Path) -> None:
    db = tmp_path / "plexlike.db"
    con = sqlite3.connect(db)
    con.create_collation("naturalsort", lambda a, b: (b > a) - (b < a))
    con.execute("CREATE TABLE t (title TEXT)")
    con.execute("CREATE INDEX t_title ON t(title COLLATE naturalsort)")
    con.executemany("INSERT INTO t VALUES (?)", [(chr(97 + i) * 3,) for i in range(26)])
    con.commit()
    con.close()
    snapshot(db, tmp_path / "copy.db")
    result = check(tmp_path / "copy.db")
    assert result.status == "ok"
    assert result.mode == "quick"
    assert result.unknown_collations == ("naturalsort",)


def test_large_databases_get_the_quick_check(tmp_path: Path) -> None:
    make_db(tmp_path / "app.db")
    assert check(tmp_path / "app.db", full_max_bytes=1).mode == "quick"


def test_a_check_that_takes_too_long_is_unchecked_not_damaged(tmp_path: Path) -> None:
    make_db(tmp_path / "app.db", rows=50_000, wal=False)
    result = check(tmp_path / "app.db", timeout_s=0)
    assert result.status == "unchecked"
    assert result.messages == ("check took too long",)
