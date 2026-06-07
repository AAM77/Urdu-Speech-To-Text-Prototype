"""Route tests for run lifecycle — Step 4.3.3.

Written BEFORE implementation (TDD).  All tests must fail until
``urdu_pipeline.api.routes.runs`` exists and is wired into the app.

Covers:
  POST /runs
  ──────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success (session + CSRF).
  - Bearer auth bypasses CSRF.
  - Returns run_id (opaque, starts with "run_").
  - Status is "pending" on creation.
  - upload_id echoed in response.
  - No user_id or job_id in response.
  - upload_id must exist for caller (404 for unknown).
  - upload_id must belong to caller (404 for other user's upload).
  - Upload must be COMPLETED (422 for non-completed upload).
  - Unknown field in body returns 422.
  - Job is enqueued to the job queue after creation.

  GET /runs
  ─────────
  - Auth: 401 without credentials.
  - Returns 200 with empty list when no runs.
  - Returns runs created by current user.
  - Does not return runs owned by other users.

  GET /runs/{run_id}
  ──────────────────
  - Auth: 401 without credentials.
  - 200 for own run.
  - 404 for unknown run_id.
  - 404 for another user's run.
  - No user_id in response.
  - No job_id in response.

  GET /runs/{run_id}/events
  ─────────────────────────
  - Auth: 401 without credentials.
  - 200 with events list (may be empty for now).
  - 404 for unknown run_id.
  - 404 for another user's run.

  POST /runs/{run_id}/cancel
  ──────────────────────────
  - Auth: 401 without credentials.
  - CSRF: 403 with session but no X-CSRF-Token.
  - 200 on success.
  - status changes to "cancelled".
  - 404 for unknown run_id.
  - 404 for another user's run.
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
    InMemoryJobQueue,
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

_VALID_UPLOAD_BODY = {
    "filename": "speech.mp3",
    "content_type": "audio/mpeg",
    "size_bytes": 1024 * 1024,
}


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


def _build_app(store: InMemoryMetadataStore, job_queue: InMemoryJobQueue | None = None) -> TestClient:
    hasher = _FakeHasher()
    state = AppState(
        metadata_store=store,
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        password_hasher=hasher,
        job_queue=job_queue,
    )
    return TestClient(create_app(state=state), raise_server_exceptions=True)


def _session_client(*, add_bob: bool = False, job_queue: InMemoryJobQueue | None = None) -> tuple[TestClient, str, InMemoryMetadataStore]:
    """Return (alice's client, csrf_token, store)."""
    store = _make_store(add_bob=add_bob)
    client = _build_app(store, job_queue=job_queue)
    resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    csrf = resp.cookies.get("csrf_token", "")
    return client, csrf, store


def _create_completed_upload(client: TestClient, csrf: str) -> str:
    """Init and complete an upload, return upload_id."""
    resp = client.post("/uploads/init", json=_VALID_UPLOAD_BODY, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.json()
    upload_id = resp.json()["upload_id"]
    resp2 = client.post(f"/uploads/{upload_id}/complete", json={}, headers={"X-CSRF-Token": csrf})
    assert resp2.status_code == 200, resp2.json()
    return upload_id


def _create_run(client: TestClient, csrf: str, upload_id: str) -> str:
    """Create a run for a completed upload, return run_id."""
    resp = client.post(
        "/runs",
        json={"upload_id": upload_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["run_id"]


# ── TestCreateRun ─────────────────────────────────────────────────────────────


class TestCreateRun:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.post("/runs", json={"upload_id": "upl_" + "a" * 32})

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id})

        assert resp.status_code == 403

    def test_session_with_csrf_returns_200(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert resp.status_code == 200

    def test_bearer_auth_bypasses_csrf(self):
        from urdu_pipeline.auth.bearer import create_bearer_token

        store = _make_store()
        # Use a session client to create a completed upload
        session_client = _build_app(store)
        login_resp = session_client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        csrf = login_resp.cookies.get("csrf_token", "")
        upload_id = _create_completed_upload(session_client, csrf)

        alice = store.get_user_by_username("alice")
        raw_token, _ = create_bearer_token(store, user_id=alice.user_id, name="ci")

        # Fresh client: no session cookies, only bearer header
        bearer_client = _build_app(store)
        resp = bearer_client.post(
            "/runs",
            json={"upload_id": upload_id},
            headers={"Authorization": f"Bearer {raw_token}"},
        )

        assert resp.status_code == 200

    def test_returns_run_id(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert resp.json()["run_id"].startswith("run_")

    def test_status_is_pending(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert resp.json()["status"] == "pending"

    def test_upload_id_in_response(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert resp.json()["upload_id"] == upload_id

    def test_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert "user_id" not in resp.json()

    def test_does_not_expose_job_id(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert "job_id" not in resp.json()

    def test_unknown_upload_returns_404(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/runs",
            json={"upload_id": "upl_" + "a" * 32},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 404

    def test_other_users_upload_returns_404(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _create_completed_upload(alice_client, alice_csrf)

        bob_client = _build_app(store)
        bob_resp = bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})
        bob_csrf = bob_resp.cookies.get("csrf_token", "")

        resp = bob_client.post(
            "/runs",
            json={"upload_id": upload_id},
            headers={"X-CSRF-Token": bob_csrf},
        )

        assert resp.status_code == 404

    def test_non_completed_upload_returns_422(self):
        """Cannot create a run for an upload that hasn't been completed."""
        client, csrf, _ = _session_client()
        # Create upload but do NOT complete it
        resp = client.post("/uploads/init", json=_VALID_UPLOAD_BODY, headers={"X-CSRF-Token": csrf})
        upload_id = resp.json()["upload_id"]

        resp2 = client.post(
            "/runs",
            json={"upload_id": upload_id},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp2.status_code == 422

    def test_unknown_field_returns_422(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post(
            "/runs",
            json={"upload_id": upload_id, "chunk_size_seconds": 30},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 422

    def test_job_is_enqueued(self):
        """After run creation, a job must be present in the job queue."""
        job_queue = InMemoryJobQueue()
        client, csrf, _ = _session_client(job_queue=job_queue)
        upload_id = _create_completed_upload(client, csrf)

        client.post("/runs", json={"upload_id": upload_id}, headers={"X-CSRF-Token": csrf})

        assert len(job_queue._queued) == 1

    def test_description_is_optional(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)

        resp = client.post(
            "/runs",
            json={"upload_id": upload_id, "description": "My run"},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200
        assert resp.json()["description"] == "My run"


# ── TestListRuns ──────────────────────────────────────────────────────────────


class TestListRuns:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.get("/runs")

        assert resp.status_code == 401

    def test_returns_empty_list_when_no_runs(self):
        client, _, _ = _session_client()

        resp = client.get("/runs")

        assert resp.status_code == 200
        assert resp.json()["runs"] == []

    def test_returns_created_runs(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        _create_run(client, csrf, upload_id)

        resp = client.get("/runs")

        assert len(resp.json()["runs"]) == 1

    def test_does_not_return_other_users_runs(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _create_completed_upload(alice_client, alice_csrf)
        _create_run(alice_client, alice_csrf, upload_id)

        bob_client = _build_app(store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get("/runs")

        assert resp.json()["runs"] == []


# ── TestGetRun ────────────────────────────────────────────────────────────────


class TestGetRun:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.get("/runs/run_" + "a" * 32)

        assert resp.status_code == 401

    def test_returns_200_for_own_run(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.get(f"/runs/{run_id}")

        assert resp.status_code == 200

    def test_returns_404_for_unknown(self):
        client, _, _ = _session_client()

        resp = client.get("/runs/run_" + "a" * 32)

        assert resp.status_code == 404

    def test_returns_404_for_other_users_run(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _create_completed_upload(alice_client, alice_csrf)
        run_id = _create_run(alice_client, alice_csrf, upload_id)

        bob_client = _build_app(store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/runs/{run_id}")

        assert resp.status_code == 404

    def test_does_not_expose_user_id(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.get(f"/runs/{run_id}")

        assert "user_id" not in resp.json()

    def test_does_not_expose_job_id(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.get(f"/runs/{run_id}")

        assert "job_id" not in resp.json()


# ── TestGetRunEvents ──────────────────────────────────────────────────────────


class TestGetRunEvents:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.get("/runs/run_" + "a" * 32 + "/events")

        assert resp.status_code == 401

    def test_returns_200_with_events_list(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.get(f"/runs/{run_id}/events")

        assert resp.status_code == 200
        assert "events" in resp.json()
        assert isinstance(resp.json()["events"], list)

    def test_returns_404_for_unknown_run(self):
        client, _, _ = _session_client()

        resp = client.get("/runs/run_" + "a" * 32 + "/events")

        assert resp.status_code == 404

    def test_returns_404_for_other_users_run(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _create_completed_upload(alice_client, alice_csrf)
        run_id = _create_run(alice_client, alice_csrf, upload_id)

        bob_client = _build_app(store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/runs/{run_id}/events")

        assert resp.status_code == 404


# ── TestCancelRun ─────────────────────────────────────────────────────────────


class TestCancelRun:
    def test_requires_auth(self):
        store = _make_store()
        client = _build_app(store)

        resp = client.post("/runs/run_" + "a" * 32 + "/cancel")

        assert resp.status_code == 401

    def test_session_without_csrf_returns_403(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.post(f"/runs/{run_id}/cancel")

        assert resp.status_code == 403

    def test_cancel_returns_200(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.post(f"/runs/{run_id}/cancel", headers={"X-CSRF-Token": csrf})

        assert resp.status_code == 200

    def test_cancel_sets_status_to_cancelled(self):
        client, csrf, _ = _session_client()
        upload_id = _create_completed_upload(client, csrf)
        run_id = _create_run(client, csrf, upload_id)

        resp = client.post(f"/runs/{run_id}/cancel", headers={"X-CSRF-Token": csrf})

        assert resp.json()["status"] == "cancelled"

    def test_cancel_returns_404_for_unknown(self):
        client, csrf, _ = _session_client()

        resp = client.post(
            "/runs/run_" + "a" * 32 + "/cancel",
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 404

    def test_cancel_returns_404_for_other_users_run(self):
        alice_client, alice_csrf, store = _session_client(add_bob=True)
        upload_id = _create_completed_upload(alice_client, alice_csrf)
        run_id = _create_run(alice_client, alice_csrf, upload_id)

        bob_client = _build_app(store)
        bob_resp = bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})
        bob_csrf = bob_resp.cookies.get("csrf_token", "")

        resp = bob_client.post(
            f"/runs/{run_id}/cancel",
            headers={"X-CSRF-Token": bob_csrf},
        )

        assert resp.status_code == 404
