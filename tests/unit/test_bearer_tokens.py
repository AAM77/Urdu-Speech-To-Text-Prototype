"""Unit tests for bearer token lifecycle — Step 4.2.3.

Written BEFORE implementation (TDD).  All tests must fail at import until
``urdu_pipeline.auth.bearer`` and its data model exist.

Covers the four requirements from the plan:
  1. Tokens are shown once — ``create_bearer_token`` returns the raw token;
     only the hash is stored.
  2. Hashed at rest — the stored ``token_hash`` is the SHA-256 of raw token,
     not the raw value itself.
  3. Revocable — ``revoke_bearer_token`` marks the token; subsequent
     ``resolve_bearer_token`` returns None.
  4. Expiry — tokens past their ``expires_at`` resolve to None.
  5. ``last_used_at`` updated — a successful resolve mutates the stored record.

All tests use a minimal fake store defined in this file so they run without
a database or the real InMemoryMetadataStore.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone

import pytest

from urdu_pipeline.domain import UserId


# ── Narrow fake store used only in this test file ────────────────────────────
# Written against the narrow _BearerStore protocol that auth/bearer.py will
# define, NOT against MetadataStore.  This keeps unit tests decoupled from
# infrastructure.


class _FakeBearerStore:
    """In-process bearer token store for unit tests."""

    def __init__(self) -> None:
        self._by_hash: dict[str, object] = {}
        self._by_id: dict[str, object] = {}
        self._by_user: dict[str, list[object]] = {}

    def create_bearer_token(self, record) -> None:
        self._by_hash[record.token_hash] = record
        self._by_id[str(record.token_id)] = record
        self._by_user.setdefault(str(record.user_id), []).append(record)

    def get_bearer_token_by_hash(self, token_hash: str):
        return self._by_hash.get(token_hash)

    def get_bearer_token(self, token_id):
        return self._by_id.get(str(token_id))

    def update_bearer_token(self, record) -> None:
        old = self._by_id.get(str(record.token_id))
        if old is None:
            raise KeyError(f"token not found: {record.token_id}")
        self._by_hash[record.token_hash] = record
        self._by_id[str(record.token_id)] = record
        user_list = self._by_user.get(str(record.user_id), [])
        self._by_user[str(record.user_id)] = [
            record if r is old else r for r in user_list
        ]

    def list_bearer_tokens_for_user(self, user_id) -> list:
        return list(self._by_user.get(str(user_id), []))


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── TestCreateBearerToken ─────────────────────────────────────────────────────


class TestCreateBearerToken:
    def test_returns_raw_token_and_record(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token"
        )

        assert isinstance(raw_token, str)
        assert len(raw_token) > 0

    def test_raw_token_is_64_hex_characters(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        raw_token, _ = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token"
        )

        assert len(raw_token) == 64
        assert all(c in "0123456789abcdef" for c in raw_token)

    def test_token_hash_stored_is_sha256_of_raw_token(self):
        """Token shown once: the stored hash is the SHA-256, not the raw value."""
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token"
        )

        assert record.token_hash == _sha256(raw_token)

    def test_raw_token_not_in_stored_record(self):
        """The raw token must never be stored at rest."""
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token"
        )

        assert record.token_hash != raw_token

    def test_record_retrievable_by_token_hash(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token"
        )

        stored = store.get_bearer_token_by_hash(_sha256(raw_token))
        assert stored is not None
        assert stored.token_hash == record.token_hash

    def test_token_not_revoked_on_creation(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        _, record = create_bearer_token(store, user_id=UserId.new(), name="ci-token")

        assert record.revoked_at is None

    def test_last_used_at_is_none_on_creation(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        _, record = create_bearer_token(store, user_id=UserId.new(), name="ci-token")

        assert record.last_used_at is None

    def test_token_with_expiry_sets_expires_at(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        _, record = create_bearer_token(
            store,
            user_id=UserId.new(),
            name="ci-token",
            expires_in=timedelta(days=30),
        )

        assert record.expires_at is not None
        expected = datetime.now(tz=timezone.utc) + timedelta(days=30)
        delta = abs((record.expires_at - expected).total_seconds())
        assert delta < 2.0

    def test_token_without_expiry_has_none_expires_at(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        _, record = create_bearer_token(
            store, user_id=UserId.new(), name="ci-token", expires_in=None
        )

        assert record.expires_at is None

    def test_each_call_produces_different_raw_token(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        user_id = UserId.new()
        token_a, _ = create_bearer_token(store, user_id=user_id, name="a")
        token_b, _ = create_bearer_token(store, user_id=user_id, name="b")

        assert token_a != token_b

    def test_name_is_stored_in_record(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _FakeBearerStore()
        _, record = create_bearer_token(store, user_id=UserId.new(), name="my-token")

        assert record.name == "my-token"


# ── TestResolveBearerToken ────────────────────────────────────────────────────


class TestResolveBearerToken:
    def test_returns_auth_principal_for_valid_token(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token
        from urdu_pipeline.application.ports.services import AuthPrincipal

        store = _FakeBearerStore()
        raw_token, _ = create_bearer_token(store, user_id=UserId.new(), name="tok")

        principal = resolve_bearer_token(store, raw_token=raw_token)

        assert principal is not None
        assert isinstance(principal, AuthPrincipal)

    def test_principal_kind_is_user(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token

        store = _FakeBearerStore()
        raw_token, _ = create_bearer_token(store, user_id=UserId.new(), name="tok")

        principal = resolve_bearer_token(store, raw_token=raw_token)

        assert principal.kind == "user"

    def test_principal_id_matches_user_id(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token

        store = _FakeBearerStore()
        user_id = UserId.new()
        raw_token, _ = create_bearer_token(store, user_id=user_id, name="tok")

        principal = resolve_bearer_token(store, raw_token=raw_token)

        assert principal.principal_id == user_id

    def test_returns_none_for_unknown_token(self):
        from urdu_pipeline.auth.bearer import resolve_bearer_token

        store = _FakeBearerStore()

        assert resolve_bearer_token(store, raw_token="a" * 64) is None

    def test_returns_none_for_expired_token(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token

        store = _FakeBearerStore()
        raw_token, _ = create_bearer_token(
            store,
            user_id=UserId.new(),
            name="tok",
            expires_in=timedelta(seconds=-1),  # already past expiry
        )

        assert resolve_bearer_token(store, raw_token=raw_token) is None

    def test_returns_none_for_revoked_token(self):
        from urdu_pipeline.auth.bearer import (
            create_bearer_token,
            resolve_bearer_token,
            revoke_bearer_token,
        )

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="tok"
        )
        revoke_bearer_token(store, token_id=record.token_id)

        assert resolve_bearer_token(store, raw_token=raw_token) is None

    def test_resolve_updates_last_used_at(self):
        """Successful resolution must mutate last_used_at in the store."""
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token

        store = _FakeBearerStore()
        before = datetime.now(tz=timezone.utc)
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="tok"
        )

        assert record.last_used_at is None

        resolve_bearer_token(store, raw_token=raw_token)
        after = datetime.now(tz=timezone.utc)

        updated = store.get_bearer_token_by_hash(_sha256(raw_token))
        assert updated.last_used_at is not None
        assert before <= updated.last_used_at <= after

    def test_tokens_with_none_expiry_never_expire(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, resolve_bearer_token

        store = _FakeBearerStore()
        raw_token, _ = create_bearer_token(
            store, user_id=UserId.new(), name="tok", expires_in=None
        )

        assert resolve_bearer_token(store, raw_token=raw_token) is not None


# ── TestRevokeBearerToken ─────────────────────────────────────────────────────


class TestRevokeBearerToken:
    def test_stores_revoked_at_timestamp(self):
        from urdu_pipeline.auth.bearer import create_bearer_token, revoke_bearer_token

        store = _FakeBearerStore()
        before = datetime.now(tz=timezone.utc)
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="tok"
        )
        revoke_bearer_token(store, token_id=record.token_id)
        after = datetime.now(tz=timezone.utc)

        updated = store.get_bearer_token_by_hash(record.token_hash)
        assert updated.revoked_at is not None
        assert before <= updated.revoked_at <= after

    def test_revoked_token_resolves_to_none(self):
        from urdu_pipeline.auth.bearer import (
            create_bearer_token,
            resolve_bearer_token,
            revoke_bearer_token,
        )

        store = _FakeBearerStore()
        raw_token, record = create_bearer_token(
            store, user_id=UserId.new(), name="tok"
        )
        revoke_bearer_token(store, token_id=record.token_id)

        assert resolve_bearer_token(store, raw_token=raw_token) is None

    def test_revoke_requires_existing_token_id(self):
        """Revoking a non-existent token_id must raise KeyError."""
        from urdu_pipeline.auth.bearer import revoke_bearer_token
        from urdu_pipeline.domain.ids import TokenId

        store = _FakeBearerStore()

        with pytest.raises(KeyError):
            revoke_bearer_token(store, token_id=TokenId.new())
