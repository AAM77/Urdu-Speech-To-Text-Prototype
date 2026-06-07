"""Tests for processor job lifecycle — Step 5.1.2.

Design under test
─────────────────
* ``JobQueue.complete(lease)`` — acknowledges a successfully processed job;
  removes the active lease without re-enqueueing; a completed job is terminal
  (a duplicate queue message is dropped on the next claim).

* ``heartbeat(queue, lease, *, lease_seconds)`` — thin wrapper around
  ``queue.extend_lease``; raises ``KeyError`` when the lease is no longer
  active (already released, completed, retried, etc.).

* ``claim_and_run(queue, metadata_store, *, worker_id, handler, ...)``
  orchestrates the full lifecycle:
    - Empty queue → returns ``False``; queue is untouched.
    - Job claimed → returns ``True`` regardless of outcome.
    - Before handler: ``JobRecord.status`` set to RUNNING; ``RunRecord.status``
      set to RUNNING.
    - Success: status → SUCCEEDED; ``RunRecord.status`` → SUCCEEDED; lease
      completed in queue.
    - ``TransientJobError`` (attempt < max_attempts): status → QUEUED; job
      re-enqueued; NOT dead-lettered.
    - ``TransientJobError`` (attempt >= max_attempts): status → FAILED;
      ``RunRecord.status`` → FAILED; job dead-lettered.
    - Any other exception (``FatalJobError`` or unexpected): status → FAILED;
      ``RunRecord.status`` → FAILED; lease marked terminal.
    - Pre-cancelled job: ``queue.cancel`` called; handler NOT invoked.
    - Missing job record: terminal failure in queue.
    - Completed/terminal job re-delivered by queue: dropped automatically.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

import pytest

from urdu_pipeline.application.ports.services import (
    JobLease,
    JobRecord,
    QueueMessage,
    RunRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    JobId,
    JobStatus,
    RunStatus,
    ServiceIdentityId,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryJobQueue,
    InMemoryMetadataStore,
)
from urdu_pipeline.processor.lifecycle import (
    FatalJobError,
    TransientJobError,
    claim_and_run,
    heartbeat,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


_WORKER_ID = ServiceIdentityId.new()


def _make_queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


def _seed_job(
    store: InMemoryMetadataStore,
) -> tuple[UserId, JobRecord, RunRecord]:
    """Seed a minimal user → upload → run → job chain and return ids."""
    user_id = UserId.new()
    store.create_user(
        UserRecord(user_id=user_id, username="proc_user", status=UserStatus.ACTIVE)
    )
    upload_id = UploadId.new()
    store.create_upload(
        UploadRecord(
            user_id=user_id,
            upload_id=upload_id,
            status=UploadStatus.COMPLETED,
        )
    )
    from urdu_pipeline.domain import RunId, JobId
    run_id = RunId.new()
    run = RunRecord(user_id=user_id, run_id=run_id, status=RunStatus.QUEUED)
    store.create_run(run)
    job_id = JobId.new()
    job = JobRecord(user_id=user_id, run_id=run_id, job_id=job_id, status=JobStatus.QUEUED)
    store.create_job(job)
    return user_id, job, run


def _enqueue(queue: InMemoryJobQueue, job: JobRecord) -> None:
    queue.enqueue(QueueMessage(job_id=job.job_id))


def _noop_handler(lease: JobLease, store: InMemoryMetadataStore) -> None:
    pass


# ── JobQueue.complete — basic ─────────────────────────────────────────────────


def test_complete_removes_active_lease():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert lease is not None
    queue.complete(lease)

    # The lease is gone — a second complete on the same lease must raise.
    with pytest.raises(KeyError):
        queue.complete(lease)


def test_complete_on_nonexistent_lease_raises():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert lease is not None
    # Complete it once — valid.
    queue.complete(lease)
    # Second call must fail (lease gone).
    with pytest.raises(KeyError):
        queue.complete(lease)


def test_completed_job_is_not_reclaimed():
    """After complete(), a duplicate queue message for the same job is dropped."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert lease is not None
    queue.complete(lease)

    # Manually enqueue the same job again (simulates duplicate delivery).
    queue.enqueue(QueueMessage(job_id=job.job_id))
    # Should be silently dropped because the job is now terminal (completed).
    next_lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert next_lease is None


# ── Heartbeat / extend_lease ──────────────────────────────────────────────────


def test_heartbeat_returns_lease_with_later_expiry():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=10)
    assert lease is not None
    original_expiry = lease.expires_at

    extended = heartbeat(queue, lease, lease_seconds=60)
    assert extended.expires_at > original_expiry


def test_heartbeat_preserves_job_id_and_attempt_number():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=10)
    assert lease is not None
    extended = heartbeat(queue, lease, lease_seconds=60)

    assert extended.job_id == lease.job_id
    assert extended.attempt_number == lease.attempt_number


def test_heartbeat_on_completed_lease_raises():
    """Heartbeat on a lease that has already been completed must raise KeyError."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert lease is not None
    queue.complete(lease)

    with pytest.raises(KeyError):
        heartbeat(queue, lease, lease_seconds=60)


def test_heartbeat_on_retried_lease_raises():
    """Heartbeat on a lease that was already retried must raise KeyError."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    lease = queue.claim(worker_id=_WORKER_ID, lease_seconds=30)
    assert lease is not None
    queue.retry(lease, reason="transient")

    with pytest.raises(KeyError):
        heartbeat(queue, lease, lease_seconds=60)


# ── claim_and_run — empty queue ───────────────────────────────────────────────


def test_claim_and_run_returns_false_when_queue_empty():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=_noop_handler
    )
    assert result is False


# ── claim_and_run — success path ──────────────────────────────────────────────


def test_claim_and_run_returns_true_when_job_processed():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=_noop_handler
    )
    assert result is True


def test_successful_job_sets_job_status_succeeded():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=_noop_handler)

    updated = store.get_job_by_id(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.SUCCEEDED


def test_successful_job_updates_run_status_to_succeeded():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, run = _seed_job(store)
    _enqueue(queue, job)

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=_noop_handler)

    updated_run = store.get_run(user_id=run.user_id, run_id=run.run_id)
    assert updated_run is not None
    assert updated_run.status == RunStatus.SUCCEEDED


def test_successful_job_completes_lease_in_queue():
    """After success, the lease must be gone so duplicate delivery is dropped."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=_noop_handler)

    # Re-enqueue to simulate duplicate; must be silently ignored.
    queue.enqueue(QueueMessage(job_id=job.job_id))
    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=_noop_handler
    )
    assert result is False


def test_job_status_is_running_during_handler_execution():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    captured: list[JobStatus] = []

    def handler(lease: JobLease, s: InMemoryMetadataStore) -> None:
        rec = s.get_job_by_id(lease.job_id)
        assert rec is not None
        captured.append(rec.status)

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=handler)
    assert captured == [JobStatus.RUNNING]


def test_run_status_is_running_during_handler_execution():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, run = _seed_job(store)
    _enqueue(queue, job)

    captured: list[RunStatus] = []

    def handler(lease: JobLease, s: InMemoryMetadataStore) -> None:
        r = s.get_run(user_id=run.user_id, run_id=run.run_id)
        assert r is not None
        captured.append(r.status)

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=handler)
    assert captured == [RunStatus.RUNNING]


# ── claim_and_run — transient failure ────────────────────────────────────────


def test_transient_failure_requeues_job_for_retry():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    call_count = [0]

    def failing_then_ok(lease: JobLease, s: InMemoryMetadataStore) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise TransientJobError("temporary glitch")

    # First invocation fails transiently.
    claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=failing_then_ok, max_attempts=3
    )
    # Second invocation should succeed.
    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=failing_then_ok, max_attempts=3
    )
    assert result is True
    assert call_count[0] == 2


def test_transient_failure_resets_job_status_to_queued():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def always_transient(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise TransientJobError("temporary")

    claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=always_transient, max_attempts=3
    )
    updated = store.get_job_by_id(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.QUEUED


def test_transient_failure_at_max_attempts_dead_letters():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def always_transient(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise TransientJobError("temporary")

    for _ in range(3):
        claim_and_run(
            queue, store, worker_id=_WORKER_ID, handler=always_transient, max_attempts=3
        )

    # After 3 attempts the job must be dead-lettered — not re-enqueued.
    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=always_transient, max_attempts=3
    )
    assert result is False


def test_transient_failure_at_max_attempts_sets_job_failed():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def always_transient(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise TransientJobError("temporary")

    for _ in range(3):
        claim_and_run(
            queue, store, worker_id=_WORKER_ID, handler=always_transient, max_attempts=3
        )

    updated = store.get_job_by_id(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.FAILED


def test_transient_failure_at_max_attempts_sets_run_failed():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, run = _seed_job(store)
    _enqueue(queue, job)

    def always_transient(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise TransientJobError("temporary")

    for _ in range(3):
        claim_and_run(
            queue, store, worker_id=_WORKER_ID, handler=always_transient, max_attempts=3
        )

    updated_run = store.get_run(user_id=run.user_id, run_id=run.run_id)
    assert updated_run is not None
    assert updated_run.status == RunStatus.FAILED


# ── claim_and_run — fatal failure ─────────────────────────────────────────────


def test_fatal_failure_sets_job_status_failed():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def fatal(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise FatalJobError("unrecoverable")

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)
    updated = store.get_job_by_id(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.FAILED


def test_fatal_failure_sets_run_status_failed():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, run = _seed_job(store)
    _enqueue(queue, job)

    def fatal(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise FatalJobError("unrecoverable")

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)
    updated_run = store.get_run(user_id=run.user_id, run_id=run.run_id)
    assert updated_run is not None
    assert updated_run.status == RunStatus.FAILED


def test_fatal_failure_does_not_requeue():
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def fatal(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise FatalJobError("unrecoverable")

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)
    # Queue must be empty — no requeue.
    result = claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)
    assert result is False


def test_unexpected_exception_treated_as_fatal():
    """Any non-TransientJobError exception is treated as a fatal failure."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def explodes(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise RuntimeError("unexpected bug")

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=explodes)
    updated = store.get_job_by_id(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.FAILED
    # Not requeued.
    result = claim_and_run(queue, store, worker_id=_WORKER_ID, handler=explodes)
    assert result is False


# ── claim_and_run — cancellation ─────────────────────────────────────────────


def test_cancelled_job_is_skipped():
    """A job cancelled before claiming must not invoke the handler."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    # Cancel via MetadataStore before the processor claims it.
    # The processor should detect the CANCELLED status and skip.
    from urdu_pipeline.domain import JobStatus as JS
    store.update_job(replace(job, status=JS.CANCELLED))

    handler_called = [False]

    def handler(lease: JobLease, s: InMemoryMetadataStore) -> None:
        handler_called[0] = True

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=handler)
    assert not handler_called[0]


def test_cancelled_job_returns_true():
    """claim_and_run returns True (a job was claimed) even if it was pre-cancelled."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)
    store.update_job(replace(job, status=JobStatus.CANCELLED))

    result = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=_noop_handler
    )
    assert result is True


# ── claim_and_run — missing record ────────────────────────────────────────────


def test_missing_job_record_marks_terminal_in_queue():
    """If the job record is absent from MetadataStore, queue the job as terminal."""
    queue = _make_queue()
    store = InMemoryMetadataStore()

    # Enqueue a job_id that has NO corresponding record in MetadataStore.
    phantom_job_id = JobId.new()
    queue.enqueue(QueueMessage(job_id=phantom_job_id))

    handler_called = [False]

    def handler(lease: JobLease, s: InMemoryMetadataStore) -> None:
        handler_called[0] = True

    result = claim_and_run(queue, store, worker_id=_WORKER_ID, handler=handler)
    assert result is True
    assert not handler_called[0]
    # Queue must be empty — the phantom job was marked terminal.
    result2 = claim_and_run(
        queue, store, worker_id=_WORKER_ID, handler=_noop_handler
    )
    assert result2 is False


# ── Duplicate queue message ───────────────────────────────────────────────────


def test_duplicate_message_after_completion_is_dropped():
    """A duplicate delivery after a job completes is silently dropped."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    call_count = [0]

    def counting_handler(lease: JobLease, s: InMemoryMetadataStore) -> None:
        call_count[0] += 1

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=counting_handler)
    assert call_count[0] == 1

    # Simulate duplicate delivery.
    queue.enqueue(QueueMessage(job_id=job.job_id))
    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=counting_handler)
    # Handler must NOT be called again.
    assert call_count[0] == 1


def test_duplicate_message_after_terminal_failure_is_dropped():
    """A duplicate delivery after a fatal failure is silently dropped."""
    queue = _make_queue()
    store = InMemoryMetadataStore()
    _, job, _ = _seed_job(store)
    _enqueue(queue, job)

    def fatal(lease: JobLease, s: InMemoryMetadataStore) -> None:
        raise FatalJobError("fatal")

    claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)

    queue.enqueue(QueueMessage(job_id=job.job_id))
    result = claim_and_run(queue, store, worker_id=_WORKER_ID, handler=fatal)
    assert result is False
