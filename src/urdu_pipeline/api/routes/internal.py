"""Internal endpoints — accessible only to authenticated service identities.

These routes are reserved for the background processor and other trusted
services.  They are NOT part of the public user-facing API and must not be
reachable with user session cookies or user bearer tokens.

Stage 5.1.1: provides a ``/internal/ping`` sentinel so the processor can
verify connectivity and service-token validity before starting its work loop.
Subsequent steps (5.1.2+) will add job lifecycle endpoints here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from urdu_pipeline.api.dependencies import require_service_principal
from urdu_pipeline.application.ports.services import AuthPrincipal

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/ping")
def internal_ping(
    principal: Annotated[AuthPrincipal, Depends(require_service_principal)],
) -> dict:
    """Health-check endpoint for the processor service.

    Returns the resolved principal kind and scopes so callers can confirm
    they authenticated as a service identity with the expected privileges.
    """
    return {
        "status": "ok",
        "principal_kind": principal.kind,
        "scopes": sorted(principal.scopes),
    }
