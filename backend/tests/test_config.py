import pytest

from backuphaven.config import Settings


def test_defaults_are_development_folders() -> None:
    settings = Settings()
    assert settings.port == 7272
    assert settings.docker_proxy is None
    assert str(settings.config_dir) == ".dev-config"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUPHAVEN_PORT", "8000")
    monkeypatch.setenv("BACKUPHAVEN_DOCKER_PROXY", "tcp://socket-proxy:2375")
    settings = Settings()
    assert settings.port == 8000
    assert settings.docker_proxy == "tcp://socket-proxy:2375"
