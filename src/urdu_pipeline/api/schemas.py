"""Strict public request/response schemas for the Urdu Pipeline API.

Security invariants enforced here:

* ``extra="forbid"`` on every model — unknown fields from callers are rejected
  with a 422 Unprocessable Entity rather than silently ignored.

* No field exposes internal identifiers that must never cross the public
  boundary: ``user_id``, raw object-store keys, provider names, model IDs,
  prompts, or raw artifact text.

* Callers cannot inject provider/model/prompt fields into any request —
  those are server-controlled through the versioned provider registry.

Resource identifiers (``upload_id``, ``run_id``, ``artifact_id``, …) are
opaque domain ID strings (e.g. ``upl_<uuid-hex>``). They are safe to expose
as resource handles but carry no authorization by themselves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from urdu_pipeline.domain.states import ArtifactStage, ArtifactType, RunStatus, UploadStatus


class _StrictModel(BaseModel):
    """Base for all public API schemas.

    Subclasses inherit ``extra="forbid"`` so any unknown field in a caller's
    request body raises a validation error instead of being silently dropped.
    """

    model_config = ConfigDict(extra="forbid")


# ── Auth ──────────────────────────────────────────────────────────────────────


class LoginRequest(_StrictModel):
    """Credentials for a password-based login."""

    username: str
    password: str


class SessionResponse(_StrictModel):
    """Returned after a successful session login.

    Does not include ``user_id`` or any internal identifier.
    """

    username: str


# ── Bearer tokens ─────────────────────────────────────────────────────────────


class CreateTokenRequest(_StrictModel):
    """Request body for creating a new bearer token."""

    name: str
    description: str | None = None
    expires_in_days: int | None = None


class CreateTokenResponse(_StrictModel):
    """Returned once when a bearer token is created.

    The ``token`` field holds the raw value and is never stored server-side.
    It will not appear on subsequent list or get calls.
    """

    token_id: str
    name: str
    token: str
    created_at: datetime
    expires_at: datetime | None = None


class TokenSummary(_StrictModel):
    """Summary of a bearer token (raw value is never returned here)."""

    token_id: str
    name: str
    description: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class TokenListResponse(_StrictModel):
    tokens: list[TokenSummary]


class RevokeTokenResponse(_StrictModel):
    token_id: str
    revoked: bool


# ── Uploads ───────────────────────────────────────────────────────────────────


class InitUploadRequest(_StrictModel):
    """Request to initialise a new upload slot and receive a signed upload URL.

    Does not accept ``user_id``, object keys, or provider-related fields.
    """

    filename: str
    content_type: str
    size_bytes: int


class InitUploadResponse(_StrictModel):
    """Response containing the signed upload URL.

    ``upload_url`` is a short-lived signed URL for the object store.
    No raw object key is returned.
    """

    upload_id: str
    upload_url: str
    upload_url_expires_at: datetime
    status: UploadStatus


class UploadPartInfo(_StrictModel):
    """One completed part of a multipart upload."""

    part_number: int
    etag: str


class CompleteUploadRequest(_StrictModel):
    """Signals that the client has finished uploading.

    ``parts`` is required for multipart uploads; omit for single-part.
    """

    parts: list[UploadPartInfo] | None = None


class UploadResponse(_StrictModel):
    """Current state of an upload record.

    No object key is returned; the server resolves storage location
    from ``upload_id``.
    """

    upload_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: UploadStatus
    created_at: datetime
    expires_at: datetime | None = None


# ── Runs ──────────────────────────────────────────────────────────────────────


class CreateRunRequest(_StrictModel):
    """Request to start a processing run for a completed upload.

    Provider, model, and prompt selection are server-controlled and must
    not appear here.
    """

    upload_id: str
    description: str | None = None


class RunResponse(_StrictModel):
    """Current state of a processing run.

    Does not expose ``user_id``, job IDs, object keys, provider details,
    or intermediate artifact content.
    """

    run_id: str
    upload_id: str
    status: RunStatus
    description: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class RunListResponse(_StrictModel):
    runs: list[RunResponse]
    total: int


class CancelRunResponse(_StrictModel):
    run_id: str
    status: RunStatus


# ── Events ────────────────────────────────────────────────────────────────────


class EventResponse(_StrictModel):
    """A single structured pipeline event.

    Raw pipeline text (transcripts, translations, article body) is never
    returned here. Use the artifact download endpoint instead.
    """

    event_id: str
    run_id: str
    event_type: str
    created_at: datetime


class EventListResponse(_StrictModel):
    events: list[EventResponse]


# ── Artifacts ─────────────────────────────────────────────────────────────────


class ArtifactSummary(_StrictModel):
    """Metadata about one pipeline artifact.

    The artifact content (JSON or Markdown) is not included here.
    Use the download endpoint to retrieve a short-lived signed URL.
    No object key is exposed.
    """

    artifact_id: str
    run_id: str
    stage: ArtifactStage
    artifact_type: ArtifactType
    has_markdown: bool = False


class ArtifactListResponse(_StrictModel):
    artifacts: list[ArtifactSummary]


class ArtifactDownloadResponse(_StrictModel):
    """A short-lived signed download URL for one artifact.

    The raw artifact content is accessed through ``download_url``; it is
    not embedded in the API response.
    """

    artifact_id: str
    download_url: str
    expires_at: datetime
    format: Literal["json", "markdown"]


__all__ = [
    "ArtifactDownloadResponse",
    "ArtifactListResponse",
    "ArtifactSummary",
    "CancelRunResponse",
    "CompleteUploadRequest",
    "CreateRunRequest",
    "CreateTokenRequest",
    "CreateTokenResponse",
    "EventListResponse",
    "EventResponse",
    "InitUploadRequest",
    "InitUploadResponse",
    "LoginRequest",
    "RevokeTokenResponse",
    "RunListResponse",
    "RunResponse",
    "SessionResponse",
    "TokenListResponse",
    "TokenSummary",
    "UploadPartInfo",
    "UploadResponse",
]
