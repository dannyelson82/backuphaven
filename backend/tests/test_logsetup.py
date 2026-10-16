import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backuphaven.logsetup import REDACTED, JsonFormatter, SecretRegistry, configure_logging, redact
from backuphaven.middleware import safe_query


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    yield
    for value in SecretRegistry.values():
        SecretRegistry.unregister(value)


def test_registered_secret_values_are_redacted() -> None:
    SecretRegistry.register("s3cr3t-value-123")
    assert redact("key is s3cr3t-value-123!") == f"key is {REDACTED}!"


def test_short_values_are_not_registered() -> None:
    SecretRegistry.register("abc")  # would redact half the log
    assert redact("abcdef") == "abcdef"


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ("GET /api?apikey=abcdef123456", "abcdef123456"),
        ('{"password": "hunter22"}', "hunter22"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("X-Api-Key: 0123456789abcdef", "0123456789abcdef"),
        ("token=zzz999", "zzz999"),
    ],
)
def test_credential_patterns_are_redacted(text: str, leak: str) -> None:
    assert leak not in redact(text)


def test_json_formatter_includes_extra_fields_and_redacts_them() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.path = "/x?apikey=leakme12345"
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello world"
    assert line["level"] == "info"
    assert "leakme12345" not in line["path"]


def test_safe_query_redacts_credentials_only() -> None:
    assert safe_query(b"page=2&apikey=abc&APIKEY=def") == (
        "page=2&apikey=[REDACTED]&APIKEY=[REDACTED]"
    )


def test_request_log_never_contains_api_key(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="backuphaven.request"):
        client.get("/nothing-here?apikey=supersecretvalue", headers={"X-Api-Key": "hdrsecret99"})
    records = [r for r in caplog.records if r.name == "backuphaven.request"]
    assert len(records) == 1
    rendered = JsonFormatter().format(records[0])
    assert "supersecretvalue" not in rendered
    assert "hdrsecret99" not in rendered
    assert json.loads(rendered)["status"] == 404


def test_healthz_is_not_request_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="backuphaven.request"):
        client.get("/healthz")
    assert not [r for r in caplog.records if r.name == "backuphaven.request"]


def test_configure_logging_writes_json_file(tmp_path: Path) -> None:
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("info", tmp_path / "logs", 1_000_000, 2)
        logging.getLogger("backuphaven.test").info("file check", extra={"n": 1})
        for handler in root.handlers:
            handler.flush()
        lines = (tmp_path / "logs" / "backuphaven.log").read_text().splitlines()
        assert json.loads(lines[-1])["message"] == "file check"
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])
        logging.getLogger("uvicorn.access").disabled = False
