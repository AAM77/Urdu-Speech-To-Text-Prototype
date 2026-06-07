"""Route tests for artifact list, read, and download — Step 4.3.4.

Written BEFORE implementation (TDD).  All tests must fail until
``urdu_pipeline.api.routes.artifacts`` exists and is wired into the app.

Artifact objects are created by the processor (Stage 5), not by the API.
Tests seed artifact records directly into the in-memory stores to simulate
what the processor would produce.

Covers:
  GET /runs/{run_id}/artifacts
  ─────────────────────────────
  - Auth: 401 without credentials.
  - 200 with empty list for a run that has no artifacts.
  - 200 with artifact summaries for a seeded run.
  - 404 for unknown run_id.
  - 404 for another user's run.
  - No raw object key in any artifact summary.

  GET /artifacts/{artifact_id}
  ────────────────────────────
  - Auth: 401 without credentials.
  - 200 with artifact metadata (stage, type, has_markdown).
  - 404 for unknown artifact_id.
  - 404 for another user's artifact.
  - No object key in response.

  GET /artifacts/{artifact_id}/download?format=json|markdown
  ───────────────────────────────────────────────────────────
  - Auth: 401 without credentials.
  - 200 returns download_url, expires_at, format, artifact_id.
  - format=json and format=markdown both work (when content exists).
  - 404 for unknown artifact_id.
  - 404 for another user's artifact.
  - 404 for markdown download when has_markdown=False.
  - No raw object key in response.
  - download_url is non-empty.
  - expires_at is in the future.
  - Invalid format value returns 422.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from urdu_pipeline.api.app import create_app
from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.application.ports.services import ArtifactRecord, RunRecord, UserRecord
from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, UserId, UserStatus
from urdu_pipeline.domain.ids import RunId
from urdu_pipeline.domain.states import RunStatus
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


def _build_app(
    store: InMemoryMetadataStore,
    object_store: InMemoryObjectStore | None = None,
) -> TestClient:
    state = AppState(
        metadata_store=store,
        object_store=object_store or InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        password_hasher=_FakeHasher(),
    )
    return TestClient(create_app(state=state), raise_server_exceptions=True)


def _make_alice(store: InMemoryMetadataStore) -> UserId:
    hasher = _FakeHasher()
    uid = UserId.new()
    store.create_user(UserRecord(
        user_id=uid,
        username="alice",
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret("s3cret"),
    ))
    return uid


def _make_bob(store: InMemoryMetadataStore) -> UserId:
    hasher = _FakeHasher()
    uid = UserId.new()
    store.create_user(UserRecord(
        user_id=uid,
        username="bob",
        status=UserStatus.ACTIVE,
        password_hash=hasher.hash_secret("b0bpass"),
    ))
    return uid


def _make_run(store: InMemoryMetadataStore, user_id: UserId) -> RunId:
    run_id = RunId.new()
    store.create_run(RunRecord(
        user_id=user_id,
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
    ))
    return run_id


def _seed_artifact(
    store: InMemoryMetadataStore,
    object_store: InMemoryObjectStore,
    user_id: UserId,
    run_id: RunId,
    *,
    has_markdown: bool = False,
) -> ArtifactId:
    """Seed a fake artifact in both the metadata store and object store."""
    artifact_id = ArtifactId.new()
    record = ArtifactRecord(
        user_id=user_id,
        run_id=run_id,
        artifact_id=artifact_id,
        stage=ArtifactStage.TRANSCRIBER,
        artifact_type=ArtifactType.RAW_URDU_TRANSCRIPT,
        has_markdown=has_markdown,
    )
    store.record_artifact(record)
    object_store.put_stream(
        f"artifacts/{artifact_id}.json",
        BytesIO(b'{"text":"urdu transcript"}'),
    )
    if has_markdown:
        object_store.put_stream(
            f"artifacts/{artifact_id}.md",
            BytesIO(b"# Urdu Transcript\n\nSome text."),
        )
    return artifact_id


def _login_alice(client: TestClient) -> str:
    """Log in as alice and return the csrf_token."""
    resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    return resp.cookies.get("csrf_token", "")


# ── TestListArtifacts ─────────────────────────────────────────────────────────


class TestListArtifacts:
    def _setup(self, *, has_markdown: bool = False) -> tuple[TestClient, str, InMemoryMetadataStore, InMemoryObjectStore, RunId, ArtifactId]:
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        client = _build_app(store, obj_store)
        csrf = _login_alice(client)
        run_id = _make_run(store, alice_id)
        artifact_id = _seed_artifact(store, obj_store, alice_id, run_id, has_markdown=has_markdown)
        return client, csrf, store, obj_store, run_id, artifact_id

    def test_requires_auth(self):
        store = InMemoryMetadataStore()
        alice_id = _make_alice(store)
        run_id = _make_run(store, alice_id)
        client = _build_app(store)

        resp = client.get(f"/runs/{run_id}/artifacts")

        assert resp.status_code == 401

    def test_returns_empty_list_for_no_artifacts(self):
        store = InMemoryMetadataStore()
        alice_id = _make_alice(store)
        run_id = _make_run(store, alice_id)
        client = _build_app(store)
        _login_alice(client)

        resp = client.get(f"/runs/{run_id}/artifacts")

        assert resp.status_code == 200
        assert resp.json()["artifacts"] == []

    def test_returns_artifact_summaries(self):
        client, _, _, _, run_id, artifact_id = self._setup()

        resp = client.get(f"/runs/{run_id}/artifacts")

        assert resp.status_code == 200
        assert len(resp.json()["artifacts"]) == 1
        assert resp.json()["artifacts"][0]["artifact_id"] == str(artifact_id)

    def test_returns_404_for_unknown_run(self):
        store = InMemoryMetadataStore()
        _make_alice(store)
        client = _build_app(store)
        _login_alice(client)

        resp = client.get("/runs/run_" + "a" * 32 + "/artifacts")

        assert resp.status_code == 404

    def test_returns_404_for_other_users_run(self):
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        bob_id = _make_bob(store)
        run_id = _make_run(store, alice_id)

        # Bob tries to list alice's run's artifacts
        bob_client = _build_app(store, obj_store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/runs/{run_id}/artifacts")

        assert resp.status_code == 404

    def test_artifact_summary_does_not_expose_object_key(self):
        client, _, _, _, run_id, _ = self._setup()

        resp = client.get(f"/runs/{run_id}/artifacts")

        body_str = str(resp.json())
        assert "object_key" not in body_str
        assert ".json" not in body_str
        assert ".md" not in body_str

    def test_artifact_summary_does_not_expose_user_id(self):
        client, _, _, _, run_id, _ = self._setup()

        resp = client.get(f"/runs/{run_id}/artifacts")

        assert "user_id" not in str(resp.json())

    def test_artifact_summary_includes_expected_fields(self):
        client, _, _, _, run_id, artifact_id = self._setup()

        resp = client.get(f"/runs/{run_id}/artifacts")

        summary = resp.json()["artifacts"][0]
        assert "artifact_id" in summary
        assert "run_id" in summary
        assert "stage" in summary
        assert "artifact_type" in summary
        assert "has_markdown" in summary

    def test_multiple_artifacts_returned(self):
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        client = _build_app(store, obj_store)
        _login_alice(client)
        run_id = _make_run(store, alice_id)
        _seed_artifact(store, obj_store, alice_id, run_id)
        _seed_artifact(store, obj_store, alice_id, run_id)

        resp = client.get(f"/runs/{run_id}/artifacts")

        assert len(resp.json()["artifacts"]) == 2


# ── TestGetArtifact ───────────────────────────────────────────────────────────


class TestGetArtifact:
    def _setup(self) -> tuple[TestClient, str, ArtifactId]:
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        run_id = _make_run(store, alice_id)
        artifact_id = _seed_artifact(store, obj_store, alice_id, run_id)
        client = _build_app(store, obj_store)
        _login_alice(client)
        return client, str(artifact_id), artifact_id

    def test_requires_auth(self):
        store = InMemoryMetadataStore()
        client = _build_app(store)

        resp = client.get("/artifacts/art_" + "a" * 32)

        assert resp.status_code == 401

    def test_returns_200_for_own_artifact(self):
        client, _, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}")

        assert resp.status_code == 200

    def test_returns_artifact_metadata(self):
        client, _, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}")

        body = resp.json()
        assert body["artifact_id"] == str(artifact_id)
        assert "stage" in body
        assert "artifact_type" in body
        assert "has_markdown" in body

    def test_returns_404_for_unknown(self):
        store = InMemoryMetadataStore()
        _make_alice(store)
        client = _build_app(store)
        _login_alice(client)

        resp = client.get("/artifacts/art_" + "a" * 32)

        assert resp.status_code == 404

    def test_returns_404_for_other_users_artifact(self):
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        bob_id = _make_bob(store)
        run_id = _make_run(store, alice_id)
        artifact_id = _seed_artifact(store, obj_store, alice_id, run_id)

        bob_client = _build_app(store, obj_store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/artifacts/{artifact_id}")

        assert resp.status_code == 404

    def test_does_not_expose_object_key(self):
        client, _, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}")

        body_str = str(resp.json())
        assert "object_key" not in body_str
        assert ".json" not in body_str

    def test_does_not_expose_user_id(self):
        client, _, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}")

        assert "user_id" not in resp.json()


# ── TestDownloadArtifact ──────────────────────────────────────────────────────


class TestDownloadArtifact:
    def _setup(self, *, has_markdown: bool = False) -> tuple[TestClient, ArtifactId]:
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        run_id = _make_run(store, alice_id)
        artifact_id = _seed_artifact(store, obj_store, alice_id, run_id, has_markdown=has_markdown)
        client = _build_app(store, obj_store)
        _login_alice(client)
        return client, artifact_id

    def test_requires_auth(self):
        store = InMemoryMetadataStore()
        client = _build_app(store)

        resp = client.get("/artifacts/art_" + "a" * 32 + "/download?format=json")

        assert resp.status_code == 401

    def test_json_download_returns_200(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert resp.status_code == 200

    def test_markdown_download_returns_200_when_has_markdown(self):
        client, artifact_id = self._setup(has_markdown=True)

        resp = client.get(f"/artifacts/{artifact_id}/download?format=markdown")

        assert resp.status_code == 200

    def test_download_returns_artifact_id(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert resp.json()["artifact_id"] == str(artifact_id)

    def test_download_returns_non_empty_url(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert resp.json()["download_url"] != ""

    def test_download_url_expires_at_is_in_future(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        expires_str = resp.json()["expires_at"]
        expires = datetime.fromisoformat(expires_str)
        assert expires > datetime.now(tz=timezone.utc)

    def test_download_returns_format_in_response(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert resp.json()["format"] == "json"

    def test_download_markdown_returns_markdown_format(self):
        client, artifact_id = self._setup(has_markdown=True)

        resp = client.get(f"/artifacts/{artifact_id}/download?format=markdown")

        assert resp.json()["format"] == "markdown"

    def test_download_returns_404_for_unknown(self):
        store = InMemoryMetadataStore()
        _make_alice(store)
        client = _build_app(store)
        _login_alice(client)

        resp = client.get("/artifacts/art_" + "a" * 32 + "/download?format=json")

        assert resp.status_code == 404

    def test_download_returns_404_for_other_users_artifact(self):
        store = InMemoryMetadataStore()
        obj_store = InMemoryObjectStore()
        alice_id = _make_alice(store)
        _make_bob(store)
        run_id = _make_run(store, alice_id)
        artifact_id = _seed_artifact(store, obj_store, alice_id, run_id)

        bob_client = _build_app(store, obj_store)
        bob_client.post("/auth/login", json={"username": "bob", "password": "b0bpass"})

        resp = bob_client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert resp.status_code == 404

    def test_markdown_download_returns_404_if_no_markdown(self):
        """Requesting markdown for an artifact with has_markdown=False → 404."""
        client, artifact_id = self._setup(has_markdown=False)

        resp = client.get(f"/artifacts/{artifact_id}/download?format=markdown")

        assert resp.status_code == 404

    def test_invalid_format_returns_422(self):
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=xml")

        assert resp.status_code == 422

    def test_download_does_not_expose_object_key(self):
        """The download URL must not contain the raw internal key path."""
        client, artifact_id = self._setup()

        resp = client.get(f"/artifacts/{artifact_id}/download?format=json")

        assert "object_key" not in resp.json()
