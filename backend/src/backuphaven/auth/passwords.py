"""Argon2id password hashing."""

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 10
MAX_LENGTH = 256  # hashing huge inputs is a cheap denial-of-service

_hasher = PasswordHasher()  # argon2id with RFC 9106 parameters


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("backuphaven-timing-equaliser")


def burn_verify_time(password: str) -> None:
    """Spend the same time as a real check, so unknown usernames aren't detectable."""
    verify_password(_dummy_hash(), password)
