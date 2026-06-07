"""Redis/Valkey job queue adapter.

Redis is used only for delivery. The persisted jobs table remains authoritative
for claim, lease, retry, cancellation, failure, and dead-letter state.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from urdu_pipeline.application.ports import JobLease, QueueMessage
from urdu_pipeline.domain import JobId, ServiceIdentityId

_SAFE_ROUTING_KEYS = {
    "correlation_id",
    "lease_hint",
    "priority",
    "queue",
    "retry_hint",
    "stage",
}
_SAFE_ROUTING_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$")


class RedisJobQueue:
    """JobQueue implementation backed by Redis/Valkey delivery lists."""

    def __init__(
        self,
        *,
        metadata_store: Any,
        redis_client: Any | None = None,
        redis_url: str | None = None,
        queue_name: str = "jobs",
        max_stale_messages_per_claim: int = 100,
    ) -> None:
        if not queue_name:
            raise ValueError("queue_name must be non-empty.")
        if max_stale_messages_per_claim <= 0:
            raise ValueError("max_stale_messages_per_claim must be positive.")
        self.metadata_store = metadata_store
        self.redis_client = redis_client or _build_redis_client(redis_url)
        self.queue_name = queue_name
        self.max_stale_messages_per_claim = max_stale_messages_per_claim

    def enqueue(self, message: QueueMessage) -> None:
        routing = _validate_routing(message.routing)
        payload = json.dumps(
            {
                "job_id": str(message.job_id),
                "routing": routing,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.redis_client.rpush(self.queue_name, payload)

    def claim(
        self,
        *,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
    ) -> JobLease | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        for _ in range(self.max_stale_messages_per_claim):
            payload = self.redis_client.lpop(self.queue_name)
            if payload is None:
                return None
            message = _decode_message(payload)
            if message is None:
                continue
            lease = self.metadata_store.claim_job(
                job_id=message.job_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if lease is not None:
                return lease
        return None

    def extend_lease(
        self,
        lease: JobLease,
        *,
        lease_seconds: int,
    ) -> JobLease:
        return self.metadata_store.extend_job_lease(
            lease,
            lease_seconds=lease_seconds,
        )

    def retry(self, lease: JobLease, *, reason: str) -> None:
        self.metadata_store.retry_job(lease, reason=reason)
        self.enqueue(QueueMessage(job_id=lease.job_id, routing=dict(lease.routing)))

    def mark_terminal_failure(self, lease: JobLease, *, reason: str) -> None:
        self.metadata_store.mark_job_terminal_failure(lease, reason=reason)

    def cancel(self, job_id: JobId, *, reason: str) -> None:
        self.metadata_store.cancel_job(job_id, reason=reason)

    def dead_letter(self, lease: JobLease, *, reason: str) -> None:
        self.metadata_store.dead_letter_job(lease, reason=reason)


def _build_redis_client(redis_url: str | None) -> Any:
    try:
        import redis
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "redis is required for RedisJobQueue. "
            "Install the queue extra, for example: pip install -e '.[queue]'."
        ) from exc
    if redis_url:
        return redis.Redis.from_url(redis_url)
    return redis.Redis()


def _decode_message(payload: object) -> QueueMessage | None:
    if isinstance(payload, bytes):
        raw = payload.decode("utf-8")
    else:
        raw = str(payload)
    try:
        decoded = json.loads(raw)
        job_id = JobId(str(decoded["job_id"]))
        routing = _validate_routing(decoded.get("routing") or {})
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return QueueMessage(job_id=job_id, routing=routing)


def _validate_routing(routing: Mapping[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in routing.items():
        if key not in _SAFE_ROUTING_KEYS:
            raise ValueError(f"unsafe routing metadata key: {key}")
        if not isinstance(value, str) or not _SAFE_ROUTING_VALUE_RE.fullmatch(value):
            raise ValueError(f"unsafe routing metadata value for {key}")
        safe[key] = value
    return safe


__all__ = ["RedisJobQueue"]
