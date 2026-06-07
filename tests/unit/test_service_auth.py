"""Tests for service identity auth and processor command — Step 5.1.1.

Design under test
─────────────────
* ``AppState.service_auth_token`` holds the static shared secret for the
  processor service.  When ``None``, service auth is disabled.
* ``get_principal_from_service_token`` FastAPI dependency: resolves a service
  ``AuthPrincipal`` (kind="service", scopes={"processor"}) when the request
  carries ``Authorization: Bearer <service_auth_token>``.  Returns ``None``
  if the token is missing, wrong, or service auth is not configured.
* ``require_service_principal`` dependency: wraps the above, raising 401
  when the principal is ``None``.
* ``GET /internal/ping`` is a sentinel endpoint protected by
  ``require_service_principal`` and returns the resolved principal kind/scopes
  so integration tests can verify the resolved identity.
* ``require_principal`` (user endpoints) does not accept service principals —
  service tokens on user endpoints return 401 (token not in bearer store).
* ``process`` CLI command: skeleton that validates ``SERVICE_AUTH_TOKEN``
  config and exits 0 on ``--dry-run``, exits 1 when the token is absent.
"""

from __future__ import annotations

from typing import Generator

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from urdu_pipeline.api.app import create_app
from urdu_pipeline.api.dependencies import AppState
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryCacheStore,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemorySecretProvider,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_SERVICE_TOKEN = "svc-test-secret-abc123"
_WRONG_TOKEN = "not-the-right-token"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(*, service_auth_token: str | None = _SERVICE_TOKEN) -> AppState:
    return AppState(
        metadata_store=InMemoryMetadataStore(),
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
        service_auth_token=service_auth_token,
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_logged_in_client(state: AppState) -> tuple[TestClient, str, str]:
    """Create a user account, log in, and return (client, user_id, csrf_token)."""
    from urdu_pipeline.admin.users import admin_create_user
    from urdu_pipeline.auth.hashing import BcryptHasher

    hasher = BcryptHasher()
    record = admin_create_user(
        state.metadata_store,
        hasher,
        username="svctest",
        password="pass1234",
    )
    client = TestClient(create_app(state=state), raise_server_exceptions=True)
    resp = client.post("/auth/login", json={"username": "svctest", "password": "pass1234"})
    assert resp.status_code == 200
    csrf = resp.cookies.get("csrf_token", "")
    return client, str(record.user_id), csrf


def _make_user_bearer_token(state: AppState, client: TestClient, csrf: str) -> str:
    """Create a bearer token for an already-logged-in user and return the raw token."""
    resp = client.post(
        "/tokens",
        json={"name": "svctest-tok"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    return resp.json()["token"]


# ── AppState field ────────────────────────────────────────────────────────────


def test_app_state_accepts_service_auth_token_kwarg():
    """AppState must accept service_auth_token without error."""
    state = _make_state(service_auth_token="my-secret")
    assert state.service_auth_token == "my-secret"


def test_app_state_service_auth_token_defaults_to_none():
    state = AppState(
        metadata_store=InMemoryMetadataStore(),
        object_store=InMemoryObjectStore(),
        cache_store=InMemoryCacheStore(),
        secret_provider=InMemorySecretProvider(),
    )
    assert state.service_auth_token is None


# ── /internal/ping — happy paths ─────────────────────────────────────────────


def test_service_token_returns_200_on_internal_ping():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN))
    assert resp.status_code == 200


def test_internal_ping_response_body_contains_status_ok():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN))
    assert resp.json()["status"] == "ok"


def test_internal_ping_response_reports_service_principal_kind():
    """The endpoint must reflect the resolved principal kind so tests can verify it."""
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN))
    assert resp.json()["principal_kind"] == "service"


def test_internal_ping_response_includes_processor_scope():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN))
    scopes = resp.json()["scopes"]
    assert "processor" in scopes


# ── /internal/ping — rejection paths ─────────────────────────────────────────


def test_no_auth_header_returns_401_on_internal_ping():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping")
    assert resp.status_code == 401


def test_wrong_service_token_returns_401_on_internal_ping():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_WRONG_TOKEN))
    assert resp.status_code == 401


def test_service_auth_not_configured_returns_401_on_internal_ping():
    """When no service_auth_token is set in AppState, all service auth fails."""
    client = TestClient(create_app(state=_make_state(service_auth_token=None)))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN))
    assert resp.status_code == 401


def test_empty_bearer_returns_401_on_internal_ping():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_non_bearer_scheme_returns_401_on_internal_ping():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers={"Authorization": f"Token {_SERVICE_TOKEN}"})
    assert resp.status_code == 401


# ── User session/bearer cannot reach internal endpoints ──────────────────────


def test_user_session_cookie_cannot_reach_internal_ping():
    """A valid user session is not accepted on service-only endpoints."""
    state = _make_state()
    client, _, _ = _make_logged_in_client(state)
    # TestClient carries the session cookie automatically; no Authorization header
    resp = client.get("/internal/ping")
    assert resp.status_code == 401


def test_user_bearer_token_cannot_reach_internal_ping():
    """A valid user bearer token must not satisfy service auth on internal endpoints."""
    state = _make_state()
    client, _, csrf = _make_logged_in_client(state)
    user_token = _make_user_bearer_token(state, client, csrf)
    # Fresh client with no session cookies, only the user bearer token
    fresh = TestClient(create_app(state=state))
    resp = fresh.get("/internal/ping", headers=_auth_header(user_token))
    assert resp.status_code == 401


# ── Service token cannot reach user-only endpoints ───────────────────────────


def test_service_token_cannot_reach_runs_list():
    """Service tokens are not user principals and must be rejected from /runs."""
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/runs", headers=_auth_header(_SERVICE_TOKEN))
    assert resp.status_code == 401


def test_service_token_cannot_reach_uploads_init():
    client = TestClient(create_app(state=_make_state()))
    resp = client.post(
        "/uploads/init",
        json={"filename": "audio.mp3", "content_type": "audio/mpeg", "size_bytes": 1024},
        headers=_auth_header(_SERVICE_TOKEN),
    )
    assert resp.status_code == 401


def test_service_token_cannot_create_run():
    client = TestClient(create_app(state=_make_state()))
    from urdu_pipeline.domain import UploadId
    resp = client.post(
        "/runs",
        json={"upload_id": UploadId.new()},
        headers=_auth_header(_SERVICE_TOKEN),
    )
    assert resp.status_code == 401


def test_service_token_cannot_list_artifacts():
    client = TestClient(create_app(state=_make_state()))
    from urdu_pipeline.domain import RunId
    resp = client.get(
        f"/runs/{RunId.new()}/artifacts",
        headers=_auth_header(_SERVICE_TOKEN),
    )
    assert resp.status_code == 401


# ── Timing-safe comparison ────────────────────────────────────────────────────


def test_service_token_with_extra_leading_space_is_rejected():
    """Tokens with extra whitespace must not authenticate — guard against lax matching."""
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(" " + _SERVICE_TOKEN))
    assert resp.status_code == 401


def test_service_token_with_trailing_newline_is_rejected():
    client = TestClient(create_app(state=_make_state()))
    resp = client.get("/internal/ping", headers=_auth_header(_SERVICE_TOKEN + "\n"))
    assert resp.status_code == 401


# ── Processor CLI command ─────────────────────────────────────────────────────


def test_process_command_exists_in_cli():
    """The CLI must expose a 'process' sub-command for the background worker."""
    from urdu_pipeline.cli import app as cli_app
    runner = CliRunner()
    result = runner.invoke(cli_app, ["process", "--help"])
    assert result.exit_code == 0
    assert "process" in result.output.lower() or "processor" in result.output.lower()


def test_process_command_requires_service_token():
    """Running the processor without a service token must exit non-zero."""
    from urdu_pipeline.cli import app as cli_app
    runner = CliRunner()
    # Explicitly pass empty env so SERVICE_AUTH_TOKEN is absent
    result = runner.invoke(cli_app, ["process"], env={"SERVICE_AUTH_TOKEN": ""})
    assert result.exit_code != 0


def test_process_command_dry_run_exits_zero_with_token():
    """With a service token and --dry-run, the processor validates config and exits 0."""
    from urdu_pipeline.cli import app as cli_app
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["process", "--dry-run"],
        env={"SERVICE_AUTH_TOKEN": _SERVICE_TOKEN},
    )
    assert result.exit_code == 0


def test_process_command_dry_run_reports_ok():
    from urdu_pipeline.cli import app as cli_app
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["process", "--dry-run"],
        env={"SERVICE_AUTH_TOKEN": _SERVICE_TOKEN},
    )
    assert "valid" in result.output.lower() or "ok" in result.output.lower()
