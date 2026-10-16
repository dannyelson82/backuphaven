"""Random tokens and their stored hashes."""

import hashlib
import hmac
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def new_api_key() -> str:
    return secrets.token_hex(32)  # 32 random bytes (SECURITY.md)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 is right for high-entropy random tokens (ReelHaven ADR-0011)."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
