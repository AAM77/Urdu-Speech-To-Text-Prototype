"""Bearer token management routes — create, list, and revoke.

Design:
  - All routes require session-cookie auth (bearer tokens cannot create
    more bearer tokens to prevent unbounded token proliferation).
  - Bearer token auth IS accepted on GET /tokens so that automated
    tooling can list its own tokens, but NOT on POST or DELETE.

Wait — on reflection, GET /tokens is also restricted to session auth to
keep the surface minimal.  Automated tooling should use the session
API.  Bearer tokens are for *resource* routes (uploads, runs, artifacts),
not for token management itself.

Routes:
  POST   /tokens               — create, returns raw token once
  GET    /tokens               — list summaries (no raw token, no hash)
  DELETE /tokens/{token_id}    — revoke
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from urdu_pipeline.api.dependencies import (
    get_metadata_store,
    require_principal,
    require_session_principal,
)
from urdu_pipeline.api.schemas import (
    CreateTokenRequest,
    CreateTokenResponse,
    RevokeTokenResponse,
    TokenListResponse,
    TokenSummary,
)
from urdu_pipeline.application.ports import MetadataStore
from urdu_pipeline.application.ports.services import AuthPrincipal
from urdu_pipeline.auth.bearer import (
    create_bearer_token,
    revoke_bearer_token,
)
from urdu_pipeline.domain.ids import TokenId

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("", response_model=CreateTokenResponse, status_code=status.HTTP_200_OK)
def create_token(
    body: CreateTokenRequest,
    principal: Annotated[AuthPrincipal, Depends(require_session_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> CreateTokenResponse:
    """Create a new bearer token.  The raw token is returned exactly once."""
    expires_in = (
        timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    raw_token, record = create_bearer_token(
        metadata_store,
        user_id=principal.principal_id,  # type: ignore[arg-type]
        name=body.name,
        description=body.description,
        expires_in=expires_in,
    )
    return CreateTokenResponse(
        token_id=str(record.token_id),
        name=record.name,
        token=raw_token,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("", response_model=TokenListResponse, status_code=status.HTTP_200_OK)
def list_tokens(
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> TokenListResponse:
    """List all bearer tokens for the authenticated user.

    The raw token is never included; only metadata is returned.
    """
    records = metadata_store.list_bearer_tokens_for_user(
        principal.principal_id  # type: ignore[arg-type]
    )
    return TokenListResponse(
        tokens=[
            TokenSummary(
                token_id=str(r.token_id),
                name=r.name,
                description=r.description,
                created_at=r.created_at,
                expires_at=r.expires_at,
                last_used_at=r.last_used_at,
            )
            for r in records
        ]
    )


@router.delete(
    "/{token_id}",
    response_model=RevokeTokenResponse,
    status_code=status.HTTP_200_OK,
)
def revoke_token(
    token_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_session_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> RevokeTokenResponse:
    """Revoke a bearer token by ID.

    Returns 404 if the token does not exist or does not belong to the caller.
    """
    try:
        tok = TokenId(token_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found.")

    record = metadata_store.get_bearer_token(tok)
    if record is None or record.user_id != principal.principal_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found.")

    try:
        revoke_bearer_token(metadata_store, token_id=tok)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found.")

    return RevokeTokenResponse(token_id=token_id, revoked=True)
