"""Browsing a backup and downloading from it (phase 0.3 chunk 2)."""

import io
import tarfile
import time

import zstandard
from fastapi.testclient import TestClient

from backuphaven import retention
from backuphaven.app import create_app
from backuphaven.config import Settings
from tests.helpers import API, admin_client, client_at
from tests.test_runner import unraid  # noqa: F401 - fixture


def _backup(app, admin) -> int:  # type: ignore[no-untyped-def]
    with app.state.db.write() as session:
        retention.save_settings(session, retention.KeepingSettings(min_free_gb=0))
    admin.put(f"{API}/schedule/apps", json={"apps": ["sonarr", "Tool"]})
    run_id: int = admin.post(f"{API}/runs").json()["run_id"]
    for _ in range(200):
        if not app.state.runner.running:
            break
        time.sleep(0.05)
    return run_id


def test_contents_entries_and_downloads(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        run_id = _backup(app, admin)

        contents = admin.get(f"{API}/backups/{run_id}/contents").json()
        apps = {a["name"]: a for a in contents["apps"]}
        assert apps["sonarr"]["key"] == "sonarr" and apps["Tool"]["key"] == "Tool"
        assert apps["sonarr"]["databases"][0]["path"] == "sonarr.db"

        entries = {
            e["path"]: e for e in admin.get(f"{API}/backups/{run_id}/apps/sonarr/entries").json()
        }
        assert "files/config/config.xml" in entries and "databases/config/sonarr.db" in entries
        assert "template.xml" in entries

        whole = admin.get(f"{API}/backups/{run_id}/download")
        assert whole.status_code == 200 and "attachment" in whole.headers["content-disposition"]
        archive = unraid.backups_dir / contents["archive"]
        assert whole.content == archive.read_bytes()

        one_app = admin.get(f"{API}/backups/{run_id}/apps/sonarr/download")
        assert one_app.status_code == 200
        assert 'filename="sonarr-' in one_app.headers["content-disposition"]
        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(one_app.content))
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            names: dict[str, bytes] = {}
            for info in tar:
                data = tar.extractfile(info)
                names[info.name] = data.read() if data is not None else b""
        assert names["sonarr/files/config/config.xml"].startswith(b"<Config>")
        assert "sonarr/databases/config/sonarr.db" in names
        assert not any(n.startswith("Tool/") for n in names)

        one_file = admin.get(
            f"{API}/backups/{run_id}/apps/sonarr/file", params={"path": "files/config/config.xml"}
        )
        assert one_file.status_code == 200 and one_file.content.startswith(b"<Config>")
        assert 'filename="config.xml"' in one_file.headers["content-disposition"]


def test_downloads_refuse_bad_requests(unraid: Settings) -> None:
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        run_id = _backup(app, admin)
        base = f"{API}/backups/{run_id}"
        assert (
            admin.get(
                f"{base}/apps/sonarr/file",
                params={"path": "../Tool/files/data_config/settings.json"},
            ).status_code
            == 400
        )
        assert (
            admin.get(f"{base}/apps/sonarr/file", params={"path": "files/config/nope"}).status_code
            == 404
        )
        assert admin.get(f"{base}/apps/nothere/download").status_code == 404
        assert admin.get(f"{API}/backups/9999/contents").status_code == 404
        assert client_at(app, "203.0.113.5").get(f"{base}/download").status_code == 401
        archive = admin.get(f"{base}/contents").json()["archive"]
        (unraid.backups_dir / archive).unlink()
        assert admin.get(f"{base}/download").json()["detail"] == "backup_file_missing"
