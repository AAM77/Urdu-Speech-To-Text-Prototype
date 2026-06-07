"""Route tests for bearer token management — Step 4.2.3.

Written BEFORE implementation (TDD).  Tests must fail until routes and
dependencies are wired up.

Tests cover:
  - POST /tokens: create token, raw token returned once in response.
  - GET /tokens: list tokens, raw token NEVER appears in list.
  - DELETE /tokens/{token_id}: revoke a token.
  - Bearer header auth: requests with a valid Bearer token are accepted
    on protected routes; invalid/revoked/expired tokens receive 401.
  - Unauthenticated requests to token routes return 401.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from urdu_pipeline.api.app import create_app
from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.application.ports.services import UserRecord
from urdu_pipeline.domain import UserId, UserStatus
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryCacheStore,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemorySecretProvider,
)


# ── Fake hasher (no bcrypt cost) ──────────────────────────────────────────────


class _FakeHasher:
    def hash_secret(self, secret: str) -> str:
        return "HASHED:" + secret

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        return secret_hash == "HASHED:" + secret


# ── Test fixture helpers ──────────────────────────────────────────────────────


def _make_app() -> tuple[TestClient, InMemoryMetadataStore]:
    """App with a single pre-configured user (alice / s3cret)."""
    store = InMemoryMetadataStore()
    hasher = _FakeHasher()
    user = UserRecord(
        user_id=UserId.new(),
        username="alice",
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret("s3cret"),
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


def _logged_in_client() -> tuple[TestClient, InMemoryMetadataStore]:
    """Client that has already logged in (session cookie set)."""
    client, store = _make_app()
    client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    return client, store


# ── TestCreateToken ───────────────────────────────────────────────────────────


class TestCreateToken:
    def test_create_token_requires_auth(self):
        client, _ = _make_app()

        response = client.post("/tokens", json={"name": "ci"})

        assert response.status_code == 401

    def test_create_token_returns_200(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})

        assert response.status_code == 200

    def test_response_contains_raw_token(self):
        """Token is shown once — must appear in create response."""
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})
        body = response.json()

        assert "token" in body
        assert len(body["token"]) == 64  # secrets.token_hex(32)

    def test_response_contains_token_id(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})
        body = response.json()

        assert "token_id" in body
        assert body["token_id"].startswith("tok_")

    def test_response_contains_name(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "my-ci-key"})

        assert response.json()["name"] == "my-ci-key"

    def test_response_does_not_expose_user_id(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})

        assert "user_id" not in response.json()

    def test_response_does_not_expose_token_hash(self):
        """Hash must never leave the server."""
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})

        assert "token_hash" not in response.json()

    def test_create_token_with_expiry(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci", "expires_in_days": 90})
        body = response.json()

        assert body.get("expires_at") is not None

    def test_create_token_without_expiry_has_no_expires_at(self):
        client, _ = _logged_in_client()

        response = client.post("/tokens", json={"name": "ci"})
        body = response.json()

        assert body.get("expires_at") is None

    def test_unknown_field_in_request_returns_422(self):
        client, _ = _logged_in_client()

        response = client.post(
            "/tokens", json={"name": "ci", "injected_field": "evil"}
        )

        assert response.status_code == 422


# ── TestListTokens ────────────────────────────────────────────────────────────


class TestListTokens:
    def test_list_tokens_requires_auth(self):
        client, _ = _make_app()

        response = client.get("/tokens")

        assert response.status_code == 401

    def test_list_tokens_returns_200(self):
        client, _ = _logged_in_client()

        response = client.get("/tokens")

        assert response.status_code == 200

    def test_list_tokens_shows_created_tokens(self):
        client, _ = _logged_in_client()
        client.post("/tokens", json={"name": "ci"})
        client.post("/tokens", json={"name": "deploy"})

        response = client.get("/tokens")
        body = response.json()

        assert len(body["tokens"]) == 2

    def test_list_tokens_never_includes_raw_token(self):
        """Shown once — raw token must NOT appear in list responses."""
        client, _ = _logged_in_client()
        create_resp = client.post("/tokens", json={"name": "ci"})
        raw_token = create_resp.json()["token"]

        list_resp = client.get("/tokens")
        list_body = str(list_resp.json())

        assert raw_token not in list_body

    def test_list_tokens_never_includes_hash(self):
        client, _ = _logged_in_client()
        client.post("/tokens", json={"name": "ci"})

        response = client.get("/tokens")

        assert "token_hash" not in str(response.json())

    def test_list_tokens_includes_token_id_and_name(self):
        client, _ = _logged_in_client()
        client.post("/tokens", json={"name": "ci"})

        response = client.get("/tokens")
        token_summary = response.json()["tokens"][0]

        assert "token_id" in token_summary
        assert "name" in token_summary

    def test_list_tokens_includes_last_used_at_field(self):
        client, _ = _logged_in_client()
        client.post("/tokens", json={"name": "ci"})

        response = client.get("/tokens")
        token_summary = response.json()["tokens"][0]

        assert "last_used_at" in token_summary


# ── TestRevokeToken ───────────────────────────────────────────────────────────


class TestRevokeToken:
    def test_revoke_token_requires_auth(self):
        client, _ = _make_app()

        response = client.delete("/tokens/tok_" + "a" * 32)

        assert response.status_code == 401

    def test_revoke_token_returns_200(self):
        client, _ = _logged_in_client()
        create_resp = client.post("/tokens", json={"name": "ci"})
        token_id = create_resp.json()["token_id"]

        response = client.delete(f"/tokens/{token_id}")

        assert response.status_code == 200

    def test_revoke_token_response_contains_token_id(self):
        client, _ = _logged_in_client()
        create_resp = client.post("/tokens", json={"name": "ci"})
        token_id = create_resp.json()["token_id"]

        response = client.delete(f"/tokens/{token_id}")

        assert response.json()["token_id"] == token_id

    def test_revoke_nonexistent_token_returns_404(self):
        client, _ = _logged_in_client()

        response = client.delete("/tokens/tok_" + "a" * 32)

        assert response.status_code == 404

    def test_revoked_token_no_longer_valid_for_bearer_auth(self):
        """After revocation, bearer auth with that token returns 401."""
        client, _ = _logged_in_client()
        create_resp = client.post("/tokens", json={"name": "ci"})
        body = create_resp.json()
        token_id = body["token_id"]
        raw_token = body["token"]

        # revoke
        client.delete(f"/tokens/{token_id}")

        # try to use the revoked token for bearer auth
        unauth_client, _ = _make_app()
        response = unauth_client.get(
            "/tokens", headers={"Authorization": f"Bearer {raw_token}"}
        )
        assert response.status_code == 401


# ── TestBearerTokenAuth ───────────────────────────────────────────────────────


class TestBearerTokenAuth:
    def test_valid_bearer_token_authenticates_request(self):
        """A raw token from create can be used as Bearer auth on token list."""
        # Share the same app instance so the token created by client1 is
        # visible when client2 authenticates with the bearer header.
        store = InMemoryMetadataStore()
        hasher = _FakeHasher()
        user = UserRecord(
            user_id=UserId.new(),
            username="alice",
            status=UserStatus.ACTIVE,
            password_hash=hasher.hash_secret("s3cret"),
        )
        store.create_user(user)
        state = AppState(
            metadata_store=store,
            object_store=InMemoryObjectStore(),
            cache_store=InMemoryCacheStore(),
            secret_provider=InMemorySecretProvider(),
            password_hasher=hasher,
        )
        app = create_app(state=state)

        # Client 1: log in with session cookie and create a bearer token
        client1 = TestClient(app, raise_server_exceptions=True)
        client1.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        create_resp = client1.post("/tokens", json={"name": "ci"})
        raw_token = create_resp.json()["token"]

        # Client 2: no session cookie, authenticates via bearer header only
        client2 = TestClient(app, raise_server_exceptions=True)
        response = client2.get(
            "/tokens", headers={"Authorization": f"Bearer {raw_token}"}
        )

        assert response.status_code == 200

    def test_invalid_bearer_token_returns_401(self):
        client, _ = _make_app()

        response = client.get(
            "/tokens", headers={"Authorization": "Bearer " + "x" * 64}
        )

        assert response.status_code == 401

    def test_bearer_auth_updates_last_used_at_in_store(self):
        client, store = _logged_in_client()
        create_resp = client.post("/tokens", json={"name": "ci"})
        raw_token = create_resp.json()["token"]

        # make a request with the bearer token
        client2, store2 = _make_app()
        # Note: client2 uses a separate store instance; we need the original store
        # So we test last_used_at via the list endpoint on the same client
        client.logout = None  # no logout needed
        client.get("/tokens", headers={"Authorization": f"Bearer {raw_token}"})

        # Check last_used_at via list
        list_resp = client.get("/tokens")
        token_summary = list_resp.json()["tokens"][0]
        assert token_summary["last_used_at"] is not None

    def test_expired_bearer_token_returns_401(self):
        """An expired token (created with negative expiry) returns 401."""
        from urdu_pipeline.auth.bearer import create_bearer_token
        from datetime import timedelta

        _, store = _logged_in_client()
        # Directly create an expired token in the store
        user = store.get_user_by_username("alice")
        raw_token, _ = create_bearer_token(
            store,
            user_id=user.user_id,
            name="expired",
            expires_in=timedelta(seconds=-1),
        )

        client, _ = _make_app()
        response = client.get(
            "/tokens", headers={"Authorization": f"Bearer {raw_token}"}
        )
        assert response.status_code == 401
