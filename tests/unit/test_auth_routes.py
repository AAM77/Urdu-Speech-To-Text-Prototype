"""Route tests for login/logout — Step 4.2.2.

Tests:
  - Login success: 200, HTTP-only cookie with samesite attribute.
  - Login success: response body contains username (no user_id).
  - Login failure: wrong password → 401.
  - Login failure: unknown user → 401.
  - Login failure: disabled user → 401.
  - Logout: 200 and cookie cleared.
  - Logout: valid session is revoked in store.
  - Logout without cookie: graceful 200.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from urdu_pipeline.api.app import create_app
from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.application.ports.services import UserRecord
from urdu_pipeline.auth.sessions import _hash_token, create_session
from urdu_pipeline.domain import UserId, UserStatus
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryCacheStore,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemorySecretProvider,
)


# ── fake hasher for tests (no bcrypt cost) ────────────────────────────────────


class _FakeHasher:
    def hash_secret(self, secret: str) -> str:
        return "HASHED:" + secret

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        return secret_hash == "HASHED:" + secret


# ── helpers ───────────────────────────────────────────────────────────────────


def _app_with_user(
    *,
    username: str = "alice",
    password: str = "s3cret",
    status: UserStatus = UserStatus.ACTIVE,
) -> tuple[TestClient, InMemoryMetadataStore]:
    """Return (TestClient, store) with a pre-created user."""
    store = InMemoryMetadataStore()
    hasher = _FakeHasher()
    user = UserRecord(
        user_id=UserId.new(),
        username=username,
        status=status,
        password_hash=hasher.hash_secret(password),
    )
    store.create_user(user)

    state = AppState(
        metadata_store=store,
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        password_hasher=hasher,
    )
    client = TestClient(create_app(state=state), raise_server_exceptions=True)
    return client, store


# ── TestLoginRoute ────────────────────────────────────────────────────────────


class TestLoginRoute:
    def test_login_success_returns_200(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        assert response.status_code == 200

    def test_login_success_returns_username_in_body(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        assert response.json()["username"] == "alice"

    def test_login_response_does_not_expose_user_id(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        assert "user_id" not in response.json()

    def test_login_success_sets_session_cookie(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        assert "session" in response.cookies

    def test_login_cookie_is_http_only(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        set_cookie = response.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()

    def test_login_cookie_has_samesite_attribute(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        set_cookie = response.headers.get("set-cookie", "")
        assert "samesite" in set_cookie.lower()

    def test_login_wrong_password_returns_401(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "wrong"}
        )

        assert response.status_code == 401

    def test_login_unknown_user_returns_401(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login", json={"username": "nobody", "password": "s3cret"}
        )

        assert response.status_code == 401

    def test_login_disabled_user_returns_401(self):
        client, _ = _app_with_user(status=UserStatus.DISABLED)

        response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )

        assert response.status_code == 401

    def test_login_unknown_field_returns_422(self):
        client, _ = _app_with_user()

        response = client.post(
            "/auth/login",
            json={"username": "alice", "password": "s3cret", "injected": "evil"},
        )

        assert response.status_code == 422


# ── TestLogoutRoute ───────────────────────────────────────────────────────────


class TestLogoutRoute:
    def test_logout_returns_200(self):
        client, _ = _app_with_user()

        response = client.post("/auth/logout")

        assert response.status_code == 200

    def test_logout_without_session_cookie_is_graceful(self):
        client, _ = _app_with_user()

        response = client.post("/auth/logout")

        assert response.status_code == 200

    def test_logout_clears_session_cookie(self):
        client, store = _app_with_user()

        # Login first to get a cookie
        client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        response = client.post("/auth/logout")

        set_cookie = response.headers.get("set-cookie", "")
        # Cookie should be cleared (max-age=0 or expires in the past)
        assert "session" in set_cookie.lower()
        assert "max-age=0" in set_cookie.lower() or 'max-age=0' in set_cookie

    def test_logout_revokes_session_in_store(self):
        client, store = _app_with_user()

        # Login to create a session
        login_response = client.post(
            "/auth/login", json={"username": "alice", "password": "s3cret"}
        )
        raw_token = login_response.cookies["session"]

        # Logout
        client.post("/auth/logout")

        # Session should be revoked
        record = store.get_session_by_token_hash(_hash_token(raw_token))
        assert record is not None
        assert record.revoked_at is not None
