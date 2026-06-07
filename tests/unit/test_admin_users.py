"""Unit tests for admin user and service identity management — Step 4.2.1.

All functions are tested with in-memory fakes so no real database or hashing
library is required.  A ``_FakeHasher`` stores ``"HASHED:" + plain`` so tests
can assert that (a) hashing was called and (b) the plaintext was not stored.

The real bcrypt hasher is introduced in Step 4.2.2.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

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


# ── in-test fakes ──────────────────────────────────────────────────────────────


class _FakeHasher:
    """Test hasher — prefix plaintext so we can verify hashing was called."""

    def hash_secret(self, secret: str) -> str:
        return "HASHED:" + secret


class _FakeUserAdminStore:
    """Minimal in-memory store for user admin operations."""

    def __init__(self) -> None:
        self._users: dict[UserId, UserRecord] = {}

    def create_user(self, record: UserRecord) -> None:
        self._users[record.user_id] = record

    def get_user(self, user_id: UserId) -> UserRecord | None:
        return self._users.get(user_id)

    def update_user(self, record: UserRecord) -> None:
        if record.user_id not in self._users:
            raise KeyError(f"user not found: {record.user_id}")
        self._users[record.user_id] = record

    def list_users(self) -> list[UserRecord]:
        return sorted(self._users.values(), key=lambda u: u.created_at)


class _FakeServiceIdentityAdminStore:
    """Minimal in-memory store for service identity admin operations."""

    def __init__(self) -> None:
        self._identities: dict[ServiceIdentityId, ServiceIdentityRecord] = {}

    def create_service_identity(self, record: ServiceIdentityRecord) -> None:
        self._identities[record.service_identity_id] = record

    def get_service_identity(
        self, service_identity_id: ServiceIdentityId
    ) -> ServiceIdentityRecord | None:
        return self._identities.get(service_identity_id)

    def update_service_identity(self, record: ServiceIdentityRecord) -> None:
        if record.service_identity_id not in self._identities:
            raise KeyError(f"service identity not found: {record.service_identity_id}")
        self._identities[record.service_identity_id] = record


# ── helper ─────────────────────────────────────────────────────────────────────


def _make_user_store(*users: UserRecord) -> _FakeUserAdminStore:
    store = _FakeUserAdminStore()
    for u in users:
        store.create_user(u)
    return store


# ── TestAdminCreateUser ────────────────────────────────────────────────────────


class TestAdminCreateUser:
    def test_creates_active_user_in_store(self):
        from urdu_pipeline.admin.users import admin_create_user

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()
        record = admin_create_user(store, hasher, username="alice", password="s3cret")

        assert record.username == "alice"
        assert record.status == UserStatus.ACTIVE
        assert store.get_user(record.user_id) == record

    def test_password_is_hashed_not_stored_as_plaintext(self):
        from urdu_pipeline.admin.users import admin_create_user

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()
        record = admin_create_user(store, hasher, username="alice", password="s3cret")

        assert record.password_hash is not None
        # Plaintext must not be stored directly; the hasher output must be stored.
        assert record.password_hash != "s3cret"
        assert record.password_hash == hasher.hash_secret("s3cret")

    def test_assigns_unique_user_ids(self):
        from urdu_pipeline.admin.users import admin_create_user

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()
        a = admin_create_user(store, hasher, username="alice", password="pw1")
        b = admin_create_user(store, hasher, username="bob", password="pw2")

        assert a.user_id != b.user_id

    def test_rejects_blank_username(self):
        from urdu_pipeline.admin.users import admin_create_user

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()

        with pytest.raises(ValueError, match="username"):
            admin_create_user(store, hasher, username="", password="s3cret")

        with pytest.raises(ValueError, match="username"):
            admin_create_user(store, hasher, username="   ", password="s3cret")

    def test_rejects_blank_password(self):
        from urdu_pipeline.admin.users import admin_create_user

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()

        with pytest.raises(ValueError, match="password"):
            admin_create_user(store, hasher, username="alice", password="")

        with pytest.raises(ValueError, match="password"):
            admin_create_user(store, hasher, username="alice", password="   ")


# ── TestAdminResetPassword ─────────────────────────────────────────────────────


class TestAdminResetPassword:
    def _make_user(self) -> UserRecord:
        return UserRecord(
            user_id=UserId.new(),
            username="alice",
            status=UserStatus.ACTIVE,
            password_hash="HASHED:old",
        )

    def test_updates_password_hash_in_store(self):
        from urdu_pipeline.admin.users import admin_reset_password

        user = self._make_user()
        store = _make_user_store(user)
        hasher = _FakeHasher()

        updated = admin_reset_password(
            store, hasher, user_id=user.user_id, new_password="newpass"
        )

        assert updated.password_hash == "HASHED:newpass"
        assert store.get_user(user.user_id).password_hash == "HASHED:newpass"

    def test_hashes_new_password(self):
        from urdu_pipeline.admin.users import admin_reset_password

        user = self._make_user()
        store = _make_user_store(user)
        hasher = _FakeHasher()

        updated = admin_reset_password(
            store, hasher, user_id=user.user_id, new_password="newpass"
        )

        # Plaintext must not be stored directly; the hasher output must be stored.
        assert updated.password_hash != "newpass"
        assert updated.password_hash == hasher.hash_secret("newpass")

    def test_preserves_username_and_status(self):
        from urdu_pipeline.admin.users import admin_reset_password

        user = self._make_user()
        store = _make_user_store(user)

        updated = admin_reset_password(
            store, _FakeHasher(), user_id=user.user_id, new_password="x"
        )

        assert updated.username == user.username
        assert updated.status == user.status

    def test_raises_key_error_if_user_not_found(self):
        from urdu_pipeline.admin.users import admin_reset_password

        store = _FakeUserAdminStore()

        with pytest.raises(KeyError):
            admin_reset_password(
                store, _FakeHasher(), user_id=UserId.new(), new_password="x"
            )

    def test_rejects_blank_new_password(self):
        from urdu_pipeline.admin.users import admin_reset_password

        user = self._make_user()
        store = _make_user_store(user)

        with pytest.raises(ValueError, match="password"):
            admin_reset_password(
                store, _FakeHasher(), user_id=user.user_id, new_password=""
            )


# ── TestAdminDisableUser ───────────────────────────────────────────────────────


class TestAdminDisableUser:
    def _make_user(self) -> UserRecord:
        return UserRecord(
            user_id=UserId.new(),
            username="alice",
            status=UserStatus.ACTIVE,
        )

    def test_sets_status_to_disabled(self):
        from urdu_pipeline.admin.users import admin_disable_user

        user = self._make_user()
        store = _make_user_store(user)

        updated = admin_disable_user(store, user_id=user.user_id)

        assert updated.status == UserStatus.DISABLED
        assert store.get_user(user.user_id).status == UserStatus.DISABLED

    def test_preserves_username_and_password_hash(self):
        from urdu_pipeline.admin.users import admin_disable_user

        user = UserRecord(
            user_id=UserId.new(),
            username="alice",
            status=UserStatus.ACTIVE,
            password_hash="HASHED:pw",
        )
        store = _make_user_store(user)

        updated = admin_disable_user(store, user_id=user.user_id)

        assert updated.username == "alice"
        assert updated.password_hash == "HASHED:pw"

    def test_raises_key_error_if_user_not_found(self):
        from urdu_pipeline.admin.users import admin_disable_user

        store = _FakeUserAdminStore()

        with pytest.raises(KeyError):
            admin_disable_user(store, user_id=UserId.new())


# ── TestAdminListUsers ─────────────────────────────────────────────────────────


class TestAdminListUsers:
    def test_returns_empty_list_when_no_users(self):
        from urdu_pipeline.admin.users import admin_list_users

        store = _FakeUserAdminStore()

        result = admin_list_users(store)

        assert result == []

    def test_returns_all_users(self):
        from urdu_pipeline.admin.users import admin_list_users

        store = _FakeUserAdminStore()
        hasher = _FakeHasher()

        from urdu_pipeline.admin.users import admin_create_user

        alice = admin_create_user(store, hasher, username="alice", password="pw")
        bob = admin_create_user(store, hasher, username="bob", password="pw")

        result = admin_list_users(store)

        user_ids = {r.user_id for r in result}
        assert alice.user_id in user_ids
        assert bob.user_id in user_ids
        assert len(result) == 2


# ── TestAdminRevokeServiceIdentity ─────────────────────────────────────────────


class TestAdminRevokeServiceIdentity:
    def _make_identity(self) -> ServiceIdentityRecord:
        return ServiceIdentityRecord(
            service_identity_id=ServiceIdentityId.new(),
            name="processor",
            status=ServiceIdentityStatus.ACTIVE,
        )

    def test_sets_status_to_revoked(self):
        from urdu_pipeline.admin.users import admin_revoke_service_identity

        identity = self._make_identity()
        store = _FakeServiceIdentityAdminStore()
        store.create_service_identity(identity)

        updated = admin_revoke_service_identity(
            store, service_identity_id=identity.service_identity_id
        )

        assert updated.status == ServiceIdentityStatus.REVOKED
        stored = store.get_service_identity(identity.service_identity_id)
        assert stored.status == ServiceIdentityStatus.REVOKED

    def test_preserves_name(self):
        from urdu_pipeline.admin.users import admin_revoke_service_identity

        identity = self._make_identity()
        store = _FakeServiceIdentityAdminStore()
        store.create_service_identity(identity)

        updated = admin_revoke_service_identity(
            store, service_identity_id=identity.service_identity_id
        )

        assert updated.name == identity.name

    def test_raises_key_error_if_identity_not_found(self):
        from urdu_pipeline.admin.users import admin_revoke_service_identity

        store = _FakeServiceIdentityAdminStore()

        with pytest.raises(KeyError):
            admin_revoke_service_identity(
                store, service_identity_id=ServiceIdentityId.new()
            )
