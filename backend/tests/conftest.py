from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings
from backuphaven.db import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "config", backups_dir=tmp_path / "backups")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, gateways=frozenset())) as test_client:  # runs migrations
        yield test_client


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "test.db")
    database.migrate()
    yield database
    database.close()
