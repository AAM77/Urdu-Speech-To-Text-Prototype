"""Processor job lifecycle orchestration.

This module contains the pure Python functions that drive the processor's
interaction with the job queue and metadata store.  It has no HTTP, no
threading, and no external I/O — those concerns belong in the processor
command shell (Stage 5.1.1) and pipeline execution stages (Stage 5.2.x).

Functions
─────────
heartbeat(queue, lease, *, lease_seconds) -> JobLease
    Extend an active lease.  Call periodically from a background thread or
    asyncio task while the job handler runs.

claim_and_run(queue, metadata_store, *, worker_id, handler, ...) -> bool
    Claim one job, run the handler, and manage queue + metadata lifecycle.

Exceptions (raised by the handler, caught by claim_and_run)
───────────────────────────────────────────────────────────
TransientJobError  — retriable failure; job is re-enqueued (up to max_attempts)
FatalJobError      — unrecoverable failure; job is marked terminal immediately
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from urdu_pipeline.application.ports.services import (
    JobLease,
    JobQueue,
    MetadataStore,
)
from urdu_pipeline.domain import (
    JobStatus,
    RunStatus,
    ServiceIdentityId,
)

_DEFAULT_LEASE_SECONDS: int = 30
_DEFAULT_MAX_ATTEMPTS: int = 3


class TransientJobError(Exception):
    """The handler raises this to request a retry.

    Use for recoverable conditions: temporary network errors, resource
    contention, provider rate-limit responses, etc.
    """


class FatalJobError(Exception):
    """The handler raises this to abort without retrying.

    Use for unrecoverable conditions: corrupt job records, invalid pipeline
    input, budget exhaustion, etc.
    """


def heartbeat(
    queue: JobQueue,
    lease: JobLease,
    *,
    lease_seconds: int,
) -> JobLease:
    """Extend ``lease`` by ``lease_seconds`` from now.

    Returns the updated ``JobLease`` with the new ``expires_at``.  Raises
    ``KeyError`` if the lease is no longer active (already completed, retried,
    cancelled, or dead-lettered).

    In production, call this from a background thread at half the
    ``lease_seconds`` interval to keep the lease alive while the handler runs.
    """
    return queue.extend_lease(lease, lease_seconds=lease_seconds)


def claim_and_run(
    queue: JobQueue,
    metadata_store: MetadataStore,
    *,
    worker_id: ServiceIdentityId,
    handler: Callable[[JobLease, MetadataStore], None],
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """Claim one job and execute ``handler``, managing the full lifecycle.

    Parameters
    ──────────
    queue
        The job queue adapter.
    metadata_store
        The metadata store adapter (used to update ``JobRecord`` and
        ``RunRecord`` status).
    worker_id
        Service identity of this processor instance (for lease attribution).
    handler
        Callable that receives ``(lease, metadata_store)`` and performs the
        pipeline work.  Must raise ``TransientJobError`` or ``FatalJobError``
        on failure; any other exception is treated as fatal.
    lease_seconds
        Initial lease duration.  Use ``heartbeat()`` to extend it.
    max_attempts
        Maximum number of delivery attempts before a job is dead-lettered.

    Returns
    ───────
    True  — a job was claimed and processed (regardless of outcome).
    False — the queue was empty; the caller should back off before retrying.
    """
    lease = queue.claim(worker_id=worker_id, lease_seconds=lease_seconds)
    if lease is None:
        return False

    job = metadata_store.get_job_by_id(lease.job_id)
    if job is None:
        queue.mark_terminal_failure(
            lease, reason="job record not found in metadata store"
        )
        return True

    if job.status == JobStatus.CANCELLED:
        # Race: job was cancelled after it was enqueued but before we claimed
        # it.  Release cleanly without invoking the handler.
        queue.cancel(lease.job_id, reason="job already cancelled")
        return True

    # Transition to RUNNING before invoking the handler so that any inspection
    # of MetadataStore during execution sees the correct state.
    metadata_store.update_job(replace(job, status=JobStatus.RUNNING))

    run = metadata_store.get_run(user_id=job.user_id, run_id=job.run_id)
    if run is not None and run.status not in (
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ):
        metadata_store.update_run(replace(run, status=RunStatus.RUNNING))

    try:
        handler(lease, metadata_store)
    except TransientJobError as exc:
        if lease.attempt_number >= max_attempts:
            metadata_store.update_job(replace(job, status=JobStatus.FAILED))
            if run is not None:
                metadata_store.update_run(replace(run, status=RunStatus.FAILED))
            queue.dead_letter(
                lease,
                reason=f"max_attempts={max_attempts} exceeded: {exc}",
            )
        else:
            metadata_store.update_job(replace(job, status=JobStatus.QUEUED))
            queue.retry(lease, reason=str(exc))
        return True
    except Exception as exc:
        # Covers FatalJobError and any unexpected exception — treat as fatal.
        metadata_store.update_job(replace(job, status=JobStatus.FAILED))
        if run is not None:
            metadata_store.update_run(replace(run, status=RunStatus.FAILED))
        queue.mark_terminal_failure(lease, reason=str(exc))
        return True

    # ── Success ───────────────────────────────────────────────────────────────
    metadata_store.update_job(replace(job, status=JobStatus.SUCCEEDED))
    if run is not None:
        metadata_store.update_run(replace(run, status=RunStatus.SUCCEEDED))
    queue.complete(lease)
    return True
