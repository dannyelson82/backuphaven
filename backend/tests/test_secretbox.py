import stat
from pathlib import Path

import pytest

from backuphaven.logsetup import SecretRegistry
from backuphaven.secretbox import SecretBox, SecretBoxError


def test_secretbox_creates_private_key_and_round_trips(tmp_path: Path) -> None:
    box = SecretBox(tmp_path)
    assert stat.S_IMODE(box.path.stat().st_mode) == 0o600
    token = box.encrypt("hunter2-api-key")
    assert "hunter2" not in token
    assert SecretBox(tmp_path).decrypt(token) == "hunter2-api-key"  # same key file reused
    assert "hunter2-api-key" in SecretRegistry.values()  # redacted from logs


def test_secretbox_fixes_loose_permissions(tmp_path: Path) -> None:
    SecretBox(tmp_path).path.chmod(0o644)
    box = SecretBox(tmp_path)
    assert stat.S_IMODE(box.path.stat().st_mode) == 0o600


def test_secretbox_wrong_key(tmp_path: Path) -> None:
    token = SecretBox(tmp_path / "a").encrypt("secret-value")
    with pytest.raises(SecretBoxError):
        SecretBox(tmp_path / "b").decrypt(token)
