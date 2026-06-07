"""Admin functions for user and service identity lifecycle management.

These are operator/admin operations only — there is no public signup endpoint.
Each function accepts narrow store/hasher protocols so the logic can be tested
with in-memory fakes and wired to real adapters by CLI commands.

Password hashing is delegated to a ``_PasswordHasher`` protocol.  The CLI
currently wires a PBKDF2-based placeholder; Step 4.2.2 will replace it with
a proper bcrypt/Argon2 implementation tied to the ``AuthService`` port.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, Sequence

from urdu_pipeline.application.ports.services import (
    ServiceIdentityRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ServiceIdentityId,
    ServiceIdentityStatus,
    UserId,
    UserStatus,
)


# ---------------------------------------------------------------------------
# Narrow store/hasher protocols
# ---------------------------------------------------------------------------


class _UserAdminStore(Protocol):
    def create_user(self, record: UserRecord) -> None: ...

    def get_user(self, user_id: UserId) -> UserRecord | None: ...

    def update_user(self, record: UserRecord) -> None: ...

    def list_users(self) -> Sequence[UserRecord]: ...


class _ServiceIdentityAdminStore(Protocol):
    def create_service_identity(self, record: ServiceIdentityRecord) -> None: ...

    def get_service_identity(
        self, service_identity_id: ServiceIdentityId
    ) -> ServiceIdentityRecord | None: ...

    def update_service_identity(self, record: ServiceIdentityRecord) -> None: ...


class _PasswordHasher(Protocol):
    """Minimal interface for password hashing."""

    def hash_secret(self, secret: str) -> str: ...


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


def admin_create_user(
    store: _UserAdminStore,
    hasher: _PasswordHasher,
    *,
    username: str,
    password: str,
) -> UserRecord:
    """Create a new active user with a hashed password.

    Fails fast on blank username or password before writing to the store.
    """
    if not username or not username.strip():
        raise ValueError("username must be a non-empty string.")
    if not password or not password.strip():
        raise ValueError("password must be a non-empty string.")

    record = UserRecord(
        user_id=UserId.new(),
        username=username.strip(),
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret(password),
    )
    get_by_username = getattr(store, "get_user_by_username", None)
    if callable(get_by_username):
        existing = get_by_username(record.username)
        if existing is not None:
            updated = replace(
                existing,
                status=UserStatus.ACTIVE,
                password_hash=record.password_hash,
            )
            store.update_user(updated)
            return updated
    store.create_user(record)
    return record


def admin_reset_password(
    store: _UserAdminStore,
    hasher: _PasswordHasher,
    *,
    user_id: UserId,
    new_password: str,
) -> UserRecord:
    """Replace the stored password hash for an existing user.

    Raises ``KeyError`` if the user does not exist.
    Raises ``ValueError`` if ``new_password`` is blank.
    """
    if not new_password or not new_password.strip():
        raise ValueError("password must be a non-empty string.")

    existing = store.get_user(user_id)
    if existing is None:
        raise KeyError(f"user not found: {user_id}")

    updated = replace(existing, password_hash=hasher.hash_secret(new_password))
    store.update_user(updated)
    return updated


def admin_disable_user(
    store: _UserAdminStore,
    *,
    user_id: UserId,
) -> UserRecord:
    """Set a user's status to DISABLED.

    Raises ``KeyError`` if the user does not exist.
    """
    existing = store.get_user(user_id)
    if existing is None:
        raise KeyError(f"user not found: {user_id}")

    updated = replace(existing, status=UserStatus.DISABLED)
    store.update_user(updated)
    return updated


def admin_list_users(store: _UserAdminStore) -> list[UserRecord]:
    """Return all users ordered by creation time."""
    return list(store.list_users())


# ---------------------------------------------------------------------------
# Service identity management
# ---------------------------------------------------------------------------


def admin_revoke_service_identity(
    store: _ServiceIdentityAdminStore,
    *,
    service_identity_id: ServiceIdentityId,
) -> ServiceIdentityRecord:
    """Revoke a service identity, preventing it from authenticating.

    Raises ``KeyError`` if the service identity does not exist.
    """
    existing = store.get_service_identity(service_identity_id)
    if existing is None:
        raise KeyError(f"service identity not found: {service_identity_id}")

    updated = replace(existing, status=ServiceIdentityStatus.REVOKED)
    store.update_service_identity(updated)
    return updated


__all__ = [
    "admin_create_user",
    "admin_disable_user",
    "admin_list_users",
    "admin_reset_password",
    "admin_revoke_service_identity",
]
