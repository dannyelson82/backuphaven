from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backuphaven.app import create_app
from backuphaven.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "config", backups_dir=tmp_path / "backups")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
