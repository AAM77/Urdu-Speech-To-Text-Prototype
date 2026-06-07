"""Bearer token creation, resolution, and revocation.

Bearer tokens are long-lived credentials for programmatic API access.  Like
session tokens, the raw value is shown to the caller exactly once at creation
time; only the SHA-256 hex digest is stored server-side.

Design rationale:
- SHA-256 (not bcrypt) is used for the token hash because the raw tokens are
  cryptographically random (64 hex chars from ``secrets.token_hex(32)``), so
  there is no need for bcrypt's slow KDF — the entropy is already high.
- ``last_used_at`` is updated on every successful resolution so operators can
  audit token activity without exposing the raw token value again.
- Revocation sets ``revoked_at``; the raw-token/hash mapping is unchanged so
  the store can still look up the record for audit purposes.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

from urdu_pipeline.application.ports.services import AuthPrincipal, BearerTokenRecord
from urdu_pipeline.domain import TokenId, UserId


# ---------------------------------------------------------------------------
# Narrow store protocol (tests provide a minimal fake; routes use MetadataStore)
# ---------------------------------------------------------------------------


class _BearerStore(Protocol):
    def create_bearer_token(self, record: BearerTokenRecord) -> None: ...

    def get_bearer_token_by_hash(self, token_hash: str) -> BearerTokenRecord | None: ...

    def update_bearer_token(self, record: BearerTokenRecord) -> None: ...


class _BearerRevokeStore(Protocol):
    """Narrow protocol for revocation — only needs hash + id lookup."""

    def get_bearer_token_by_hash(self, token_hash: str) -> BearerTokenRecord | None: ...

    def get_bearer_token(self, token_id: TokenId) -> BearerTokenRecord | None: ...

    def update_bearer_token(self, record: BearerTokenRecord) -> None: ...


# ---------------------------------------------------------------------------
# Internal token hashing
# ---------------------------------------------------------------------------


def _hash_token(raw_token: str) -> str:
    """SHA-256 hex digest of a raw bearer token.  Deterministic for lookup."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Bearer token lifecycle
# ---------------------------------------------------------------------------


def create_bearer_token(
    store: _BearerStore,
    *,
    user_id: UserId,
    name: str,
    description: str | None = None,
    expires_in: timedelta | None = None,
) -> tuple[str, BearerTokenRecord]:
    """Create a new bearer token and return ``(raw_token, record)``.

    The caller must deliver ``raw_token`` to the client exactly once (e.g. in
    the API response).  Only the SHA-256 hash is persisted; the raw value is
    never stored.
    """
    raw_token = secrets.token_hex(32)
    now = datetime.now(tz=timezone.utc)
    record = BearerTokenRecord(
        token_id=TokenId.new(),
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        name=name,
        description=description,
        created_at=now,
        expires_at=(now + expires_in) if expires_in is not None else None,
    )
    store.create_bearer_token(record)
    return raw_token, record


def resolve_bearer_token(
    store: _BearerStore,
    *,
    raw_token: str,
) -> AuthPrincipal | None:
    """Resolve a raw bearer token to an ``AuthPrincipal``.

    Returns ``None`` when the token is unknown, expired, or revoked.
    On success, ``last_used_at`` is updated in the store.
    """
    record = store.get_bearer_token_by_hash(_hash_token(raw_token))
    if record is None:
        return None
    if record.revoked_at is not None:
        return None
    now = datetime.now(tz=timezone.utc)
    if record.expires_at is not None and now >= record.expires_at:
        return None

    # Update last_used_at to track token activity
    updated = dc_replace(record, last_used_at=now)
    store.update_bearer_token(updated)

    return AuthPrincipal(
        principal_id=record.user_id,
        kind="user",
        scopes=frozenset(),
    )


def revoke_bearer_token(
    store: _BearerRevokeStore,
    *,
    token_id: TokenId,
) -> None:
    """Mark a bearer token as revoked.

    Raises ``KeyError`` if the token_id is not found — callers should surface
    this as a 404.
    """
    existing = store.get_bearer_token(token_id)
    if existing is None:
        raise KeyError(f"bearer token not found: {token_id}")
    updated = dc_replace(existing, revoked_at=datetime.now(tz=timezone.utc))
    store.update_bearer_token(updated)


__all__ = ["create_bearer_token", "resolve_bearer_token", "revoke_bearer_token"]
