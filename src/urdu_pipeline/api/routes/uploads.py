"""Upload lifecycle routes — single-part, multipart, and direct.

Security invariants:
- No internal object key is ever returned to the caller.
- Ownership is enforced on every route: callers may only see and act on their
  own uploads.
- Mutating routes (init, complete, abort, direct) require CSRF when using
  session auth; bearer-token callers are implicitly exempt.
- Both session-cookie and bearer-token auth are accepted on all routes so that
  automated pipelines can use bearer tokens without a browser session.

Routes:
  POST /uploads/multipart/init                         — start multipart, signed URL for part 1
  GET  /uploads/multipart/{upload_id}/parts/{n}        — signed URL for part n
  POST /uploads/multipart/{upload_id}/complete         — finalise multipart
  DELETE /uploads/multipart/{upload_id}                — abort multipart
  POST /uploads/direct                                 — direct body upload (≤ 50 MB)
  POST /uploads/init                                   — single-part signed PUT URL
  GET  /uploads/{upload_id}                            — fetch upload status
  POST /uploads/{upload_id}/complete                   — mark single-part upload complete

Route ordering note: literal-path segments (multipart/*, direct) are declared
before parameterised segments ({upload_id}) to prevent shadowing.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from urdu_pipeline.api.dependencies import (
    get_metadata_store,
    get_object_store,
    require_principal,
)
from urdu_pipeline.api.middleware.csrf import require_csrf
from urdu_pipeline.api.schemas import (
    _ALLOWED_CONTENT_TYPES,
    _ALLOWED_EXTENSIONS,
    MAX_DIRECT_UPLOAD_BYTES,
    MAX_PART_NUMBER,
    CompleteMultipartRequest,
    CompleteUploadRequest,
    InitMultipartUploadRequest,
    InitMultipartUploadResponse,
    InitUploadRequest,
    InitUploadResponse,
    PartUrlResponse,
    UploadResponse,
)
from urdu_pipeline.application.ports import MetadataStore, ObjectStore
from urdu_pipeline.application.ports.services import AuthPrincipal, UploadRecord
from urdu_pipeline.application.ports.storage import (
    MultipartUpload,
    MultipartPart,
    ObjectMetadata,
)
from urdu_pipeline.domain import UserId
from urdu_pipeline.domain.ids import UploadId
from urdu_pipeline.domain.states import UploadStatus

router = APIRouter(prefix="/uploads", tags=["uploads"])

_SIGNED_URL_TTL = timedelta(hours=1)


def _upload_object_key(upload_id: UploadId) -> str:
    """Derive the internal object-store key from an upload ID.

    The key is never returned to callers; computed on-the-fly whenever the
    server needs to interact with object storage.
    """
    return f"uploads/{upload_id}"


def _to_response(record: UploadRecord) -> UploadResponse:
    return UploadResponse(
        upload_id=str(record.upload_id),
        filename=record.original_filename or "",
        content_type=record.content_type or "",
        size_bytes=record.size_bytes or 0,
        status=record.status,
        created_at=record.created_at,
    )


def _resolve_upload(
    upload_id_str: str,
    principal: AuthPrincipal,
    metadata_store: MetadataStore,
) -> UploadRecord:
    """Resolve an upload by ID, enforcing ownership.  Raises 404 on failure."""
    try:
        uid = UploadId(upload_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    record = metadata_store.get_upload(user_id=user_id, upload_id=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    return record


def _rebuild_multipart(record: UploadRecord) -> MultipartUpload:
    """Reconstruct the object-store MultipartUpload handle from a stored record."""
    if record.multipart_upload_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload was not initiated as a multipart upload.",
        )
    return MultipartUpload(
        key=_upload_object_key(record.upload_id),
        upload_id=record.multipart_upload_id,
    )


# ── Multipart routes (declared before /{upload_id} to prevent shadowing) ──────


@router.post(
    "/multipart/init",
    response_model=InitMultipartUploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def init_multipart_upload(
    body: InitMultipartUploadRequest,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> InitMultipartUploadResponse:
    """Start a multipart upload and return a signed URL for part 1.

    The caller should PUT each part to its signed URL then call
    ``POST /uploads/multipart/{upload_id}/complete``.
    """
    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    upload_id = UploadId.new()
    object_key = _upload_object_key(upload_id)

    mp_upload = object_store.create_multipart_upload(
        object_key,
        metadata=ObjectMetadata(content_type=body.content_type),
    )
    part1_url = object_store.create_signed_part_upload_url(
        mp_upload,
        part_number=1,
        expires_in=_SIGNED_URL_TTL,
    )

    record = UploadRecord(
        user_id=user_id,
        upload_id=upload_id,
        status=UploadStatus.UPLOADING,
        original_filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        multipart_upload_id=mp_upload.upload_id,
    )
    metadata_store.create_upload(record)

    return InitMultipartUploadResponse(
        upload_id=str(upload_id),
        part_url=part1_url.url,
        part_url_expires_at=part1_url.expires_at,
        status=record.status,
    )


@router.get(
    "/multipart/{upload_id}/parts/{part_number}",
    response_model=PartUrlResponse,
    status_code=status.HTTP_200_OK,
)
def get_part_url(
    upload_id: str,
    part_number: int,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> PartUrlResponse:
    """Return a signed PUT URL for a specific multipart upload part.

    Part numbers must be between 1 and 10,000 (inclusive).
    Returns 404 if the upload does not exist or belongs to another user.
    """
    if part_number < 1 or part_number > MAX_PART_NUMBER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"part_number must be between 1 and {MAX_PART_NUMBER}.",
        )

    record = _resolve_upload(upload_id, principal, metadata_store)
    mp_upload = _rebuild_multipart(record)

    signed_url = object_store.create_signed_part_upload_url(
        mp_upload,
        part_number=part_number,
        expires_in=_SIGNED_URL_TTL,
    )

    return PartUrlResponse(
        upload_id=upload_id,
        part_number=part_number,
        part_url=signed_url.url,
        part_url_expires_at=signed_url.expires_at,
    )


@router.post(
    "/multipart/{upload_id}/complete",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def complete_multipart_upload(
    upload_id: str,
    body: CompleteMultipartRequest,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> UploadResponse:
    """Finalise a multipart upload.

    All parts must have been PUT to their respective signed URLs before
    calling this endpoint.  The ``parts`` list must be non-empty.
    """
    record = _resolve_upload(upload_id, principal, metadata_store)
    mp_upload = _rebuild_multipart(record)

    storage_parts = [
        MultipartPart(part_number=p.part_number, etag=p.etag) for p in body.parts
    ]
    object_store.complete_multipart_upload(mp_upload, storage_parts)

    completed = dataclasses.replace(record, status=UploadStatus.COMPLETED)
    metadata_store.update_upload(completed)

    return _to_response(completed)


@router.delete(
    "/multipart/{upload_id}",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def abort_multipart_upload(
    upload_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> UploadResponse:
    """Abort an in-progress multipart upload and release object-store resources."""
    record = _resolve_upload(upload_id, principal, metadata_store)
    mp_upload = _rebuild_multipart(record)

    object_store.abort_multipart_upload(mp_upload)

    cancelled = dataclasses.replace(record, status=UploadStatus.CANCELLED)
    metadata_store.update_upload(cancelled)

    return _to_response(cancelled)


# ── Direct upload (declared before /{upload_id} to prevent shadowing) ─────────


@router.post(
    "/direct",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def direct_upload(
    file: Annotated[UploadFile, File(description="Audio file to upload directly")],
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> UploadResponse:
    """Upload a small audio file directly to the API server (≤ 50 MB).

    The API streams the bytes to object storage and returns a COMPLETED upload
    record in a single round-trip.  For files larger than 50 MB use the
    signed-URL workflow (``POST /uploads/init``) or multipart upload.
    """
    filename = file.filename or ""
    content_type = file.content_type or ""

    ext = PurePosixPath(filename).suffix.lower()
    if not filename or ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File extension {ext!r} is not allowed.",
        )
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Content-Type {content_type!r} is not allowed.",
        )

    content = file.file.read()

    if len(content) == 0:
        raise HTTPException(
            status_code=422,
            detail="Uploaded file must not be empty.",
        )
    if len(content) > MAX_DIRECT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File exceeds the 50 MB direct-upload limit. Use /uploads/init for larger files.",
        )

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    upload_id = UploadId.new()
    object_key = _upload_object_key(upload_id)

    object_store.put_stream(
        object_key,
        BytesIO(content),
        metadata=ObjectMetadata(content_type=content_type),
    )

    record = UploadRecord(
        user_id=user_id,
        upload_id=upload_id,
        status=UploadStatus.COMPLETED,
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(content),
    )
    metadata_store.create_upload(record)

    return _to_response(record)


# ── Single-part upload routes ──────────────────────────────────────────────────


@router.post(
    "/init",
    response_model=InitUploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def init_upload(
    body: InitUploadRequest,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> InitUploadResponse:
    """Allocate an upload slot and return a short-lived signed PUT URL.

    The caller should PUT the file bytes directly to ``upload_url`` before
    it expires, then call ``POST /uploads/{upload_id}/complete``.
    """
    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    upload_id = UploadId.new()
    object_key = _upload_object_key(upload_id)

    signed_url = object_store.create_signed_upload_url(
        object_key,
        expires_in=_SIGNED_URL_TTL,
        metadata=ObjectMetadata(content_type=body.content_type),
    )

    record = UploadRecord(
        user_id=user_id,
        upload_id=upload_id,
        status=UploadStatus.INITIALIZED,
        original_filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
    )
    metadata_store.create_upload(record)

    return InitUploadResponse(
        upload_id=str(upload_id),
        upload_url=signed_url.url,
        upload_url_expires_at=signed_url.expires_at,
        status=record.status,
    )


@router.get(
    "/{upload_id}",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
)
def get_upload(
    upload_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> UploadResponse:
    """Return the current status of an upload.

    Returns 404 if the upload does not exist or does not belong to the caller.
    """
    record = _resolve_upload(upload_id, principal, metadata_store)
    return _to_response(record)


@router.post(
    "/{upload_id}/complete",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def complete_upload(
    upload_id: str,
    body: CompleteUploadRequest,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> UploadResponse:
    """Signal that the client has finished uploading.

    Transitions the upload status to COMPLETED.  Returns 404 if the upload
    does not exist or does not belong to the caller.
    """
    record = _resolve_upload(upload_id, principal, metadata_store)
    completed = dataclasses.replace(record, status=UploadStatus.COMPLETED)
    metadata_store.update_upload(completed)
    return _to_response(completed)


__all__ = ["router"]
