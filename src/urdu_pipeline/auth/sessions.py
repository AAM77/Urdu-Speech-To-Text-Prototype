"""Session creation, resolution, and revocation.

Session tokens are high-entropy random values (64 hex characters from
``secrets.token_hex(32)``).  The raw token is sent to the client in an
HTTP-only cookie; only its SHA-256 hex digest is stored server-side.

This approach means:
- A DB breach does not reveal session tokens (SHA-256 of a random 32-byte
  token is not reversible in practice).
- Lookup is O(1): hash the cookie value, query by hash.
- No bcrypt needed here — bcrypt is for low-entropy passwords.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

from urdu_pipeline.application.ports.services import AuthPrincipal, SessionRecord
from urdu_pipeline.domain import SessionId, UserId


# ---------------------------------------------------------------------------
# Narrow store protocol
# ---------------------------------------------------------------------------


class _SessionStore(Protocol):
    def create_session(self, record: SessionRecord) -> None: ...

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...

    def revoke_session(self, session_id: SessionId, *, revoked_at: datetime) -> None: ...


# ---------------------------------------------------------------------------
# Token hashing
# ---------------------------------------------------------------------------


def _hash_token(raw_token: str) -> str:
    """SHA-256 hex digest of a raw session token.  Deterministic for lookup."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def create_session(
    store: _SessionStore,
    *,
    user_id: UserId,
    expires_in: timedelta = timedelta(days=7),
) -> tuple[str, SessionRecord]:
    """Create a new session and return ``(raw_token, record)``.

    The caller must send ``raw_token`` to the client (e.g. in an HTTP-only
    cookie).  Only the token hash is persisted; the raw value is never stored.
    """
    raw_token = secrets.token_hex(32)
    now = datetime.now(tz=timezone.utc)
    record = SessionRecord(
        session_id=SessionId.new(),
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=now + expires_in,
        created_at=now,
    )
    store.create_session(record)
    return raw_token, record


def resolve_session(
    store: _SessionStore,
    *,
    raw_token: str,
) -> AuthPrincipal | None:
    """Resolve a raw cookie token to an ``AuthPrincipal``.

    Returns ``None`` if the token is unknown, expired, or revoked.
    """
    record = store.get_session_by_token_hash(_hash_token(raw_token))
    if record is None:
        return None
    if record.revoked_at is not None:
        return None
    if datetime.now(tz=timezone.utc) >= record.expires_at:
        return None
    return AuthPrincipal(
        principal_id=record.user_id,
        kind="user",
        scopes=frozenset(),
    )


def revoke_session(
    store: _SessionStore,
    *,
    session_id: SessionId,
) -> None:
    """Mark a session as revoked so subsequent resolution returns ``None``."""
    store.revoke_session(session_id, revoked_at=datetime.now(tz=timezone.utc))


__all__ = ["create_session", "resolve_session", "revoke_session"]
