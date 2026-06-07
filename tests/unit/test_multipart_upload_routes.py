"""Route tests for multipart and direct upload — Step 4.3.2.

Written BEFORE implementation (TDD).  All tests must fail until the routes
exist in ``urdu_pipeline.api.routes.uploads``.

Covers:
  POST /uploads/multipart/init
  ────────────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - CSRF: bearer auth bypasses CSRF.
  - 200 on success.
  - Returns upload_id, part_url, part_url_expires_at, status="uploading".
  - No object key or user_id in response.
  - Validation: disallowed extension, content_type, size 0, size > 500 MB, unknown field.
  - Total-size constraint: same 500 MB ceiling as single upload.

  GET /uploads/multipart/{upload_id}/parts/{part_number}
  ──────────────────────────────────────────────────────
  - Auth: 401 without credentials.
  - 200 returns signed URL.
  - 404 for unknown upload_id.
  - 404 for another user's upload (ownership check).
  - part_number must be >= 1 (returns 400 for 0 or negative).
  - Part-size constraint: part_number must be <= 10000.
  - Response does not expose object key.

  POST /uploads/multipart/{upload_id}/complete
  ─────────────────────────────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success with parts.
  - status changes to "completed".
  - 404 for unknown upload_id.
  - 404 for another user's upload.
  - Requires non-empty parts list.
  - Empty parts list returns 422.
  - Omitting parts field returns 422.

  DELETE /uploads/multipart/{upload_id}  (abort)
  ───────────────────────────────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success.
  - status changes to "cancelled".
  - 404 for unknown upload_id.
  - 404 for another user's upload.

  POST /uploads/direct
  ────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success.
  - status is "completed" immediately.
  - upload_id is returned.
  - Response does not expose object key or user_id.
  - Rejects disallowed file extension.
  - Rejects disallowed content_type.
  - Rejects empty file (size 0) → 422.
  - Rejects file over max direct upload size (50 MB) → 413 or 422.
  - Policy parity with /uploads/init: same extension and content_type allowlists.
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
    "size_bytes": 10 * 1024 * 1024,  # 10 MB
}

_FAKE_PARTS = [{"part_number": 1, "etag": "abc123etag"}]


def _build_app(store: InMemoryMetadataStore) -> TestClient:
    hasher = _FakeHasher()
    state = AppState(
        metadata_store=store,
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        password_hasher=hasher,
    )
    return TestClient(create_app(state=state), raise_server_exceptions=True)


def _make_store(*, add_bob: bool = False) -> InMemoryMetadataStore:
    hasher = _FakeHasher()
    store = InMemoryMetadataStore()
    alice = UserRecord(
        user_id=UserId.new(),
        username="alice",
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret("s3cret"),
    )
    store.create_user(alice)
    if add_bob:
        bob = UserRecord(
            user_id=UserId.new(),
            username="bob",
            status=UserStatus.ACTIVE,
            password_hash=hasher.hash_secret("b0bpass"),
        )
        store.create_user(bob)
    return store


def _session_client(*, add_bob: bool = False) -> tuple[TestClient, str, InMemoryMetadataStore]:
    """Return (client logged in as alice, csrf_token, store)."""
    store = _make_store(add_bob=add_bob)
    client = _build_app(store)
    resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    csrf = resp.cookies.get("csrf_token", "")
    return client, csrf, store


def _init_multipart(client: TestClient, csrf: str, body: dict | None = None) -> str:
    """Create a multipart upload and return its upload_id."""
    resp = client.post(
        "/uploads/multipart/init",
        json=body or _VALID_INIT_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["upload_id"]


# ── TestMultipartInit ─────────────────────────────────────────────────────────


class TestMultipartInit:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.post("/uploads/multipart/init", json=_VALID_INIT_BODY)

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, _, _ = _session_client()

        resp = client.post("/uploads/multipart/init", json=_VALID_INIT_BODY)

        assert resp.status_code == 403

    def test_session_with_csrf_returns_200(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200

    def test_bearer_auth_bypasses_csrf(self):
        """A bearer-token-only client must succeed without X-CSRF-Token."""
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _make_store()
        client = _build_app(store)
        # Log in to get a bearer token
        hasher = _FakeHasher()
        alice = store.get_user_by_username("alice")
        raw_token, _ = create_bearer_token(store, user_id=alice.user_id, name="ci")

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"Authorization": f"Bearer {raw_token}"},
        )

        assert resp.status_code == 200

    def test_returns_upload_id(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["upload_id"].startswith("upl_")

    def test_returns_part_url(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert "part_url" in resp.json()
        assert resp.json()["part_url"] != ""

    def test_status_is_uploading(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["status"] == "uploading"

    def test_does_not_expose_object_key(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert "object_key" not in resp.json()
        assert "key" not in resp.json()

    def test_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json=_VALID_INIT_BODY,
            headers={"X-CSRF-Token": csrf},
        )

        assert "user_id" not in resp.json()

    def test_rejects_disallowed_extension(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json={"filename": "evil.exe", "content_type": "application/octet-stream", "size_bytes": 10 * 1024 * 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_disallowed_content_type(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json={"filename": "speech.mp3", "content_type": "text/plain", "size_bytes": 10 * 1024 * 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_zero_size(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json={"filename": "speech.mp3", "content_type": "audio/mpeg", "size_bytes": 0},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_rejects_size_over_500mb(self):
        """Total-size constraint: same 500 MB ceiling as single upload."""
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json={"filename": "speech.mp3", "content_type": "audio/mpeg", "size_bytes": 600 * 1024 * 1024},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_unknown_field_returns_422(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/init",
            json={**_VALID_INIT_BODY, "object_key": "evil"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422


# ── TestGetPartUrl ────────────────────────────────────────────────────────────


class TestGetPartUrl:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.get("/uploads/multipart/upl_" + "a" * 32 + "/parts/1")

        assert resp.status_code == 401

    def test_returns_200_and_signed_url(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/1")

        assert resp.status_code == 200
        assert "part_url" in resp.json()
        assert resp.json()["part_url"] != ""

    def test_returns_correct_part_number(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/3")

        assert resp.json()["part_number"] == 3

    def test_returns_404_for_unknown_upload_id(self):
        client, _, _ = _session_client()

        resp = client.get("/uploads/multipart/upl_" + "a" * 32 + "/parts/1")

        assert resp.status_code == 404

    def test_returns_404_for_other_users_upload(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _init_multipart(alice_client, alice_csrf)

        bob_client = _build_app(store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/uploads/multipart/{upload_id}/parts/1")

        assert resp.status_code == 404

    def test_rejects_part_number_zero(self):
        """Part numbers must be >= 1."""
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/0")

        assert resp.status_code in (400, 422)

    def test_rejects_negative_part_number(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/-1")

        assert resp.status_code in (400, 422)

    def test_rejects_part_number_over_max(self):
        """Part numbers must be <= 10000."""
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/10001")

        assert resp.status_code in (400, 422)

    def test_response_does_not_expose_object_key(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.get(f"/uploads/multipart/{upload_id}/parts/1")

        assert "object_key" not in resp.json()


# ── TestCompleteMultipart ─────────────────────────────────────────────────────


class TestCompleteMultipart:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.post(
            "/uploads/multipart/upl_" + "a" * 32 + "/complete",
            json={"parts": _FAKE_PARTS},
        )

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": _FAKE_PARTS},
        )

        assert resp.status_code == 403

    def test_returns_200_on_success(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": _FAKE_PARTS},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200

    def test_sets_status_to_completed(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": _FAKE_PARTS},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["status"] == "completed"

    def test_returns_404_for_unknown(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/uploads/multipart/upl_" + "a" * 32 + "/complete",
            json={"parts": _FAKE_PARTS},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 404

    def test_returns_404_for_other_users_upload(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _init_multipart(alice_client, alice_csrf)

        bob_client = _build_app(store)
        bob_resp = bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})
        bob_csrf = bob_resp.cookies.get("csrf_token", "")

        resp = bob_client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": _FAKE_PARTS},
            headers={"X-CSRF-Token": bob_csrf},
        )

        assert resp.status_code == 404

    def test_empty_parts_list_returns_422(self):
        """Completion validation: parts must be non-empty."""
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": []},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_missing_parts_field_returns_422(self):
        """Completion validation: parts field is required."""
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_multiple_parts_succeeds(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.post(
            f"/uploads/multipart/{upload_id}/complete",
            json={"parts": [
                {"part_number": 1, "etag": "etag1"},
                {"part_number": 2, "etag": "etag2"},
                {"part_number": 3, "etag": "etag3"},
            ]},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


# ── TestAbortMultipart ────────────────────────────────────────────────────────


class TestAbortMultipart:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.delete("/uploads/multipart/upl_" + "a" * 32)

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.delete(f"/uploads/multipart/{upload_id}")

        assert resp.status_code == 403

    def test_returns_200_on_success(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.delete(
            f"/uploads/multipart/{upload_id}",
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200

    def test_sets_status_to_cancelled(self):
        client, csrf, _ = _session_client()
        upload_id = _init_multipart(client, csrf)

        resp = client.delete(
            f"/uploads/multipart/{upload_id}",
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.json()["status"] == "cancelled"

    def test_returns_404_for_unknown(self):
        client, csrf, _ = _session_client()

        resp = client.delete(
            "/uploads/multipart/upl_" + "a" * 32,
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 404

    def test_returns_404_for_other_users_upload(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _init_multipart(alice_client, alice_csrf)

        bob_client = _build_app(store)
        bob_resp = bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})
        bob_csrf = bob_resp.cookies.get("csrf_token", "")

        resp = bob_client.delete(
            f"/uploads/multipart/{upload_id}",
            headers={"X-CSRF-Token": bob_csrf},
        )

        assert resp.status_code == 404


# ── TestDirectUpload ──────────────────────────────────────────────────────────


class TestDirectUpload:
    def _post_direct(
        self,
        client: TestClient,
        *,
        csrf: str | None = None,
        filename: str = "speech.mp3",
        content_type: str = "audio/mpeg",
        content: bytes = b"fake audio bytes",
    ):
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        return client.post(
            "/uploads/direct",
            files={"file": (filename, content, content_type)},
            headers=headers,
        )

    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = self._post_direct(client)

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, _, _ = _session_client()

        resp = self._post_direct(client)

        assert resp.status_code == 403

    def test_returns_200_on_success(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf)

        assert resp.status_code == 200

    def test_status_is_completed_immediately(self):
        """Direct upload completes in one step — no separate /complete call needed."""
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf)

        assert resp.json()["status"] == "completed"

    def test_returns_upload_id(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf)

        assert resp.json()["upload_id"].startswith("upl_")

    def test_does_not_expose_object_key(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf)

        assert "object_key" not in resp.json()
        assert "key" not in resp.json()

    def test_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf)

        assert "user_id" not in resp.json()

    def test_rejects_disallowed_extension(self):
        """Policy parity with /uploads/init: same extension allowlist."""
        client, csrf, _ = _session_client()

        resp = self._post_direct(
            client, csrf=csrf, filename="malware.exe", content_type="application/octet-stream"
        )

        assert resp.status_code == 422

    def test_rejects_disallowed_content_type(self):
        """Policy parity with /uploads/init: same MIME-type allowlist."""
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf, content_type="text/html")

        assert resp.status_code == 422

    def test_rejects_empty_file(self):
        """size_bytes == 0 is not allowed (same policy as single-part init)."""
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf, content=b"")

        assert resp.status_code == 422

    def test_rejects_file_over_max_direct_size(self):
        """Direct uploads are capped at 50 MB since they go through the API server."""
        client, csrf, _ = _session_client()

        # Simulate a 51 MB file by sending a header-based size check
        # We use a bytes object here; in production a streaming check would apply
        big_content = b"x" * (51 * 1024 * 1024)

        resp = self._post_direct(client, csrf=csrf, content=big_content)

        assert resp.status_code in (413, 422)

    def test_response_includes_filename(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf, filename="talk.wav", content_type="audio/wav")

        assert resp.json()["filename"] == "talk.wav"

    def test_response_includes_content_type(self):
        client, csrf, _ = _session_client()

        resp = self._post_direct(client, csrf=csrf, content_type="audio/wav", filename="talk.wav")

        assert resp.json()["content_type"] == "audio/wav"
