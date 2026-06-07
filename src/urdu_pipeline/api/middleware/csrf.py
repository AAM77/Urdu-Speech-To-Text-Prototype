"""CSRF protection via the double-submit cookie pattern.

How it works
────────────
1. On login the server sets two cookies:
   - ``session``    — HTTP-only, used to authenticate requests.
   - ``csrf_token`` — NOT HTTP-only, so client JS can read it.

2. For state-mutating routes that accept session-cookie auth, the client
   must include an ``X-CSRF-Token`` request header whose value matches the
   ``csrf_token`` cookie value it received at login.

3. A cross-origin attacker can trigger the browser to send the session
   cookie automatically, but cannot read the ``csrf_token`` cookie from a
   different origin (same-origin policy).  Without that value they cannot
   set the matching header, so the CSRF check fails.

Bearer token requests are fully exempt — they require an explicit
``Authorization: Bearer`` header that browsers never attach automatically.

Usage
─────
Apply ``require_csrf`` as a FastAPI dependency on any route that:
  a) Accepts session-cookie auth, AND
  b) Performs a state-mutating operation (POST, PUT, DELETE, PATCH).

Read-only (GET, HEAD, OPTIONS) routes never need CSRF protection.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, status

_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"
CSRF_TOKEN_BYTES = 16  # 32 hex chars — adequate entropy for a CSRF nonce


def generate_csrf_token() -> str:
    """Generate a fresh, random CSRF token suitable for a cookie value."""
    return secrets.token_hex(CSRF_TOKEN_BYTES)


def require_csrf(
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias="csrf_token")] = None,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> None:
    """FastAPI dependency: enforce CSRF protection for session-authed requests.

    Behaviour:
    - If there is no ``session`` cookie the CSRF check is **skipped** — the
      caller is unauthenticated and the auth dependency will raise 401.
    - If a ``session`` cookie IS present the ``X-CSRF-Token`` header must
      exist and match the ``csrf_token`` cookie.  Mismatch → HTTP 403.

    This ordering ensures callers receive 401 (not authenticated) rather than
    403 (forbidden) when they have no session at all.
    """
    if session is None:
        # No session — skip CSRF; auth dependency will raise 401
        return
    if not csrf_header or not csrf_cookie:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token required.",
        )
    if not secrets.compare_digest(csrf_header, csrf_cookie):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid.",
        )


__all__ = ["CSRF_TOKEN_BYTES", "generate_csrf_token", "require_csrf"]
