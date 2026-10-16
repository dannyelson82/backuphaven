"""Plugin manifests, bundled recipes, matching and file selection (ADR-0003)."""

import os
import sqlite3
from pathlib import Path

import pytest

from backuphaven.plugins.manifest import ManifestError, parse_manifest
from backuphaven.plugins.registry import BUNDLED_DIR, bundled, image_name, load_folder
from backuphaven.selection import (
    OutsideShareError,
    Rules,
    could_contain,
    effective_rules,
    resolve_app_folder,
    select,
)

MINIMAL = """
id: demo
name: Demo
version: 1.0.0
kind: app
plugin_api: 1
author: Someone
"""


def make_db(path: Path, wal: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- manifests --------------------------------------------------------------------------


def test_minimal_manifest_gets_safe_defaults() -> None:
    manifest = parse_manifest(MINIMAL)
    assert manifest.backup.mode == "snapshot"
    assert manifest.backup.folder == "/config"
    assert manifest.restore.stop_first is True


def test_folder_patterns_mean_everything_inside() -> None:
    manifest = parse_manifest(MINIMAL + "backup:\n  include: ['Backups/', 'a/b']\n")
    assert manifest.backup.include == ["Backups/**", "a/b"]


@pytest.mark.parametrize(
    "extra",
    [
        "backup:\n  include: ['../other-app/**']\n",  # leaves the folder
        "backup:\n  include: ['/etc/passwd']\n",  # absolute
        "backup:\n  exclude: ['a/../../b']\n",
        "backup:\n  databases: ['..\\\\x.db']\n",  # backslashes
        "backup:\n  expect: ['a/../..']\n",
        "backup:\n  folder: '/config/../..'\n",
        "settings:\n  - {id: s, label: S, when_true: {include: ['../x']}}\n",
        "backup:\n  run: 'rm -rf /'\n",  # unknown fields are refused
        "kind: destination\n",  # not in plugin API v1 yet
    ],
)
def test_manifests_that_reach_outside_or_do_unknown_things_are_refused(extra: str) -> None:
    text = MINIMAL.replace("kind: app\n", "") if extra.startswith("kind") else MINIMAL
    with pytest.raises(ManifestError):
        parse_manifest(text + extra)


def test_yaml_tags_are_not_executed() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(MINIMAL + "description: !!python/object/apply:os.system ['echo hi']\n")


@pytest.mark.parametrize("text", ["- just\n- a list\n", "a: [unclosed", "x" * 70_000])
def test_malformed_manifests(text: str) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(text)


def test_every_bundled_plugin_is_valid() -> None:
    files = sorted(p.stem for p in BUNDLED_DIR.glob("*.yaml"))
    assert sorted(load_folder(BUNDLED_DIR)) == files
    assert {"sonarr", "radarr", "prowlarr", "plex", "backuphaven", "planhaven"} <= set(files)
    assert "generic" not in files


# --- matching ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("image", "name"),
    [
        ("lscr.io/linuxserver/sonarr:latest", "lscr.io/linuxserver/sonarr"),
        ("ghcr.io/hotio/sonarr:release@sha256:abc", "ghcr.io/hotio/sonarr"),
        ("registry.local:5000/sonarr", "registry.local:5000/sonarr"),
        ("Sonarr", "sonarr"),
    ],
)
def test_image_name(image: str, name: str) -> None:
    assert image_name(image) == name


@pytest.mark.parametrize(
    ("image", "plugin"),
    [
        ("lscr.io/linuxserver/sonarr", "sonarr"),
        ("ghcr.io/hotio/radarr:release", "radarr"),
        ("lscr.io/linuxserver/prowlarr:develop", "prowlarr"),
        ("lscr.io/linuxserver/plex", "plex"),
        ("plexinc/pms-docker:latest", "plex"),
        ("ghcr.io/dannyelson82/backuphaven:edge", "backuphaven"),
        ("lscr.io/linuxserver/sonarr-extended", None),  # not Sonarr itself
        ("mariadb", None),
        ("", None),
    ],
)
def test_apps_match_their_plugin_or_none(image: str, plugin: str | None) -> None:
    """Plugins only (ADR-0013): no plugin, no backup."""
    found = bundled().match(image)
    assert (found.id if found else None) == plugin


# --- rules and selection ----------------------------------------------------------------


def test_settings_add_folders_and_lift_their_exclusion() -> None:
    sonarr = bundled().plugins["sonarr"]
    off = effective_rules(sonarr)
    assert "MediaCover/**" in off.exclude
    on = effective_rules(sonarr, {"covers": True})
    assert "MediaCover/**" in on.include and "MediaCover/**" not in on.exclude


def test_sonarr_layout(tmp_path: Path) -> None:
    root = tmp_path / "sonarr"
    write(root / "config.xml", "<Config/>")
    make_db(root / "sonarr.db", wal=True)
    write(root / "sonarr.db-wal", "pending")  # captured by the snapshot instead
    write(root / "sonarr.db-shm", "shm")
    make_db(root / "logs.db")
    write(root / "logs" / "sonarr.txt")
    write(root / "MediaCover" / "1" / "poster.jpg", "jpg" * 100)
    write(root / "Backups" / "scheduled" / "sonarr_backup.zip")
    write(root / "asp" / "key-1.xml")
    selection = select(root, effective_rules(bundled().plugins["sonarr"]))
    assert [e.rel for e in selection.databases] == ["sonarr.db"]
    assert sorted(e.rel for e in selection.files) == ["asp/key-1.xml", "config.xml"]
    assert selection.missing_expected == []
    covers = select(root, effective_rules(bundled().plugins["sonarr"], {"covers": True}))
    assert "MediaCover/1/poster.jpg" in [e.rel for e in covers.files]


def test_plex_takes_databases_and_settings_but_not_caches(tmp_path: Path) -> None:
    root = tmp_path / "plex"
    pms = root / "Library" / "Application Support" / "Plex Media Server"
    write(pms / "Preferences.xml", "<Preferences/>")
    dbs = pms / "Plug-in Support" / "Databases"
    make_db(dbs / "com.plexapp.plugins.library.db")
    make_db(dbs / "com.plexapp.plugins.library.blobs.db")
    make_db(dbs / "com.plexapp.plugins.library.db-2026-10-10")  # Plex's own dated copy
    write(pms / "Plug-in Support" / "Preferences" / "com.plexapp.agents.x.xml")
    write(pms / "Cache" / "big.bin")
    write(pms / "Metadata" / "Movies" / "a" / "poster.jpg")
    write(pms / "Logs" / "Plex Media Server.log")
    selection = select(root, effective_rules(bundled().plugins["plex"]))
    base = "Library/Application Support/Plex Media Server"
    assert sorted(e.rel for e in selection.databases) == [
        f"{base}/Plug-in Support/Databases/com.plexapp.plugins.library.blobs.db",
        f"{base}/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    ]
    assert sorted(e.rel for e in selection.files) == [
        f"{base}/Plug-in Support/Preferences/com.plexapp.agents.x.xml",
        f"{base}/Preferences.xml",
    ]
    assert selection.missing_expected == []


def test_plex_never_walks_its_poster_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plex's Metadata and Media folders can hold hundreds of thousands of files;
    walking them took minutes on a real server although none are wanted."""
    root = tmp_path / "plex"
    pms = root / "Library" / "Application Support" / "Plex Media Server"
    write(pms / "Preferences.xml", "<Preferences/>")
    make_db(pms / "Plug-in Support" / "Databases" / "com.plexapp.plugins.library.db")
    write(pms / "Metadata" / "Movies" / "a" / "poster.jpg")
    write(pms / "Media" / "localhost" / "b" / "thumb.jpg")
    walked: list[str] = []
    real_walk = os.walk

    def spy(top, **kwargs):  # type: ignore[no-untyped-def]
        for dirpath, dirnames, filenames in real_walk(top, **kwargs):
            walked.append(os.path.relpath(dirpath, root))
            yield dirpath, dirnames, filenames

    monkeypatch.setattr("backuphaven.selection.os.walk", spy)
    plex = bundled().plugins["plex"]
    select(root, effective_rules(plex))
    assert not any("Metadata" in d or "Media/" in d or d.endswith("/Media") for d in walked)

    walked.clear()  # with posters switched on, they are read
    chosen = select(root, effective_rules(plex, {"metadata": True}))
    assert any(d.endswith("Metadata/Movies/a") for d in walked)
    assert any(e.rel.endswith("poster.jpg") for e in chosen.files)


@pytest.mark.parametrize(
    ("folder", "patterns", "expected"),
    [
        ("Library", ("Library/App/Prefs.xml",), True),
        ("Library/App", ("Library/App/Prefs.xml",), True),
        ("Library/App/Metadata", ("Library/App/Prefs.xml",), False),
        ("Library/Other", ("Library/App/**",), False),
        ("Library/App/a/b/c", ("Library/App/**",), True),
        ("Library/App/Databases", ("Library/App/Databases/*.db",), True),
        ("Library/App/Databases/old", ("Library/App/Databases/*.db",), False),
        ("anything/deep", ("**",), True),
        ("x/y", ("**/*.db",), True),
        ("Library/Ap1", ("Library/Ap[0-9]/**",), True),
        ("Other", ("Library/**", "Other/x.json"), True),
    ],
)
def test_could_contain(folder: str, patterns: tuple[str, ...], expected: bool) -> None:
    assert could_contain(folder, patterns) is expected


def test_auto_databases_are_found_by_their_header(tmp_path: Path) -> None:
    root = tmp_path / "app"
    make_db(root / "data" / "store.sqlite")
    make_db(root / "weird-name.bin")
    write(root / "settings.json", "{}")
    write(root / "notes.db", "not really sqlite")
    write(root / "cache" / "x")
    write(root / "logs" / "app.log")
    selection = select(
        root,
        effective_rules(
            parse_manifest(
                MINIMAL
                + 'backup: {folder: "*", databases: auto, include: ["**"],\n'
                + '  exclude: ["cache/**", "logs/**"]}\n'
            )
        ),
    )
    assert sorted(e.rel for e in selection.databases) == ["data/store.sqlite", "weird-name.bin"]
    assert sorted(e.rel for e in selection.files) == ["notes.db", "settings.json"]


def test_links_are_kept_as_links_and_special_files_skipped(tmp_path: Path) -> None:
    root = tmp_path / "app"
    write(root / "real.txt")
    outside = tmp_path / "secret"
    write(outside / "passwords.txt")
    (root / "link-out").symlink_to(outside)  # a link to a folder outside the app
    (root / "file-link").symlink_to(outside / "passwords.txt")
    os.mkfifo(root / "pipe")
    selection = select(root, Rules(("**",), (), "auto", ()))
    assert sorted((e.rel, e.kind) for e in selection.files) == [
        ("file-link", "symlink"),
        ("link-out", "symlink"),
        ("real.txt", "file"),
    ]
    assert selection.skipped_special == ["pipe"]
    assert all("passwords" not in e.rel for e in selection.files)  # never walked into


def test_missing_expected_files_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "radarr"
    write(root / "config.xml")
    selection = select(root, effective_rules(bundled().plugins["radarr"]))
    assert selection.missing_expected == ["radarr.db"]


def test_data_folders_must_stay_inside_their_share(tmp_path: Path) -> None:
    data_root = tmp_path / "mnt-user"
    (data_root / "appdata" / "sonarr").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (data_root / "appdata" / "escape").symlink_to(tmp_path / "elsewhere")
    ok = resolve_app_folder(data_root, "/mnt/user/appdata/sonarr")
    assert ok == (data_root / "appdata" / "sonarr").resolve()
    with pytest.raises(OutsideShareError):
        resolve_app_folder(data_root, "/mnt/user/appdata/escape")


def test_local_plugins_appear_without_a_restart_and_can_replace_bundled_ones(
    tmp_path: Path,
) -> None:
    from backuphaven.plugins.registry import PluginStore

    store = PluginStore(tmp_path / "plugins")
    assert store.match("someone/newapp") is None
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "newapp.yaml").write_text(
        MINIMAL.replace("id: demo", "id: newapp") + 'recognise: {images: ["someone/newapp"]}\n'
    )
    found = store.match("someone/newapp")
    assert found is not None and found.id == "newapp" and "newapp" in store.local
    # A local copy of a bundled plugin wins, so an improved one can be tried.
    (tmp_path / "plugins" / "sonarr.yaml").write_text(
        MINIMAL.replace("id: demo", "id: sonarr").replace("Demo", "Sonarr (trying)")
        + 'recognise: {images: ["*/sonarr"]}\n'
    )
    sonarr = store.match("lscr.io/linuxserver/sonarr")
    assert sonarr is not None and sonarr.name == "Sonarr (trying)"
    (tmp_path / "plugins" / "broken.yaml").write_text("id: Not Valid\n")
    assert store.match("someone/newapp") is not None  # a bad file is skipped
