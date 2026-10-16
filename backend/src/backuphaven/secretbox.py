"""Encryption of third-party secrets at rest (SECURITY.md "Secrets").

A random key is generated on first run and kept in ``<config>/secret.key``
(mode 0600). Secrets such as alert tokens and plugin secrets are stored encrypted
with it (Fernet: AES-128-CBC + HMAC-SHA256) and decrypted only when used.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backuphaven.logsetup import SecretRegistry

logger = logging.getLogger(__name__)

KEY_FILE = "secret.key"


class SecretBoxError(Exception):
    """A secret couldn't be decrypted (wrong or replaced key file)."""


class SecretBox:
    def __init__(self, config_dir: Path) -> None:
        self.path = config_dir / KEY_FILE
        self._fernet = Fernet(self._load_or_create())

    def _load_or_create(self) -> bytes:
        if self.path.exists():
            if self.path.stat().st_mode & 0o077:
                logger.warning("secret.key was readable by others; restricting to owner")
                self.path.chmod(0o600)
            return self.path.read_bytes().strip()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # O_EXCL: never overwrite a key another process created meanwhile.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        logger.info("created a new secret key", extra={"path": str(self.path)})
        return key

    def encrypt(self, plaintext: str) -> str:
        SecretRegistry.register(plaintext)
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            value = self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretBoxError("stored secret can't be decrypted with secret.key") from exc
        SecretRegistry.register(value)
        return value
