"""Alert channels (phase 0.4 chunk 2) against fake servers."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from backuphaven import alert_channels
from backuphaven.alert_channels import ChannelConfig, LiveChannel
from backuphaven.logsetup import redact


class Recorder(BaseHTTPRequestHandler):
    received: ClassVar[list[dict[str, Any]]] = []
    status = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        Recorder.received.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": self.rfile.read(length),
            }
        )
        self.send_response(Recorder.status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Recorder.received, Recorder.status = [], 200
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def channel(
    type_: str, settings: dict[str, Any] | None = None, secret: str | None = None
) -> LiveChannel:
    config = ChannelConfig(type=type_, name=f"my {type_}", settings=settings or {})  # type: ignore[arg-type]
    alert_channels.validate(config, secret)
    return LiveChannel(config, secret)


def test_ntfy(server: str) -> None:
    channel("ntfy", {"server": server, "topic": "backups"}, "tk_secret123").send(
        "Backup failed", "sonarr: damaged", "open"
    )
    [req] = Recorder.received
    assert req["path"] == "/backups" and req["body"] == b"sonarr: damaged"
    assert req["headers"]["title"] == "Backup failed" and req["headers"]["priority"] == "high"
    assert req["headers"]["authorization"] == "Bearer tk_secret123"


def test_discord_and_home_assistant_post_json(server: str) -> None:
    channel("discord", secret=f"{server}/api/webhooks/1/abc").send(
        "Recovered: X", "fine", "recovered"
    )
    channel("homeassistant", secret=f"{server}/api/webhook/bh-secret").send("T", "M", "open")
    discord, ha = Recorder.received
    assert discord["path"] == "/api/webhooks/1/abc"
    embed = json.loads(discord["body"])["embeds"][0]
    assert embed["title"] == "Recovered: X" and embed["color"] == 0x2F9E44
    assert json.loads(ha["body"]) == {
        "title": "T",
        "message": "M",
        "state": "open",
        "source": "backuphaven",
    }


def test_unraid_notification(server: str) -> None:
    channel("unraid", {"url": f"{server}/graphql"}, "unraid-api-key-1").send(
        "Backup missed", "didn't run", "open"
    )
    [req] = Recorder.received
    assert req["headers"]["x-api-key"] == "unraid-api-key-1"
    body = json.loads(req["body"])
    assert "createNotification" in body["query"]
    assert body["variables"]["input"]["importance"] == "ALERT"
    assert body["variables"]["input"]["subject"] == "Backup missed"


def test_errors_say_what_went_wrong(server: str) -> None:
    Recorder.status = 404
    with pytest.raises(RuntimeError, match="answered 404"):
        channel("ntfy", {"server": server, "topic": "x"}).send("t", "m", "open")
    with pytest.raises(RuntimeError, match="can't reach"):
        channel("ntfy", {"server": "http://127.0.0.1:9", "topic": "x"}).send("t", "m", "open")


def test_email(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            sent["server"] = (host, port)

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *_a: object) -> None:
            pass

        def starttls(self, context: object) -> None:
            sent["tls"] = True

        def login(self, user: str, password: str) -> None:
            sent["login"] = (user, password)

        def send_message(self, mail: Any) -> None:
            sent["subject"], sent["to"] = mail["Subject"], mail["To"]

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    settings = {
        "host": "smtp.example.com",
        "username": "me",
        "sender": "bh@example.com",
        "to": "me@example.com",
    }
    channel("email", settings, "mail-password").send("Disk nearly full", "2 GB left", "open")
    assert sent == {
        "server": ("smtp.example.com", 587),
        "tls": True,
        "login": ("me", "mail-password"),
        "subject": "BackupHaven: Disk nearly full",
        "to": "me@example.com",
    }


@pytest.mark.parametrize(
    ("type_", "settings", "secret"),
    [
        ("ntfy", {"server": "file:///etc/passwd", "topic": "x"}, None),
        ("ntfy", {"topic": "bad topic!"}, None),
        ("discord", {}, "ftp://example.com/hook"),
        ("discord", {"extra": 1}, "https://example.com/hook"),
        ("unraid", {"url": "javascript:alert(1)"}, "key"),
        ("email", {"host": "x"}, None),
    ],
)
def test_bad_settings_are_refused(type_: str, settings: dict[str, Any], secret: str | None) -> None:
    with pytest.raises(ValueError):
        channel(type_, settings, secret)


def test_secrets_are_kept_out_of_logs() -> None:
    channel("ntfy", {"topic": "x"}, "super-secret-token-9")
    assert "super-secret-token-9" not in redact("sending with super-secret-token-9")


def test_channels_load_with_their_secrets_decrypted(tmp_path: Path) -> None:
    from backuphaven.db import Database
    from backuphaven.secretbox import SecretBox

    db = Database(tmp_path / "b.db")
    db.migrate()
    box = SecretBox(tmp_path)
    stored = ChannelConfig(type="discord", name="d", secret=box.encrypt("https://example.com/hook"))
    with db.write() as session:
        alert_channels.save(session, alert_channels.AlertSettings(channels=[stored]))
    with db.read() as session:
        [live] = alert_channels.live_channels(session, box)
    assert live.name == "d" and live.wants("run_failed") and not live.wants("summary")
