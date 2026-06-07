"""Optional Redis/Valkey smoke checks for RedisJobQueue."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from urdu_pipeline.application.ports import JobLease, QueueMessage
from urdu_pipeline.domain import JobId, ServiceIdentityId
from urdu_pipeline.infrastructure.redis_queue import RedisJobQueue


class SmokeMetadataStore:
    def __init__(self, job_id: JobId) -> None:
        self.job_id = job_id
        self.state = "queued"
        self.attempts = 0

    def claim_job(
        self,
        *,
        job_id: JobId,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
    ) -> JobLease | None:
        del worker_id
        if job_id != self.job_id or self.state != "queued":
            return None
        self.state = "claimed"
        self.attempts += 1
        return JobLease(
            job_id=job_id,
            lease_id=f"smoke-{self.attempts}",
            attempt_number=self.attempts,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
            routing={"queue": "smoke"},
        )

    def retry_job(self, lease: JobLease, *, reason: str) -> None:
        del reason
        if lease.job_id != self.job_id:
            raise KeyError(lease.job_id)
        self.state = "queued"

    def extend_job_lease(self, lease: JobLease, *, lease_seconds: int) -> JobLease:
        return JobLease(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            attempt_number=lease.attempt_number,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
            routing=lease.routing,
        )

    def mark_job_terminal_failure(self, lease: JobLease, *, reason: str) -> None:
        del lease, reason
        self.state = "failed"

    def cancel_job(self, job_id: JobId, *, reason: str) -> bool:
        del reason
        if job_id != self.job_id:
            return False
        self.state = "cancelled"
        return True

    def dead_letter_job(self, lease: JobLease, *, reason: str) -> None:
        del lease, reason
        self.state = "dead_lettered"


def test_redis_job_queue_runs_against_configured_redis():
    if os.environ.get("RUN_REDIS_JOB_QUEUE_SMOKE") != "1":
        pytest.skip("set RUN_REDIS_JOB_QUEUE_SMOKE=1 to run the Redis smoke test")
    if importlib.util.find_spec("redis") is None:
        pytest.skip("redis is not installed")
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")

    import redis

    client = redis.Redis.from_url(redis_url)
    queue_name = f"smoke:jobs:{uuid.uuid4().hex}"
    job_id = JobId.new()
    metadata = SmokeMetadataStore(job_id)
    queue = RedisJobQueue(
        redis_client=client,
        metadata_store=metadata,
        queue_name=queue_name,
    )
    try:
        queue.enqueue(QueueMessage(job_id=job_id, routing={"queue": "smoke"}))
        lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
        assert lease is not None
        assert lease.job_id == job_id
        assert queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30) is None
        queue.retry(lease, reason="smoke")
        retried = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
        assert retried is not None
        assert retried.attempt_number == 2
    finally:
        client.delete(queue_name)
