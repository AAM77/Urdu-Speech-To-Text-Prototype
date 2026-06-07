"""Run lifecycle routes — create, list, read, events, cancel.

Security invariants:
- No user_id, job_id, or provider details are ever returned to the caller.
- Ownership is enforced on every route: callers may only see and act on their
  own runs.
- Mutating routes (create, cancel) require CSRF when using session auth.
- Both session-cookie and bearer-token auth are accepted.

Business rules enforced here:
- A run can only be created for an upload with status COMPLETED.  Attempting
  to run an unfinished upload returns 422.
- On creation a JobRecord is written to the metadata store and the job is
  enqueued if a JobQueue adapter is configured on AppState.

Routes:
  POST /runs                     — create a run for a completed upload
  GET  /runs                     — list the caller's runs
  GET  /runs/{run_id}            — get one run
  GET  /runs/{run_id}/events     — list pipeline events (stub returns empty list)
  POST /runs/{run_id}/cancel     — cancel a pending or running run
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from urdu_pipeline.api.dependencies import (
    get_job_queue,
    get_metadata_store,
    require_principal,
)
from urdu_pipeline.api.middleware.csrf import require_csrf
from urdu_pipeline.api.schemas import (
    CancelRunResponse,
    CreateRunRequest,
    EventListResponse,
    RunListResponse,
    RunResponse,
)
from urdu_pipeline.application.ports import JobQueue, MetadataStore
from urdu_pipeline.application.ports.services import (
    AuthPrincipal,
    JobRecord,
    QueueMessage,
    RunRecord,
)
from urdu_pipeline.domain import UserId
from urdu_pipeline.domain.ids import JobId, RunId, UploadId
from urdu_pipeline.domain.states import JobStatus, RunStatus, UploadStatus

router = APIRouter(prefix="/runs", tags=["runs"])


def _resolve_run(
    run_id_str: str,
    principal: AuthPrincipal,
    metadata_store: MetadataStore,
) -> RunRecord:
    """Resolve a run by ID enforcing ownership.  Raises 404 on failure."""
    try:
        rid = RunId(run_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")

    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    record = metadata_store.get_run(user_id=user_id, run_id=rid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return record


def _to_response(record: RunRecord) -> RunResponse:
    return RunResponse(
        run_id=str(record.run_id),
        upload_id=str(record.upload_id) if record.upload_id else "",
        status=record.status,
        description=record.description,
        created_at=record.created_at,
    )


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def create_run(
    body: CreateRunRequest,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
    job_queue: Annotated[JobQueue | None, Depends(get_job_queue)],
) -> RunResponse:
    """Create a run for a completed upload.

    Validates upload ownership and completion status, creates a RunRecord and
    a JobRecord, then enqueues the job if a queue adapter is configured.
    """
    user_id: UserId = principal.principal_id  # type: ignore[assignment]

    try:
        uid = UploadId(body.upload_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")

    upload = metadata_store.get_upload(user_id=user_id, upload_id=uid)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")

    if upload.status != UploadStatus.COMPLETED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Upload must be completed before starting a run "
                f"(current status: {upload.status})."
            ),
        )

    run_id = RunId.new()
    run_record = RunRecord(
        user_id=user_id,
        run_id=run_id,
        status=RunStatus.PENDING,
        upload_id=uid,
        description=body.description,
    )
    metadata_store.create_run(run_record)

    job_id = JobId.new()
    job_record = JobRecord(
        user_id=user_id,
        run_id=run_id,
        job_id=job_id,
        status=JobStatus.QUEUED,
    )
    metadata_store.create_job(job_record)

    if job_queue is not None:
        job_queue.enqueue(QueueMessage(job_id=job_id))

    return _to_response(run_record)


@router.get(
    "",
    response_model=RunListResponse,
    status_code=status.HTTP_200_OK,
)
def list_runs(
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> RunListResponse:
    """List all runs owned by the authenticated user."""
    user_id: UserId = principal.principal_id  # type: ignore[assignment]
    records = metadata_store.list_runs(user_id=user_id)
    return RunListResponse(
        runs=[_to_response(r) for r in records],
        total=len(records),
    )


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    status_code=status.HTTP_200_OK,
)
def get_run(
    run_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> RunResponse:
    """Return the current state of a run.

    Returns 404 if the run does not exist or does not belong to the caller.
    """
    record = _resolve_run(run_id, principal, metadata_store)
    return _to_response(record)


@router.get(
    "/{run_id}/events",
    response_model=EventListResponse,
    status_code=status.HTTP_200_OK,
)
def get_run_events(
    run_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> EventListResponse:
    """Return pipeline events for a run.

    Ownership is verified before returning events; returns 404 if the run
    does not exist or belongs to another caller.

    Stores that implement durable stage events return them here.  Simpler test
    stores can omit that optional method and get an empty list.
    """
    run = _resolve_run(run_id, principal, metadata_store)
    list_events = getattr(metadata_store, "list_stage_events", None)
    if not callable(list_events):
        return EventListResponse(events=[])
    records = list_events(user_id=run.user_id, run_id=run.run_id)
    return EventListResponse(
        events=[
            {
                "event_id": f"{record.run_id}:{index}",
                "run_id": str(record.run_id),
                "event_type": record.event_type,
                "created_at": record.created_at,
            }
            for index, record in enumerate(records)
        ]
    )


@router.post(
    "/{run_id}/cancel",
    response_model=CancelRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def cancel_run(
    run_id: str,
    principal: Annotated[AuthPrincipal, Depends(require_principal)],
    metadata_store: Annotated[MetadataStore, Depends(get_metadata_store)],
) -> CancelRunResponse:
    """Request cancellation of a run.

    Transitions the run status to CANCELLED.  Returns 404 if the run does
    not exist or belongs to another caller.
    """
    record = _resolve_run(run_id, principal, metadata_store)
    cancelled = dataclasses.replace(record, status=RunStatus.CANCELLED)
    metadata_store.update_run(cancelled)
    return CancelRunResponse(run_id=run_id, status=cancelled.status)


__all__ = ["router"]
