"""Redis/Valkey job queue adapter contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from urdu_pipeline.application.ports import JobLease, JobQueue, QueueMessage
from urdu_pipeline.domain import JobId, ServiceIdentityId
from urdu_pipeline.infrastructure.redis_queue import RedisJobQueue


class FakeRedisClient:
    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = {}

    def rpush(self, name: str, value: str | bytes) -> int:
        payload = value.encode("utf-8") if isinstance(value, str) else value
        self.lists.setdefault(name, []).append(payload)
        return len(self.lists[name])

    def lpop(self, name: str) -> bytes | None:
        items = self.lists.setdefault(name, [])
        if not items:
            return None
        return items.pop(0)

    def llen(self, name: str) -> int:
        return len(self.lists.get(name, []))


class FakeAuthoritativeJobs:
    def __init__(self) -> None:
        self.states: dict[JobId, str] = {}
        self.routing: dict[JobId, dict[str, str]] = {}
        self.attempts: dict[JobId, int] = {}
        self.leases: dict[str, JobLease] = {}
        self.cancelled: list[tuple[JobId, str]] = []

    def add_job(
        self,
        job_id: JobId,
        *,
        state: str = "queued",
        routing: dict[str, str] | None = None,
    ) -> None:
        self.states[job_id] = state
        self.routing[job_id] = routing or {"queue": "default", "stage": "translator"}

    def claim_job(
        self,
        *,
        job_id: JobId,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
    ) -> JobLease | None:
        del worker_id
        if self.states.get(job_id) != "queued":
            return None
        self.states[job_id] = "claimed"
        attempt_number = self.attempts.get(job_id, 0) + 1
        self.attempts[job_id] = attempt_number
        lease = JobLease(
            job_id=job_id,
            lease_id=f"lease-{attempt_number}",
            attempt_number=attempt_number,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
            routing=dict(self.routing[job_id]),
        )
        self.leases[lease.lease_id] = lease
        return lease

    def extend_job_lease(self, lease: JobLease, *, lease_seconds: int) -> JobLease:
        if lease.lease_id not in self.leases:
            raise KeyError(lease.lease_id)
        extended = JobLease(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            attempt_number=lease.attempt_number,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
            routing=dict(lease.routing),
        )
        self.leases[lease.lease_id] = extended
        return extended

    def retry_job(self, lease: JobLease, *, reason: str) -> None:
        del reason
        self._require_lease(lease)
        self.leases.pop(lease.lease_id)
        self.states[lease.job_id] = "queued"

    def mark_job_terminal_failure(self, lease: JobLease, *, reason: str) -> None:
        del reason
        self._require_lease(lease)
        self.leases.pop(lease.lease_id)
        self.states[lease.job_id] = "failed"

    def cancel_job(self, job_id: JobId, *, reason: str) -> bool:
        self.cancelled.append((job_id, reason))
        if self.states.get(job_id) in {"failed", "dead_lettered", "cancelled"}:
            return False
        self.states[job_id] = "cancelled"
        return True

    def dead_letter_job(self, lease: JobLease, *, reason: str) -> None:
        del reason
        self._require_lease(lease)
        self.leases.pop(lease.lease_id)
        self.states[lease.job_id] = "dead_lettered"

    def _require_lease(self, lease: JobLease) -> None:
        if self.leases.get(lease.lease_id) != lease:
            raise KeyError(lease.lease_id)


def test_redis_job_queue_claims_only_when_authoritative_job_state_allows_it():
    redis = FakeRedisClient()
    metadata = FakeAuthoritativeJobs()
    queue = RedisJobQueue(
        redis_client=redis,
        metadata_store=metadata,
        queue_name="jobs",
    )
    assert isinstance(queue, JobQueue)
    job_id = JobId.new()
    metadata.add_job(job_id, routing={"queue": "db", "stage": "translator"})

    queue.enqueue(QueueMessage(job_id=job_id, routing={"queue": "redis", "stage": "article"}))
    queue.enqueue(QueueMessage(job_id=job_id, routing={"queue": "redis", "stage": "article"}))

    first = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    second = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)

    assert first is not None
    assert first.job_id == job_id
    assert dict(first.routing) == {"queue": "db", "stage": "translator"}
    assert second is None
    assert redis.llen("jobs") == 0


def test_redis_job_queue_skips_stale_messages_for_terminal_jobs():
    redis = FakeRedisClient()
    metadata = FakeAuthoritativeJobs()
    queue = RedisJobQueue(redis_client=redis, metadata_store=metadata)
    failed = JobId.new()
    cancelled = JobId.new()
    metadata.add_job(failed, state="failed")
    metadata.add_job(cancelled, state="cancelled")

    queue.enqueue(QueueMessage(job_id=failed, routing={"queue": "default"}))
    queue.enqueue(QueueMessage(job_id=cancelled, routing={"queue": "default"}))

    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None
    assert redis.llen("jobs") == 0


def test_redis_job_queue_retries_by_updating_authoritative_state_then_requeueing():
    redis = FakeRedisClient()
    metadata = FakeAuthoritativeJobs()
    queue = RedisJobQueue(redis_client=redis, metadata_store=metadata)
    job_id = JobId.new()
    metadata.add_job(job_id)
    queue.enqueue(QueueMessage(job_id=job_id, routing={"queue": "default"}))
    lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert lease is not None

    queue.retry(lease, reason="temporary failure")
    retried = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)

    assert retried is not None
    assert retried.job_id == job_id
    assert retried.attempt_number == 2


def test_redis_job_queue_lifecycle_methods_delegate_to_authoritative_store():
    redis = FakeRedisClient()
    metadata = FakeAuthoritativeJobs()
    queue = RedisJobQueue(redis_client=redis, metadata_store=metadata)
    failed = JobId.new()
    dead_lettered = JobId.new()
    cancelled = JobId.new()
    for job_id in (failed, dead_lettered, cancelled):
        metadata.add_job(job_id)

    queue.enqueue(QueueMessage(job_id=failed, routing={"queue": "default"}))
    failed_lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert failed_lease is not None
    extended = queue.extend_lease(failed_lease, lease_seconds=60)
    queue.mark_terminal_failure(extended, reason="retry limit exceeded")

    queue.enqueue(QueueMessage(job_id=dead_lettered, routing={"queue": "default"}))
    dead_letter_lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert dead_letter_lease is not None
    queue.dead_letter(dead_letter_lease, reason="poison message")

    queue.cancel(cancelled, reason="user requested cancellation")
    queue.enqueue(QueueMessage(job_id=cancelled, routing={"queue": "default"}))

    assert metadata.states[failed] == "failed"
    assert metadata.states[dead_lettered] == "dead_lettered"
    assert metadata.states[cancelled] == "cancelled"
    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None


def test_redis_job_queue_rejects_invalid_or_unsafe_messages():
    queue = RedisJobQueue(redis_client=FakeRedisClient(), metadata_store=FakeAuthoritativeJobs())

    for message in (
        QueueMessage(job_id=JobId.new(), routing={"user_id": "usr_bad"}),
        QueueMessage(job_id=JobId.new(), routing={"object_key": "tmp/users/u/source"}),
        QueueMessage(job_id=JobId.new(), routing={"queue": "../default"}),
    ):
        with pytest.raises(ValueError):
            queue.enqueue(message)

    queue.redis_client.rpush("jobs", json.dumps({"job_id": "not-a-job", "routing": {}}))
    assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None
