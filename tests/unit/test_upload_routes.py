"""Route tests for upload lifecycle — Step 4.3.1.

Written BEFORE implementation (TDD).  All tests must fail until
``urdu_pipeline.api.routes.uploads`` exists and is wired into the app.

Covers:
  POST /uploads/init
  ─────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token header.
  - CSRF: bearer-auth bypasses CSRF check.
  - Success: 200, upload_id in response, signed URL in response.
  - Ownership: no user_id or internal object key in response.
  - Validation: disallowed extension → 422.
  - Validation: disallowed content_type → 422.
  - Validation: size_bytes == 0 → 422.
  - Validation: size_bytes > MAX_UPLOAD_BYTES → 422.
  - Validation: unknown field in body → 422.
  - upload_url_expires_at is in the future.
  - status is "initialized" after init.

  GET /uploads/{upload_id}
  ────────────────────────
  - Auth: 401 without credentials.
  - 200 for own upload.
  - 404 for unknown upload_id.
  - 404 for another user's upload (ownership check).
  - Response does not expose object key or user_id.
  - Response includes filename, content_type, size_bytes, status.

  POST /uploads/{upload_id}/complete
  ────────────────────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success.
  - 404 for unknown upload_id.
  - 404 for another user's upload.
  - Status changes to "completed" after completion.
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


# ── Fake hasher ───────────────────────────────────────────────────────────────


class _FakeHasher:
    def hash_secret(self, secret: str) -> str:
        return "HASHED:" + secret

    def verify_secret(self, secret: str, secret_hash: str) -> bool:
        return secret_hash == "HASHED:" + secret


# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_INIT_BODY = {
    "filename": "speech.mp3",
    "content_type": "audio/mpeg",
    "size_bytes": 1024 * 1024,  # 1 MB
}


def _make_app(*, second_user: bool = False) -> tuple[TestClient, InMemoryMetadataStore]:
    store = InMemoryMetadataStore()
    hasher = _FakeHasher()
    alice = UserRecord(
        user_id=UserId.new(),
        username="alice",
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret("s3cret"),
    )
    store.create_user(alice)
    if second_user:
        bob = UserRecord(
            user_id=UserId.new(),
            username="bob",
            status=UserStatus.ACTIVE,
            password_hash=hasher.hash_secret("b0bpass"),
        )
        store.create_user(bob)

    state = AppState(
        metadata_store=store,
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        password_hasher=hasher,
    )
    return TestClient(create_app(state=state), raise_server_exceptions=True), store


def _session_client(store_has_second_user: bool = False) -> tuple[TestClient, str, InMemoryMetadataStore]:
    """Return (client, csrf_token, store) after logging in as alice."""
    client, store = _make_app(second_user=store_has_second_user)
    resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    csrf = resp.cookies.get("csrf_token", "")
    return client, csrf, store


def _bearer_client() -> tuple[TestClient, str, InMemoryMetadataStore]:
    """Return (client, bearer_token, store) authenticated as alice via bearer."""
    client, csrf, store = _session_client()
    resp = client.post(
        "/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf}
    )
    raw_token = resp.json()["token"]
    # New client with no cookies, just bearer header
    fresh, _ = _make_app()
    # We need the same store, so rebuild fresh with the same state
    return client, raw_token, store


# ── TestInitUpload ────────────────────────────────────────────────────────────


class TestInitUpload:
    def test_requires_auth(self):
        client, _ = _make_app()

        resp = client.post("/uploads/init", json=_VALID_INIT_BODY)

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, _, _ = _session_client()

        resp = client.post("/uploads/init", json=_VALID_INIT_BODY)

        assert resp.status_code == 403

    def test_session_with_csrf_returns_200(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200

    def test_bearer_auth_requires_no_csrf(self):
        """Bearer-authenticated upload init must succeed without X-CSRF-Token."""
        client, csrf, store = _session_client()
        token_resp = client.post(
            "/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf}
        )
        raw_token = token_resp.json()["token"]

        # Make request using bearer only (session cookie present in client but
        # the CSRF check is skipped because our require_csrf looks at session cookie)
        # To truly test bearer-only, we need a client with no session cookie.
        # Build a separate app sharing the same store.
        from urdu_pipeline.auth.bearer import create_bearer_token
        from urdu_pipeline.infrastructure.in_memory import InMemoryMetadataStore as IMS

        user = store.get_user_by_username("alice")
        raw_token2, _ = create_bearer_token(store, user_id=user.user_id, name="test")

        hasher = _FakeHasher()
        state = AppState(
            metadata_store=store,
            object_store=InMemoryObjectStore(),
            cache_store=InMemoryCacheStore(),
            secret_provider=InMemorySecretProvider(),
            password_hasher=hasher,
        )
        bearer_client = TestClient(create_app(state=state), raise_server_exceptions=True)

        resp = bearer_client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"Authorization": f"Bearer {raw_token2}"},
        )

        assert resp.status_code == 200

    def test_returns_upload_id(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        body = resp.json()
        assert "upload_id" in body
        assert body["upload_id"].startswith("upl_")

    def test_returns_signed_upload_url(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert "upload_url" in resp.json()
        assert resp.json()["upload_url"] != ""

    def test_upload_url_does_not_expose_object_key(self):
        """The signed URL must not leak the internal object key."""
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        body = resp.json()

        assert "object_key" not in body
        assert "key" not in body

    def test_response_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert "user_id" not in resp.json()

    def test_status_is_initialized(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["status"] == "initialized"

    def test_upload_url_expires_at_is_in_future(self):
        from datetime import datetime, timezone

        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        body = resp.json()

        expires_at_str = body.get("upload_url_expires_at")
        assert expires_at_str is not None
        expires_at = datetime.fromisoformat(expires_at_str)
        now = datetime.now(tz=timezone.utc)
        assert expires_at > now

    def test_rejects_disallowed_extension(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json={"filename": "payload.exe", "content_type": "application/octet-stream", "size_bytes": 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_empty_filename(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json={"filename": "", "content_type": "audio/mpeg", "size_bytes": 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_disallowed_content_type(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json={"filename": "speech.mp3", "content_type": "text/html", "size_bytes": 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_zero_size(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json={"filename": "speech.mp3", "content_type": "audio/mpeg", "size_bytes": 0},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_size_over_max(self):
        client, csrf, _ = _session_client()
        # 600 MB > max allowed
        resp = client.post(
            "/uploads/init",
            json={
                "filename": "speech.mp3",
                "content_type": "audio/mpeg",
                "size_bytes": 600 * 1024 * 1024,
            },
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_unknown_field_returns_422(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/init",
            json={**_VALID_INIT_BODY, "object_key": "evil/path"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422


# ── TestGetUpload ─────────────────────────────────────────────────────────────


class TestGetUpload:
    def _create_upload(self, client: TestClient, csrf: str) -> str:
        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        return resp.json()["upload_id"]

    def test_requires_auth(self):
        client, _ = _make_app()

        resp = client.get("/uploads/upl_" + "a" * 32)

        assert resp.status_code == 401

    def test_returns_200_for_own_upload(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert resp.status_code == 200

    def test_returns_404_for_unknown_upload_id(self):
        client, _, _ = _session_client()

        resp = client.get("/uploads/upl_" + "a" * 32)

        assert resp.status_code == 404

    def test_returns_404_for_other_users_upload(self):
        """An upload owned by bob must be invisible to alice."""
        # Create upload as alice
        alice_client, alice_csrf, store = _session_client(store_has_second_user=True)
        upload_id = self._create_upload(alice_client, alice_csrf)

        # Log in as bob with the same store
        hasher = _FakeHasher()
        state = AppState(
            metadata_store=store,
            object_store=InMemoryObjectStore(),
            cache_store=InMemoryCacheStore(),
            secret_provider=InMemorySecretProvider(),
            password_hasher=hasher,
        )
        bob_client = TestClient(create_app(state=state), raise_server_exceptions=True)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/uploads/{upload_id}")

        assert resp.status_code == 404

    def test_response_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert "user_id" not in resp.json()

    def test_response_does_not_expose_object_key(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")
        body_str = str(resp.json())

        assert "object_key" not in body_str

    def test_response_includes_filename(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert resp.json()["filename"] == "speech.mp3"

    def test_response_includes_content_type(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert resp.json()["content_type"] == "audio/mpeg"

    def test_response_includes_size_bytes(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert resp.json()["size_bytes"] == 1024 * 1024

    def test_response_includes_status(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.get(f"/uploads/{upload_id}")

        assert "status" in resp.json()


# ── TestCompleteUpload ────────────────────────────────────────────────────────


class TestCompleteUpload:
    def _create_upload(self, client: TestClient, csrf: str) -> str:
        resp = client.post(
            "/uploads/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )
        return resp.json()["upload_id"]

    def test_requires_auth(self):
        client, _ = _make_app()

        resp = client.post(
            "/uploads/upl_" + "a" * 32 + "/complete", json={}
        )

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.post(f"/uploads/{upload_id}/complete", json={})

        assert resp.status_code == 403

    def test_complete_returns_200(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.post(
            f"/uploads/{upload_id}/complete",
            json={},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200

    def test_complete_returns_404_for_unknown(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/upl_" + "a" * 32 + "/complete",
            json={},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 404

    def test_complete_sets_status_to_completed(self):
        client, csrf, _ = _session_client()
        upload_id = self._create_upload(client, csrf)

        resp = client.post(
            f"/uploads/{upload_id}/complete",
            json={},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["status"] == "completed"

    def test_complete_returns_404_for_other_users_upload(self):
        alice_client, alice_csrf, store = _session_client(store_has_second_user=True)
        upload_id = self._create_upload(alice_client, alice_csrf)

        hasher = _FakeHasher()
        state = AppState(
            metadata_store=store,
            object_store=InMemoryObjectStore(),
            cache_store=InMemoryCacheStore(),
            secret_provider=InMemorySecretProvider(),
            password_hasher=hasher,
        )
        bob_client = TestClient(create_app(state=state), raise_server_exceptions=True)
        bob_resp = bob_client.post(
            "/auth/login", json={"username": "bob", "password": "b0bpass"}
        )
        bob_csrf = bob_resp.cookies.get("csrf_token", "")

        resp = bob_client.post(
            f"/uploads/{upload_id}/complete",
            json={},
            headers={"X-CSRF-Token": bob_csrf},
        )

        assert resp.status_code == 404
