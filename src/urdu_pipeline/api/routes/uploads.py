"""Upload lifecycle routes — init, status check, and complete.

Security invariants:
- No internal object key is ever returned to the caller.
- Ownership is enforced: callers may only see and act on their own uploads.
- Mutating routes (init, complete) require CSRF when using session auth.
- Both session-cookie and bearer-token auth are accepted on all routes here,
  so automated pipelines can use bearer tokens without a browser session.

Routes:
  POST /uploads/init                  — allocate slot + return signed PUT URL
  GET  /uploads/{upload_id}           — fetch upload status
  POST /uploads/{upload_id}/complete  — mark upload as completed
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from urdu_pipeline.api.dependencies import (
    get_metadata_store,
    get_object_store,
    require_principal,
)
from urdu_pipeline.api.middleware.csrf import require_csrf
from urdu_pipeline.api.schemas import (
    CompleteUploadRequest,
    InitUploadRequest,
    InitUploadResponse,
    UploadResponse,
)
from urdu_pipeline.application.ports import MetadataStore, ObjectStore
from urdu_pipeline.application.ports.services import AuthPrincipal, UploadRecord
from urdu_pipeline.application.ports.storage import ObjectMetadata
from urdu_pipeline.domain import UserId
from urdu_pipeline.domain.ids import UploadId
from urdu_pipeline.domain.states import UploadStatus

router = APIRouter(prefix="/uploads", tags=["uploads"])

_SIGNED_URL_TTL = timedelta(hours=1)


def _upload_object_key(upload_id: UploadId) -> str:
    """Derive the internal object-store key from an upload ID.

    The key is never returned to callers; it is computed on-the-fly whenever
    the server needs to interact with object storage.
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
    try:
        uid = UploadId(upload_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        )

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    record = metadata_store.get_upload(user_id=user_id, upload_id=uid)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        )

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
    try:
        uid = UploadId(upload_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        )

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    record = metadata_store.get_upload(user_id=user_id, upload_id=uid)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        )

    completed = dataclasses.replace(record, status=UploadStatus.COMPLETED)
    metadata_store.update_upload(completed)

    return _to_response(completed)


__all__ = ["router"]
