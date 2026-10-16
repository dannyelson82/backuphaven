"""Discovery from Unraid templates (ARCHITECTURE.md §6.1, ADR-0009)."""

from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.discovery import (
    Mapping,
    Template,
    TemplateError,
    discover,
    discover_app,
    normalise_host_path,
    parse_template,
)
from tests.helpers import API, admin_client, client_at


def template_xml(name: str, image: str, *paths: tuple[str, str, str]) -> str:
    configs = "\n".join(
        f'  <Config Name="{t}" Target="{t}" Default="" Mode="{mode}" Type="Path">{host}</Config>'
        for t, host, mode in paths
    )
    return f"""<?xml version="1.0"?>
<Container version="2">
  <Name>{name}</Name>
  <Repository>{image}</Repository>
  <Config Name="Port" Target="8989" Default="8989" Mode="tcp" Type="Port">8989</Config>
  <Config Name="PUID" Target="PUID" Default="99" Mode="" Type="Variable">99</Config>
{configs}
</Container>
"""


SONARR = template_xml(
    "sonarr",
    "lscr.io/linuxserver/sonarr",
    ("/config", "/mnt/cache/fast/sonarr", "rw"),
    ("/tv", "/mnt/user/media/tv", "rw"),
    ("/downloads", "/mnt/user/downloads/", "rw"),
)


def test_parse_template_reads_name_image_and_path_mappings_only() -> None:
    template = parse_template(SONARR)
    assert template.name == "sonarr"
    assert template.image == "lscr.io/linuxserver/sonarr"
    assert [m.target for m in template.mappings] == ["/config", "/tv", "/downloads"]
    assert template.mappings[0] == Mapping("/config", "/mnt/cache/fast/sonarr", read_only=False)


def test_parse_template_read_only_and_default_path() -> None:
    xml = template_xml("x", "x/x", ("/boot", "", "ro")).replace(
        'Default="" Mode="ro"', 'Default="/boot" Mode="ro"'
    )
    assert parse_template(xml).mappings == (Mapping("/boot", "/boot", read_only=True),)


@pytest.mark.parametrize(
    "text",
    [
        "not xml at all",
        "<Other><Name>x</Name></Other>",
        "<Container><Repository>x</Repository></Container>",  # no name
        # Entity expansion ("billion laughs") is refused, not expanded.
        '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<Container><Name>&b;</Name></Container>",
        # External entities (reading files from the host) are refused.
        '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        "<Container><Name>&x;</Name></Container>",
    ],
)
def test_bad_templates_are_rejected(text: str) -> None:
    with pytest.raises(TemplateError):
        parse_template(text)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("/mnt/user/appdata/plex", "/mnt/user/appdata/plex"),
        ("/mnt/user0/appdata/plex", "/mnt/user/appdata/plex"),
        ("/mnt/cache/fast/sonarr", "/mnt/user/fast/sonarr"),
        ("/mnt/disk3/appdata/radarr", "/mnt/user/appdata/radarr"),
        ("/mnt/nvme_pool/fast/x", "/mnt/user/fast/x"),
        ("/mnt/user/appdata/plex/", "/mnt/user/appdata/plex"),
        ("/mnt/disks/usb/thing", None),  # unassigned device, not a share
        ("/mnt/remotes/nas/thing", None),
        ("/boot/config", None),
        ("/tmp", None),
        ("relative/path", None),
        ("/mnt/user/appdata/../../etc", None),
        ("/mnt/user", None),
    ],
)
def test_normalise_host_path(host: str, expected: str | None) -> None:
    result = normalise_host_path(host)
    assert (None if result is None else str(result)) == expected


def test_discover_app_sorts_data_from_media_and_missing_shares() -> None:
    template = Template(
        "plex",
        "lscr.io/linuxserver/plex",
        (
            Mapping("/config", "/mnt/cache/fast/plex", read_only=False),
            Mapping("/transcode", "/mnt/user/appdata/plex/transcode", read_only=False),
            Mapping("/movies", "/mnt/user/media/movies", read_only=True),
            Mapping("/media", "/mnt/user/media", read_only=True),  # a whole share
            Mapping("/tmp", "/tmp", read_only=False),
        ),
    )
    app = discover_app(template, frozenset({"appdata"}))
    # On a mounted data share: kept. Config on an unmounted share: flagged, not skipped.
    assert [(f.target, f.share) for f in app.folders] == [("/transcode", "appdata")]
    assert [(m.target, m.host, m.share) for m in app.missing] == [
        ("/config", "/mnt/user/fast/plex", "fast")
    ]
    both = discover_app(template, frozenset({"appdata", "fast"}))
    assert [(f.target, f.host) for f in both.folders] == [
        ("/config", "/mnt/user/fast/plex"),
        ("/transcode", "/mnt/user/appdata/plex/transcode"),
    ]
    assert both.missing == ()


def test_discover_reads_the_template_folder(tmp_path: Path) -> None:
    templates = tmp_path / "templates-user"
    templates.mkdir()
    (templates / "my-sonarr.xml").write_text(SONARR)
    (templates / "my-Radarr.xml").write_text(
        template_xml(
            "Radarr", "lscr.io/linuxserver/radarr", ("/config", "/mnt/user/appdata/radarr", "rw")
        )
    )
    (templates / "broken.xml").write_text("<Container>")
    (templates / "notes.txt").write_text("ignored")
    data_root = tmp_path / "mnt-user"
    (data_root / "appdata").mkdir(parents=True)
    apps = discover(templates, data_root)
    assert [a.name for a in apps] == ["Radarr", "sonarr"]
    radarr, sonarr = apps
    assert [f.host for f in radarr.folders] == ["/mnt/user/appdata/radarr"]
    assert sonarr.folders == () and [m.share for m in sonarr.missing] == ["fast"]
    assert PurePosixPath(sonarr.missing[0].host) == PurePosixPath("/mnt/user/fast/sonarr")


def test_discover_without_templates_folder(tmp_path: Path) -> None:
    assert discover(tmp_path / "nope", tmp_path / "nope") == []


def test_discovered_endpoint_needs_login(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "my-sonarr.xml").write_text(SONARR)
    data_root = tmp_path / "mnt-user"
    (data_root / "fast").mkdir(parents=True)
    settings = Settings(
        config_dir=tmp_path / "config", templates_dir=templates, data_root=data_root
    )
    app = create_app(settings, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        body = admin.get(f"{API}/apps/discovered").json()
        assert body["data_shares"] == ["fast"]
        assert body["templates_found"] is True
        assert body["apps"][0]["folders"] == [
            {"target": "/config", "host": "/mnt/user/fast/sonarr", "share": "fast"}
        ]
        assert client_at(app, "203.0.113.5").get(f"{API}/apps/discovered").status_code == 401
