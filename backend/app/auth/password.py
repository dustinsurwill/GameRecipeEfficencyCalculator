"""
Password hashing via pwdlib (the passlib successor).
Uses Argon2 — stronger than bcrypt for new projects.

pwdlib's API combines verify + rehash into verify_and_update(),
which returns (is_valid, new_hash_or_none).
"""
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# Argon2id — current OWASP recommendation
_hasher = PasswordHash([Argon2Hasher()])


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_and_update(plain: str, hashed: str) -> tuple[bool, str | None]:
    """
    Verify password and optionally return an upgraded hash.

    Returns:
        (is_valid, updated_hash_or_none)
        If updated_hash is not None, persist it — the hash params were outdated.
    """
    return _hasher.verify_and_update(plain, hashed)
