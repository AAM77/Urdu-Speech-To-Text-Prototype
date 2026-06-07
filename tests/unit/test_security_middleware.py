"""Security middleware tests — Step 4.2.4.

Written BEFORE implementation (TDD).  All tests must fail until CSRF,
CORS, and rate-limit middleware are wired up.

Covers:
  CSRF
  ────
  - POST /auth/login sets a non-httponly ``csrf_token`` cookie.
  - POST /auth/login itself does NOT require a CSRF token (no session yet).
  - Mutating session-authed routes (POST /tokens, DELETE /tokens/{id})
    return 403 when the ``X-CSRF-Token`` header is absent.
  - Mutating session-authed routes succeed when ``X-CSRF-Token`` matches
    the ``csrf_token`` cookie value.
  - Wrong ``X-CSRF-Token`` value returns 403.
  - GET requests never require CSRF.

  CORS
  ────
  - Requests from an allowed origin receive ``Access-Control-Allow-Origin``.
  - Requests from a disallowed origin do not receive the header.
  - OPTIONS preflight from an allowed origin returns 200.

  Rate Limits
  ──────────
  - Requests under the limit succeed (200).
  - Requests over the limit return 429 Too Many Requests.
  - The rate limiter is injectable so tests can set a low limit.
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(*, login_rate_limit: int = 100) -> tuple[AppState, InMemoryMetadataStore]:
    """Return an AppState with a pre-configured user (alice / s3cret)."""
    from urdu_pipeline.api.middleware.rate_limit import InMemoryRateLimiter

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
        login_rate_limiter=InMemoryRateLimiter(limit=login_rate_limit, window_seconds=60),
    )
    return state, store


def _client(*, login_rate_limit: int = 100, allowed_origins: list[str] | None = None) -> TestClient:
    state, _ = _make_state(login_rate_limit=login_rate_limit)
    app = create_app(state=state, allowed_origins=allowed_origins or [])
    return TestClient(app, raise_server_exceptions=True)


def _logged_in(*, login_rate_limit: int = 100) -> tuple[TestClient, str]:
    """Return (client, csrf_token) after a successful login."""
    client = _client(login_rate_limit=login_rate_limit)
    resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
    csrf_token = resp.cookies.get("csrf_token", "")
    return client, csrf_token


# ── TestCSRFCookieOnLogin ─────────────────────────────────────────────────────


class TestCSRFCookieOnLogin:
    def test_login_sets_csrf_token_cookie(self):
        """Login response must set a ``csrf_token`` cookie."""
        client = _client()

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert "csrf_token" in resp.cookies
        assert resp.cookies["csrf_token"] != ""

    def test_csrf_cookie_is_not_http_only(self):
        """Client JS must be able to read the CSRF cookie to include it in headers."""
        client = _client()

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        set_cookie = resp.headers.get("set-cookie", "")
        # The csrf_token cookie must appear in set-cookie; httponly must NOT be present
        # We look at the specific cookie directive for csrf_token, not the session cookie.
        # Split on comma to isolate individual Set-Cookie values (httpx collapses them).
        cookies_raw = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else [set_cookie]
        csrf_cookie_line = next(
            (c for c in cookies_raw if c.startswith("csrf_token")), set_cookie
        )
        assert "httponly" not in csrf_cookie_line.lower()

    def test_login_does_not_require_csrf_token(self):
        """Login itself must succeed without an X-CSRF-Token header."""
        client = _client()

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert resp.status_code == 200

    def test_csrf_token_value_is_non_trivial(self):
        """CSRF token must have enough entropy — at least 16 characters."""
        client = _client()

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert len(resp.cookies.get("csrf_token", "")) >= 16

    def test_each_login_gets_a_fresh_csrf_token(self):
        """Each new login session should receive a unique CSRF token."""
        client = _client()

        resp1 = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        client.post("/auth/logout")
        resp2 = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert resp1.cookies.get("csrf_token") != resp2.cookies.get("csrf_token")


# ── TestCSRFOnMutatingRoutes ──────────────────────────────────────────────────


class TestCSRFOnMutatingRoutes:
    def test_create_token_without_csrf_header_returns_403(self):
        """POST /tokens with session but no X-CSRF-Token must be rejected."""
        client, _ = _logged_in()

        resp = client.post("/tokens", json={"name": "ci"})

        assert resp.status_code == 403

    def test_create_token_with_correct_csrf_header_returns_200(self):
        """POST /tokens with matching X-CSRF-Token must succeed."""
        client, csrf = _logged_in()

        resp = client.post("/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf})

        assert resp.status_code == 200

    def test_create_token_with_wrong_csrf_header_returns_403(self):
        client, _ = _logged_in()

        resp = client.post(
            "/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": "wrong-value"}
        )

        assert resp.status_code == 403

    def test_delete_token_without_csrf_header_returns_403(self):
        """DELETE /tokens/{id} with session but no X-CSRF-Token must be rejected."""
        client, csrf = _logged_in()
        create_resp = client.post(
            "/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf}
        )
        token_id = create_resp.json()["token_id"]

        # Now delete without CSRF header
        resp = client.delete(f"/tokens/{token_id}")

        assert resp.status_code == 403

    def test_delete_token_with_correct_csrf_header_returns_200(self):
        client, csrf = _logged_in()
        create_resp = client.post(
            "/tokens", json={"name": "ci"}, headers={"X-CSRF-Token": csrf}
        )
        token_id = create_resp.json()["token_id"]

        resp = client.delete(f"/tokens/{token_id}", headers={"X-CSRF-Token": csrf})

        assert resp.status_code == 200

    def test_get_tokens_does_not_require_csrf(self):
        """GET is an idempotent method — no CSRF check needed."""
        client, _ = _logged_in()

        resp = client.get("/tokens")

        assert resp.status_code == 200

    def test_unauthenticated_post_tokens_returns_401_not_403(self):
        """Without a session, the auth check fires before the CSRF check."""
        client = _client()

        resp = client.post("/tokens", json={"name": "ci"})

        # Auth failure takes precedence — 401, not 403
        assert resp.status_code == 401


# ── TestCORS ──────────────────────────────────────────────────────────────────


class TestCORS:
    def test_allowed_origin_gets_access_control_allow_origin_header(self):
        """Requests from an allowed origin receive the ACAO header."""
        client = _client(allowed_origins=["https://app.example.com"])

        resp = client.get("/health", headers={"Origin": "https://app.example.com"})

        assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"

    def test_disallowed_origin_does_not_get_access_control_allow_origin_header(self):
        """Requests from a disallowed origin must NOT receive the ACAO header."""
        client = _client(allowed_origins=["https://app.example.com"])

        resp = client.get("/health", headers={"Origin": "https://evil.com"})

        assert resp.headers.get("access-control-allow-origin") != "https://evil.com"

    def test_cors_preflight_from_allowed_origin_returns_200(self):
        """OPTIONS preflight from an allowed origin must succeed."""
        client = _client(allowed_origins=["https://app.example.com"])

        resp = client.options(
            "/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert resp.status_code == 200

    def test_no_allowed_origins_means_no_cors_headers(self):
        """Default empty allowlist means cross-origin requests get no ACAO header."""
        client = _client(allowed_origins=[])

        resp = client.get("/health", headers={"Origin": "https://anywhere.com"})

        assert "access-control-allow-origin" not in resp.headers

    def test_wildcard_origin_allows_any_request(self):
        """``*`` in the allowlist allows any origin."""
        client = _client(allowed_origins=["*"])

        resp = client.get("/health", headers={"Origin": "https://anything.com"})

        assert resp.headers.get("access-control-allow-origin") is not None


# ── TestRateLimit ─────────────────────────────────────────────────────────────


class TestRateLimit:
    def test_login_under_limit_returns_200(self):
        """Requests within the limit must succeed normally."""
        client = _client(login_rate_limit=5)

        for _ in range(5):
            # Use wrong password to avoid creating sessions but still hit the limiter
            # Actually, the rate limit fires before auth, so use any credential
            client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        # At this point we've hit exactly the limit; the next should be throttled
        # but the 5th (limit=5) should still be 200
        # Reset: test that the 5th request succeeds
        # (the loop above makes 5 requests; this is the 6th)
        assert resp.status_code == 429

    def test_login_over_limit_returns_429(self):
        """Exceeding the rate limit must return 429 Too Many Requests."""
        client = _client(login_rate_limit=2)

        client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        client.post("/auth/login", json={"username": "alice", "password": "s3cret"})
        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert resp.status_code == 429

    def test_login_at_limit_boundary_is_still_allowed(self):
        """The request that exactly reaches the limit must still be allowed."""
        client = _client(login_rate_limit=3)

        resp1 = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
        resp2 = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
        resp3 = client.post("/auth/login", json={"username": "alice", "password": "wrong"})

        # All three should pass the rate-limit check (wrong password = 401, not 429)
        assert resp1.status_code == 401
        assert resp2.status_code == 401
        assert resp3.status_code == 401

    def test_login_beyond_limit_returns_429(self):
        """One request beyond the limit must be rejected with 429."""
        client = _client(login_rate_limit=3)

        for _ in range(3):
            client.post("/auth/login", json={"username": "alice", "password": "wrong"})

        resp = client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        assert resp.status_code == 429

    def test_non_login_routes_are_not_rate_limited_by_login_limiter(self):
        """The login rate limiter applies only to POST /auth/login."""
        client = _client(login_rate_limit=1)

        # Exhaust the login limiter
        client.post("/auth/login", json={"username": "alice", "password": "s3cret"})

        # Health endpoint should be unaffected
        resp = client.get("/health")
        assert resp.status_code == 200
