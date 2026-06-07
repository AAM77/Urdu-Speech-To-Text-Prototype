"""FastAPI dependency wiring — cloud-neutral adapter injection."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status

from urdu_pipeline.application.ports import (
    CacheStore,
    JobQueue,
    MetadataStore,
    ObjectStore,
    SecretProvider,
)
from urdu_pipeline.application.ports.services import AuthPrincipal
from urdu_pipeline.auth.bearer import resolve_bearer_token
from urdu_pipeline.auth.hashing import BcryptHasher, PasswordHasher
from urdu_pipeline.auth.sessions import resolve_session
from urdu_pipeline.api.middleware.rate_limit import InMemoryRateLimiter, RateLimiter
from urdu_pipeline.domain import ServiceIdentityId


@dataclass
class AppState:
    """Holder for all adapter instances injected into the application.

    Pass an ``AppState`` to ``create_app`` to swap in-memory fakes for tests
    or real adapters for production without any changes to route code.

    ``password_hasher`` defaults to ``BcryptHasher()`` so production callers
    only need to supply the stores/providers.  Tests can override it with a
    deterministic fake to avoid bcrypt's intentional cost.

    ``login_rate_limiter`` defaults to 10 requests per 60 seconds.  Tests can
    inject an ``InMemoryRateLimiter`` with a low limit to verify 429 behavior.

    ``job_queue`` is optional.  When ``None``, run creation succeeds but no
    job is enqueued.  Production deployments must supply a real queue adapter.
    """

    metadata_store: MetadataStore
    object_store: ObjectStore
    cache_store: CacheStore
    secret_provider: SecretProvider
    password_hasher: PasswordHasher = field(default_factory=BcryptHasher)
    login_rate_limiter: RateLimiter = field(
        default_factory=lambda: InMemoryRateLimiter(limit=10, window_seconds=60)
    )
    job_queue: JobQueue | None = None
    service_auth_token: str | None = None
    service_identity_id: ServiceIdentityId = field(default_factory=ServiceIdentityId.new)


def get_app_state(request: Request) -> AppState:
    """FastAPI dependency that resolves the AppState stored on ``app.state``."""
    return request.app.state.app_state


def get_metadata_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> MetadataStore:
    return state.metadata_store


def get_object_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> ObjectStore:
    return state.object_store


def get_job_queue(
    state: Annotated[AppState, Depends(get_app_state)],
) -> JobQueue | None:
    return state.job_queue


def get_cache_store(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CacheStore:
    return state.cache_store


def get_secret_provider(
    state: Annotated[AppState, Depends(get_app_state)],
) -> SecretProvider:
    return state.secret_provider


def get_password_hasher(
    state: Annotated[AppState, Depends(get_app_state)],
) -> PasswordHasher:
    return state.password_hasher


def get_login_rate_limiter(
    state: Annotated[AppState, Depends(get_app_state)],
) -> RateLimiter:
    return state.login_rate_limiter


def check_login_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_login_rate_limiter)],
) -> None:
    """FastAPI dependency: enforce per-IP rate limit on login."""
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.check_and_increment(f"login:{client_ip}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )


_SESSION_COOKIE = "session"


def get_principal_from_session(
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> AuthPrincipal | None:
    """Return the AuthPrincipal from the session cookie, or None."""
    if session is None:
        return None
    return resolve_session(metadata_store, raw_token=session)


def get_principal_from_bearer(
    request: Request,
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> AuthPrincipal | None:
    """Return the AuthPrincipal from the Authorization: Bearer header, or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_token = auth[len("Bearer "):]
    return resolve_bearer_token(metadata_store, raw_token=raw_token)


def require_principal(
    session_principal: Annotated[AuthPrincipal | None, Depends(get_principal_from_session)],
    bearer_principal: Annotated[AuthPrincipal | None, Depends(get_principal_from_bearer)],
) -> AuthPrincipal:
    """Require a valid principal from either session cookie or Bearer token.

    Raises 401 if neither is present or valid.
    """
    principal = session_principal or bearer_principal
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return principal


def get_principal_from_service_token(
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
) -> AuthPrincipal | None:
    """Return a service AuthPrincipal when the Bearer token matches the
    configured ``service_auth_token``.

    Returns ``None`` if:
    * ``service_auth_token`` is not configured (``None`` in ``AppState``).
    * No ``Authorization: Bearer`` header is present.
    * The token does not match (constant-time comparison to prevent timing attacks).
    """
    if state.service_auth_token is None:
        return None
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw = auth[len("Bearer "):]
    if not raw:
        return None
    if not secrets.compare_digest(raw.encode(), state.service_auth_token.encode()):
        return None
    return AuthPrincipal(
        principal_id=state.service_identity_id,
        kind="service",
        scopes=frozenset({"processor"}),
    )


def require_service_principal(
    principal: Annotated[AuthPrincipal | None, Depends(get_principal_from_service_token)],
) -> AuthPrincipal:
    """Require a service principal.  Raises 401 if the token is absent or invalid."""
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service authentication required.",
        )
    return principal


def require_session_principal(
    principal: Annotated[AuthPrincipal | None, Depends(get_principal_from_session)],
) -> AuthPrincipal:
    """Require a valid session-cookie principal (no bearer token accepted).

    Used on token management routes so bearer tokens cannot create more tokens.
    """
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return principal


__all__ = [
    "AppState",
    "check_login_rate_limit",
    "get_app_state",
    "get_cache_store",
    "get_job_queue",
    "get_login_rate_limiter",
    "get_metadata_store",
    "get_object_store",
    "get_password_hasher",
    "get_principal_from_bearer",
    "get_principal_from_service_token",
    "get_principal_from_session",
    "get_secret_provider",
    "require_principal",
    "require_service_principal",
    "require_session_principal",
]
