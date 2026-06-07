"""Password hashing utilities.

``PasswordHasher`` is the protocol that callers depend on.
``BcryptHasher`` is the production implementation.

Session tokens use SHA-256 (not bcrypt) because they are already
high-entropy random values; bcrypt would be unnecessary overhead there.
Session token hashing lives in ``auth.sessions``, not here.
"""

from __future__ import annotations

from typing import Protocol


class PasswordHasher(Protocol):
    """Minimal interface for password hashing and verification."""

    def hash_secret(self, secret: str) -> str: ...

    def verify_secret(self, secret: str, secret_hash: str) -> bool: ...


class BcryptHasher:
    """Production password hasher backed by bcrypt.

    Uses a work factor of 12 (bcrypt default).  The work factor can be
    overridden for environments where slower hashing is needed.
    """

    def __init__(self, *, rounds: int = 12) -> None:
        self._rounds = rounds

    def hash_secret(self, secret: str) -> str:
        import bcrypt

        hashed = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(rounds=self._rounds))
        return hashed.decode()

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        import bcrypt

        try:
            return bcrypt.checkpw(secret.encode(), secret_hash.encode())
        except Exception:
            return False


__all__ = ["BcryptHasher", "PasswordHasher"]
