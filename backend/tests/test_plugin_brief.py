"""The plugin development file (ADR-0013)."""

from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from tests.helpers import API, admin_client, client_at
from tests.test_discovery import template_xml
from tests.test_runner import make_db, unraid, write  # noqa: F401 - fixture


def test_development_file_describes_the_app_without_server_details(unraid: Settings) -> None:
    (unraid.templates_dir / "my-mealie.xml").write_text(
        template_xml(
            "mealie",
            "ghcr.io/mealie-recipes/mealie:nightly",
            ("/app/data", "/mnt/user/appdata/mealie-secret-name", "rw"),
            ("/recipes", "/mnt/user/somerecipes", "rw"),
        )
    )
    data = unraid.data_root / "appdata" / "mealie-secret-name"
    make_db(data / "mealie.db")
    write(data / "users" / "u1" / "avatar.png", "png")
    write(data / "cache" / "x.bin", "cache")
    write(data / "pg" / "PG_VERSION", "16")
    write(data / "pg" / "global" / "pg_control", "x")
    app = create_app(unraid, gateways=frozenset())
    with TestClient(app):
        admin = admin_client(app)
        response = admin.get(f"{API}/apps/mealie/plugin-brief")
        assert response.status_code == 200
        assert 'filename="backuphaven-plugin-mealie.md"' in response.headers["content-disposition"]
        text = response.text
        assert "# Plugin development file: mealie" in text
        assert (
            "Write a BackupHaven plugin for **mealie** (image `ghcr.io/mealie-recipes/mealie`)"
            in text
        )
        assert "`/app/data`: app data" in text and "`/recipes`: a **whole share**" in text
        assert "mealie.db" in text and "[SQLite database]" in text
        assert "(cache?)" in text and "PostgreSQL DATA FOLDER" in text
        assert "## 5. Open a pull request" in text  # the guide is appended
        # Host paths and share names stay out of the file.
        assert "mealie-secret-name" not in text and "somerecipes" not in text
        assert "/mnt/user" not in text.split("---", 1)[0]

        existing = admin.get(f"{API}/apps/sonarr/plugin-brief").text
        assert "already uses the **Sonarr** plugin" in existing and "```yaml" in existing
        assert admin.get(f"{API}/apps/nothere/plugin-brief").status_code == 404
        assert (
            client_at(app, "203.0.113.5").get(f"{API}/apps/mealie/plugin-brief").status_code == 401
        )
