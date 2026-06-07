"""Unit tests for session create/resolve/revoke — Step 4.2.2.

Uses an in-memory metadata store so no database or HTTP layer is needed.
Covers:
  - Session creation stores hash, not raw token.
  - Resolution returns an AuthPrincipal for valid tokens.
  - Expired sessions return None.
  - Revoked sessions return None.
  - Each call produces a unique token.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from urdu_pipeline.application.ports.services import AuthPrincipal, SessionRecord
from urdu_pipeline.domain import SessionId, UserId
from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore


# ── helpers ───────────────────────────────────────────────────────────────────


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _store() -> InMemoryMetadataStore:
    return InMemoryMetadataStore()


# ── TestCreateSession ─────────────────────────────────────────────────────────


class TestCreateSession:
    def test_returns_raw_token_and_record(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        user_id = UserId.new()

        raw_token, record = create_session(store, user_id=user_id)

        assert isinstance(raw_token, str)
        assert len(raw_token) == 64  # secrets.token_hex(32) → 64 hex chars
        assert isinstance(record, SessionRecord)

    def test_stores_token_hash_not_raw_token_in_record(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        user_id = UserId.new()

        raw_token, record = create_session(store, user_id=user_id)

        assert record.token_hash != raw_token
        assert record.token_hash == _hash(raw_token)

    def test_record_is_retrievable_by_token_hash(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        user_id = UserId.new()

        raw_token, record = create_session(store, user_id=user_id)

        stored = store.get_session_by_token_hash(_hash(raw_token))
        assert stored == record

    def test_session_is_not_revoked_on_creation(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        _, record = create_session(store, user_id=UserId.new())

        assert record.revoked_at is None

    def test_session_expires_after_given_duration(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        _, record = create_session(store, user_id=UserId.new(), expires_in=timedelta(hours=1))

        expected_expiry = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        delta = abs((record.expires_at - expected_expiry).total_seconds())
        assert delta < 2.0  # within 2 seconds of expected

    def test_each_call_returns_different_token(self):
        from urdu_pipeline.auth.sessions import create_session

        store = _store()
        user_id = UserId.new()

        token_a, _ = create_session(store, user_id=user_id)
        token_b, _ = create_session(store, user_id=user_id)

        assert token_a != token_b


# ── TestResolveSession ────────────────────────────────────────────────────────


class TestResolveSession:
    def test_returns_auth_principal_for_valid_token(self):
        from urdu_pipeline.auth.sessions import create_session, resolve_session

        store = _store()
        user_id = UserId.new()
        raw_token, _ = create_session(store, user_id=user_id)

        principal = resolve_session(store, raw_token=raw_token)

        assert principal is not None
        assert isinstance(principal, AuthPrincipal)

    def test_principal_has_user_kind(self):
        from urdu_pipeline.auth.sessions import create_session, resolve_session

        store = _store()
        raw_token, _ = create_session(store, user_id=UserId.new())

        principal = resolve_session(store, raw_token=raw_token)

        assert principal.kind == "user"

    def test_principal_id_matches_user_id(self):
        from urdu_pipeline.auth.sessions import create_session, resolve_session

        store = _store()
        user_id = UserId.new()
        raw_token, _ = create_session(store, user_id=user_id)

        principal = resolve_session(store, raw_token=raw_token)

        assert principal.principal_id == user_id

    def test_returns_none_for_unknown_token(self):
        from urdu_pipeline.auth.sessions import resolve_session

        store = _store()

        assert resolve_session(store, raw_token="a" * 64) is None

    def test_returns_none_for_expired_session(self):
        from urdu_pipeline.auth.sessions import create_session, resolve_session

        store = _store()
        raw_token, _ = create_session(
            store,
            user_id=UserId.new(),
            expires_in=timedelta(seconds=-1),  # already expired
        )

        assert resolve_session(store, raw_token=raw_token) is None

    def test_returns_none_for_revoked_session(self):
        from urdu_pipeline.auth.sessions import (
            create_session,
            resolve_session,
            revoke_session,
        )

        store = _store()
        raw_token, record = create_session(store, user_id=UserId.new())
        revoke_session(store, session_id=record.session_id)

        assert resolve_session(store, raw_token=raw_token) is None


# ── TestRevokeSession ─────────────────────────────────────────────────────────


class TestRevokeSession:
    def test_revoked_session_stores_revoked_at_timestamp(self):
        from urdu_pipeline.auth.sessions import create_session, revoke_session

        store = _store()
        before = datetime.now(tz=timezone.utc)
        _, record = create_session(store, user_id=UserId.new())
        revoke_session(store, session_id=record.session_id)
        after = datetime.now(tz=timezone.utc)

        stored = store.get_session_by_token_hash(record.token_hash)
        assert stored.revoked_at is not None
        assert before <= stored.revoked_at <= after

    def test_revoked_session_cannot_be_resolved(self):
        from urdu_pipeline.auth.sessions import (
            create_session,
            resolve_session,
            revoke_session,
        )

        store = _store()
        raw_token, record = create_session(store, user_id=UserId.new())
        revoke_session(store, session_id=record.session_id)

        assert resolve_session(store, raw_token=raw_token) is None
