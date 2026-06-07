"""Artifact read and download routes — Step 4.3.4.

Security invariants:
- No raw object key is returned to callers.  Download URLs are short-lived
  signed URLs generated server-side from an opaque key derived from the
  artifact ID.
- Ownership is enforced on every route: callers may only access artifacts
  belonging to runs they own.
- All routes accept both session-cookie and bearer-token authentication.

Object key derivation (server-internal, never exposed):
  JSON  → ``artifacts/{artifact_id}.json``
  MD    → ``artifacts/{artifact_id}.md``

Routes:
  GET /runs/{run_id}/artifacts               — list artifact summaries for a run
  GET /artifacts/{artifact_id}               — artifact metadata
  GET /artifacts/{artifact_id}/download      — short-lived signed download URL
     query: format=json|markdown (default: json)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from urdu_pipeline.api.dependencies import (
    get_metadata_store,
    get_object_store,
    require_principal,
)
from urdu_pipeline.api.schemas import (
    ArtifactDownloadResponse,
    ArtifactListResponse,
    ArtifactSummary,
)
from urdu_pipeline.application.ports import MetadataStore, ObjectStore
from urdu_pipeline.application.ports.services import ArtifactRecord, AuthPrincipal
from urdu_pipeline.domain import UserId
from urdu_pipeline.domain.ids import ArtifactId, RunId

_DOWNLOAD_URL_TTL = timedelta(hours=1)

runs_router = APIRouter(prefix="/runs", tags=["artifacts"])
artifacts_router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _artifact_json_key(artifact_id: ArtifactId) -> str:
    return f"artifacts/{artifact_id}.json"


def _artifact_md_key(artifact_id: ArtifactId) -> str:
    return f"artifacts/{artifact_id}.md"


def _to_summary(record: ArtifactRecord) -> ArtifactSummary:
    return ArtifactSummary(
        artifact_id=str(record.artifact_id),
        run_id=str(record.run_id),
        stage=record.stage,
        artifact_type=record.artifact_type,
        has_markdown=record.has_markdown,
    )


def _resolve_artifact(
    artifact_id_str: str,
    principal: AuthPrincipal,
    metadata_store: MetadataStore,
) -> ArtifactRecord:
    """Resolve an artifact by ID enforcing ownership.  Raises 404 on failure."""
    try:
        aid = ArtifactId(artifact_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    record = metadata_store.get_artifact(user_id=user_id, artifact_id=aid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    return record


# ── Nested under /runs ────────────────────────────────────────────────────────


@runs_router.get(
    "/{run_id}/artifacts",
    response_model=ArtifactListResponse,
    status_code=status.HTTP_200_OK,
)
def list_run_artifacts(
    run_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> ArtifactListResponse:
    """List artifact summaries for a run.

    Returns 404 if the run does not exist or belongs to another caller.
    Artifact content is never embedded here; use the download endpoint.
    """
    try:
        rid = RunId(run_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    run = metadata_store.get_run(user_id=user_id, run_id=rid)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")

    records = metadata_store.list_run_artifacts(user_id=user_id, run_id=rid)
    return ArtifactListResponse(artifacts=[_to_summary(r) for r in records])


# ── /artifacts routes ─────────────────────────────────────────────────────────


@artifacts_router.get(
    "/{artifact_id}",
    response_model=ArtifactSummary,
    status_code=status.HTTP_200_OK,
)
def get_artifact(
    artifact_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> ArtifactSummary:
    """Return artifact metadata.

    Artifact content (JSON or Markdown) is not embedded here.
    Use ``GET /artifacts/{artifact_id}/download`` to obtain a signed URL.
    Returns 404 if the artifact does not exist or belongs to another caller.
    """
    record = _resolve_artifact(artifact_id, principal, metadata_store)
    return _to_summary(record)


@artifacts_router.get(
    "/{artifact_id}/download",
    response_model=ArtifactDownloadResponse,
    status_code=status.HTTP_200_OK,
)
def download_artifact(
    artifact_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    format: Annotated[Literal["json", "markdown"], Query()] = "json",
) -> ArtifactDownloadResponse:
    """Return a short-lived signed download URL for an artifact.

    The ``format`` query parameter selects the representation:
    - ``json``     — structured JSON artifact content.
    - ``markdown`` — human-readable Markdown version (only available when
                     ``has_markdown`` is ``True`` on the artifact).

    Returns 404 if the artifact does not exist, belongs to another caller,
    or the requested format is not available (e.g. markdown when
    ``has_markdown=False``).

    The raw object key is never included in the response.
    """
    record = _resolve_artifact(artifact_id, principal, metadata_store)
    aid = record.artifact_id

    if format == "markdown" and not record.has_markdown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Markdown version is not available for this artifact.",
        )

    object_key = _artifact_md_key(aid) if format == "markdown" else _artifact_json_key(aid)

    signed_url = object_store.create_signed_download_url(
        object_key,
        expires_in=_DOWNLOAD_URL_TTL,
    )

    return ArtifactDownloadResponse(
        artifact_id=str(aid),
        download_url=signed_url.url,
        expires_at=signed_url.expires_at,
        format=format,
    )


__all__ = ["artifacts_router", "runs_router"]
