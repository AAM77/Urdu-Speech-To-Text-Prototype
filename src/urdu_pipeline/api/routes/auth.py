"""Session auth routes — login and logout.

Login flow:
  POST /auth/login  →  verify username/password, create session,
                        set HTTP-only cookie, return SessionResponse.

Logout flow:
  POST /auth/logout →  read session cookie, revoke session if found,
                        clear cookie, return 200.

No public signup endpoint.  Users are pre-configured by operators via
the admin CLI commands added in Step 4.2.1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from urdu_pipeline.api.dependencies import (
    get_metadata_store,
    get_password_hasher,
)
from urdu_pipeline.api.schemas import LoginRequest, SessionResponse
from urdu_pipeline.application.ports import MetadataStore
from urdu_pipeline.auth.hashing import PasswordHasher
from urdu_pipeline.auth.sessions import (
    _hash_token,
    create_session,
    revoke_session,
)
from urdu_pipeline.domain import UserStatus

router = APIRouter(prefix="/auth", tags=["auth"])

_SESSION_COOKIE = "session"
_SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days in seconds


@router.post("/login", response_model=SessionResponse, status_code=status.HTTP_200_OK)
def login(
    body: LoginRequest,
    response: Response,
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    password_hasher: Annotated[PasswordHasher, Depends(get_password_hasher)],
) -> SessionResponse:
    """Authenticate with username and password, create a session, set cookie."""
    user = metadata_store.get_user_by_username(body.username)
    if (
        user is None
        or user.status != UserStatus.ACTIVE
        or user.password_hash is None
        or not password_hasher.verify_secret(body.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    raw_token, _ = create_session(metadata_store, user_id=user.user_id)

    response.set_cookie(
        key=_SESSION_COOKIE,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=_SESSION_MAX_AGE,
    )
    return SessionResponse(username=user.username)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    response: Response,
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> dict:
    """Revoke the current session and clear the session cookie."""
    if session is not None:
        record = metadata_store.get_session_by_token_hash(_hash_token(session))
        if record is not None and record.revoked_at is None:
            revoke_session(metadata_store, session_id=record.session_id)

    response.delete_cookie(key=_SESSION_COOKIE, httponly=True, samesite="lax")
    return {"status": "logged_out"}
