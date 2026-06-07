"""In-memory adapters for tests and local contract checks."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Mapping, Sequence

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    BearerTokenRecord,
    BudgetDecision,
    BudgetService,
    CacheEntry,
    CacheScope,
    CacheStore,
    JobRecord,
    JobLease,
    JobQueue,
    MetadataStore,
    MultipartPart,
    MultipartUpload,
    ObjectInfo,
    ObjectMetadata,
    ObjectStore,
    ProviderConfigSnapshot,
    ProviderRegistry,
    QueueMessage,
    RunRecord,
    ServiceIdentityRecord,
    SecretProvider,
    SecretValue,
    SessionRecord,
    SignedUrl,
    UploadRecord,
    UsageLedger,
    UsageRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    JobId,
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ProviderRunId,
    RunId,
    ServiceIdentityId,
    SessionId,
    TokenId,
    UploadId,
    UserId,
)


@dataclass(frozen=True)
class _StoredObject:
    payload: bytes
    info: ObjectInfo


@dataclass(frozen=True)
class _PendingMultipartUpload:
    upload: MultipartUpload
    metadata: ObjectMetadata | None


def _validate_object_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("object key must be a non-empty string.")
    if key.startswith("/") or key.endswith("/") or "\\" in key or "//" in key:
        raise ValueError("object key must be a relative slash-separated key.")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("object key must not contain traversal segments.")
    return key


def _validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("object prefix must be a non-empty string.")
    probe = prefix[:-1] if prefix.endswith("/") else prefix
    _validate_object_key(probe)
    return prefix


def _object_info(
    *,
    key: str,
    payload: bytes,
    metadata: ObjectMetadata | None,
) -> ObjectInfo:
    checksum = hashlib.sha256(payload).hexdigest()
    return ObjectInfo(
        key=key,
        size_bytes=len(payload),
        etag=checksum,
        content_type=metadata.content_type if metadata else None,
        checksum_sha256=metadata.checksum_sha256 if metadata else None,
        user_metadata=dict(metadata.user_metadata) if metadata else {},
    )


class InMemoryObjectStore:
    """ObjectStore implementation backed by process memory."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredObject] = {}
        self._multipart_uploads: dict[str, _PendingMultipartUpload] = {}

    def put_stream(
        self,
        key: str,
        body: BinaryIO,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        payload = body.read()
        info = _object_info(key=safe_key, payload=payload, metadata=metadata)
        self._objects[safe_key] = _StoredObject(payload=payload, info=info)
        return info

    def get_stream(self, key: str) -> BinaryIO:
        safe_key = _validate_object_key(key)
        return io.BytesIO(self._objects[safe_key].payload)

    def head_object(self, key: str) -> ObjectInfo:
        safe_key = _validate_object_key(key)
        return self._objects[safe_key].info

    def create_signed_upload_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        metadata: ObjectMetadata | None = None,
    ) -> SignedUrl:
        safe_key = _validate_object_key(key)
        return SignedUrl(
            url=f"memory://upload/{safe_key}",
            method="PUT",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def create_signed_download_url(
        self,
        key: str,
        *,
        expires_in: timedelta,
        filename: str | None = None,
    ) -> SignedUrl:
        safe_key = _validate_object_key(key)
        self.head_object(safe_key)
        return SignedUrl(
            url=f"memory://download/{safe_key}",
            method="GET",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def delete_object(self, key: str) -> None:
        safe_key = _validate_object_key(key)
        self._objects.pop(safe_key, None)

    def list_prefix(self, prefix: str) -> Sequence[ObjectInfo]:
        safe_prefix = _validate_prefix(prefix)
        return [
            self._objects[key].info
            for key in sorted(self._objects)
            if key.startswith(safe_prefix)
        ]

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _validate_prefix(prefix)
        keys = [key for key in self._objects if key.startswith(safe_prefix)]
        for key in keys:
            del self._objects[key]
        return len(keys)

    def create_multipart_upload(
        self,
        key: str,
        *,
        metadata: ObjectMetadata | None = None,
    ) -> MultipartUpload:
        safe_key = _validate_object_key(key)
        upload = MultipartUpload(key=safe_key, upload_id=uuid.uuid4().hex)
        self._multipart_uploads[upload.upload_id] = _PendingMultipartUpload(
            upload=upload,
            metadata=metadata,
        )
        return upload

    def create_signed_part_upload_url(
        self,
        upload: MultipartUpload,
        *,
        part_number: int,
        expires_in: timedelta,
    ) -> SignedUrl:
        self._require_multipart(upload)
        if part_number <= 0:
            raise ValueError("part_number must be positive.")
        return SignedUrl(
            url=f"memory://multipart/{upload.upload_id}/{part_number}",
            method="PUT",
            expires_at=datetime.now(tz=timezone.utc) + expires_in,
        )

    def complete_multipart_upload(
        self,
        upload: MultipartUpload,
        parts: Sequence[MultipartPart],
    ) -> ObjectInfo:
        pending = self._require_multipart(upload)
        if not parts:
            raise ValueError("multipart upload must include at least one part.")
        payload = b"".join(
            f"{part.part_number}:{part.etag}:{part.size_bytes or 0}\n".encode("utf-8")
            for part in sorted(parts, key=lambda item: item.part_number)
        )
        info = _object_info(
            key=upload.key,
            payload=payload,
            metadata=pending.metadata,
        )
        self._objects[upload.key] = _StoredObject(payload=payload, info=info)
        del self._multipart_uploads[upload.upload_id]
        return info

    def abort_multipart_upload(self, upload: MultipartUpload) -> None:
        self._require_multipart(upload)
        del self._multipart_uploads[upload.upload_id]

    def _require_multipart(self, upload: MultipartUpload) -> _PendingMultipartUpload:
        pending = self._multipart_uploads.get(upload.upload_id)
        if pending is None or pending.upload.key != upload.key:
            raise KeyError(f"multipart upload not found: {upload.upload_id}")
        return pending


class InMemoryMetadataStore:
    """MetadataStore implementation backed by process memory."""

    def __init__(self) -> None:
        self._users: dict[UserId, UserRecord] = {}
        self._service_identities: dict[ServiceIdentityId, ServiceIdentityRecord] = {}
        self._sessions: dict[SessionId, SessionRecord] = {}
        self._sessions_by_token_hash: dict[str, SessionRecord] = {}
        self._bearer_tokens: dict[TokenId, BearerTokenRecord] = {}
        self._bearer_tokens_by_hash: dict[str, BearerTokenRecord] = {}
        self._uploads: dict[UploadId, UploadRecord] = {}
        self._runs: dict[RunId, RunRecord] = {}
        self._jobs: dict[JobId, JobRecord] = {}
        self._artifacts: dict[ArtifactId, ArtifactRecord] = {}

    def create_user(self, record: UserRecord) -> None:
        self._users[record.user_id] = record

    def get_user(self, user_id: UserId) -> UserRecord | None:
        return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> UserRecord | None:
        for record in self._users.values():
            if record.username == username:
                return record
        return None

    def update_user(self, record: UserRecord) -> None:
        if record.user_id not in self._users:
            raise KeyError(f"user not found: {record.user_id}")
        self._users[record.user_id] = record

    def list_users(self) -> Sequence[UserRecord]:
        return sorted(
            self._users.values(),
            key=lambda u: (u.created_at, str(u.user_id)),
        )

    def create_service_identity(self, record: ServiceIdentityRecord) -> None:
        self._service_identities[record.service_identity_id] = record

    def get_service_identity(
        self,
        service_identity_id: ServiceIdentityId,
    ) -> ServiceIdentityRecord | None:
        return self._service_identities.get(service_identity_id)

    def update_service_identity(self, record: ServiceIdentityRecord) -> None:
        if record.service_identity_id not in self._service_identities:
            raise KeyError(f"service identity not found: {record.service_identity_id}")
        self._service_identities[record.service_identity_id] = record

    def create_session(self, record: SessionRecord) -> None:
        self._sessions[record.session_id] = record
        self._sessions_by_token_hash[record.token_hash] = record

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        return self._sessions_by_token_hash.get(token_hash)

    def revoke_session(self, session_id: SessionId, *, revoked_at: datetime) -> None:
        from dataclasses import replace as dc_replace

        existing = self._sessions.get(session_id)
        if existing is None:
            raise KeyError(f"session not found: {session_id}")
        updated = dc_replace(existing, revoked_at=revoked_at)
        self._sessions[session_id] = updated
        self._sessions_by_token_hash[existing.token_hash] = updated

    def create_bearer_token(self, record: BearerTokenRecord) -> None:
        self._bearer_tokens[record.token_id] = record
        self._bearer_tokens_by_hash[record.token_hash] = record

    def get_bearer_token_by_hash(self, token_hash: str) -> BearerTokenRecord | None:
        return self._bearer_tokens_by_hash.get(token_hash)

    def get_bearer_token(self, token_id: TokenId) -> BearerTokenRecord | None:
        return self._bearer_tokens.get(token_id)

    def update_bearer_token(self, record: BearerTokenRecord) -> None:
        if record.token_id not in self._bearer_tokens:
            raise KeyError(f"bearer token not found: {record.token_id}")
        self._bearer_tokens[record.token_id] = record
        self._bearer_tokens_by_hash[record.token_hash] = record

    def list_bearer_tokens_for_user(self, user_id: UserId) -> Sequence[BearerTokenRecord]:
        return sorted(
            [r for r in self._bearer_tokens.values() if r.user_id == user_id],
            key=lambda r: r.created_at,
        )

    def create_upload(self, record: UploadRecord) -> None:
        self._require_user(record.user_id)
        self._uploads[record.upload_id] = record

    def get_upload(self, *, user_id: UserId, upload_id: UploadId) -> UploadRecord | None:
        record = self._uploads.get(upload_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def update_upload(self, record: UploadRecord) -> None:
        if record.upload_id not in self._uploads:
            raise KeyError(f"Upload not found: {record.upload_id}")
        self._uploads[record.upload_id] = record

    def list_uploads(self, *, user_id: UserId) -> Sequence[UploadRecord]:
        return sorted(
            (record for record in self._uploads.values() if record.user_id == user_id),
            key=lambda record: (record.created_at, str(record.upload_id)),
        )

    def create_run(self, record: RunRecord) -> None:
        self._require_user(record.user_id)
        self._runs[record.run_id] = record

    def get_run(self, *, user_id: UserId, run_id: RunId) -> RunRecord | None:
        record = self._runs.get(run_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def update_run(self, record: RunRecord) -> None:
        if record.run_id not in self._runs:
            raise KeyError(f"Run not found: {record.run_id}")
        self._runs[record.run_id] = record

    def list_runs(self, *, user_id: UserId) -> Sequence[RunRecord]:
        return sorted(
            (record for record in self._runs.values() if record.user_id == user_id),
            key=lambda record: (record.created_at, str(record.run_id)),
        )

    def create_job(self, record: JobRecord) -> None:
        self._require_user(record.user_id)
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        self._jobs[record.job_id] = record

    def get_job(self, *, user_id: UserId, job_id: JobId) -> JobRecord | None:
        record = self._jobs.get(job_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def get_job_by_id(self, job_id: JobId) -> JobRecord | None:
        """Processor-only lookup without ownership enforcement."""
        return self._jobs.get(job_id)

    def update_job(self, record: JobRecord) -> None:
        if record.job_id not in self._jobs:
            raise KeyError(f"job not found: {record.job_id}")
        self._jobs[record.job_id] = record

    def record_artifact(self, record: ArtifactRecord) -> None:
        self._require_user(record.user_id)
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        self._artifacts[record.artifact_id] = record

    def get_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactRecord | None:
        record = self._artifacts.get(artifact_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def list_run_artifacts(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[ArtifactRecord]:
        run = self._runs.get(run_id)
        if run is None or run.user_id != user_id:
            return []
        return sorted(
            (r for r in self._artifacts.values() if r.user_id == user_id and r.run_id == run_id),
            key=lambda r: (r.created_at, str(r.artifact_id)),
        )

    def _require_user(self, user_id: UserId) -> None:
        if user_id not in self._users:
            raise ValueError(f"user does not exist: {user_id}")

    def _require_run_owner(self, *, user_id: UserId, run_id: RunId) -> None:
        record = self._runs.get(run_id)
        if record is None or record.user_id != user_id:
            raise ValueError(f"run does not exist for user: {run_id}")


_SAFE_ROUTING_KEYS = {
    "correlation_id",
    "lease_hint",
    "priority",
    "queue",
    "retry_hint",
    "stage",
}
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$")
_CACHE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _validate_routing(routing: Mapping[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in routing.items():
        if key not in _SAFE_ROUTING_KEYS:
            raise ValueError(f"unsafe routing metadata key: {key}")
        if not isinstance(value, str) or not _SAFE_SEGMENT_RE.fullmatch(value):
            raise ValueError(f"unsafe routing metadata value for {key}")
        safe[key] = value
    return safe


def _validate_cache_segment(field: str, value: str) -> str:
    if not isinstance(value, str) or not _CACHE_SEGMENT_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be a non-empty cache segment containing only "
            "letters, numbers, underscores, and hyphens."
        )
    return value


class InMemoryJobQueue:
    """JobQueue implementation backed by process memory."""

    def __init__(self) -> None:
        self._queued: list[QueueMessage] = []
        self._leases: dict[str, JobLease] = {}
        self._attempts: dict[JobId, int] = {}
        self._cancelled_jobs: set[JobId] = set()
        self._terminal_failures: dict[JobId, str] = {}
        self._dead_letters: dict[JobId, str] = {}
        self._completed_jobs: set[JobId] = set()

    def enqueue(self, message: QueueMessage) -> None:
        routing = _validate_routing(message.routing)
        if self._is_terminal(message.job_id):
            return
        self._queued.append(QueueMessage(job_id=message.job_id, routing=routing))

    def claim(
        self,
        *,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
    ) -> JobLease | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        while self._queued:
            message = self._queued.pop(0)
            if self._is_terminal(message.job_id):
                continue
            attempt_number = self._attempts.get(message.job_id, 0) + 1
            self._attempts[message.job_id] = attempt_number
            lease = JobLease(
                job_id=message.job_id,
                lease_id=uuid.uuid4().hex,
                attempt_number=attempt_number,
                expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
                routing=dict(message.routing),
            )
            self._leases[lease.lease_id] = lease
            return lease
        return None

    def extend_lease(
        self,
        lease: JobLease,
        *,
        lease_seconds: int,
    ) -> JobLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        self._require_lease(lease)
        extended = JobLease(
            job_id=lease.job_id,
            lease_id=lease.lease_id,
            attempt_number=lease.attempt_number,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
            routing=dict(lease.routing),
        )
        self._leases[lease.lease_id] = extended
        return extended

    def complete(self, lease: JobLease) -> None:
        """Acknowledge success.  Removes the lease and marks the job terminal."""
        active = self._require_lease(lease)
        del self._leases[active.lease_id]
        self._completed_jobs.add(active.job_id)

    def retry(self, lease: JobLease, *, reason: str) -> None:
        active = self._require_lease(lease)
        del self._leases[active.lease_id]
        if not self._is_terminal(active.job_id):
            self._queued.append(
                QueueMessage(job_id=active.job_id, routing=dict(active.routing))
            )

    def mark_terminal_failure(self, lease: JobLease, *, reason: str) -> None:
        active = self._require_lease(lease)
        del self._leases[active.lease_id]
        self._terminal_failures[active.job_id] = reason

    def cancel(self, job_id: JobId, *, reason: str) -> None:
        self._cancelled_jobs.add(job_id)
        self._queued = [message for message in self._queued if message.job_id != job_id]
        for lease_id, lease in list(self._leases.items()):
            if lease.job_id == job_id:
                del self._leases[lease_id]

    def dead_letter(self, lease: JobLease, *, reason: str) -> None:
        active = self._require_lease(lease)
        del self._leases[active.lease_id]
        self._dead_letters[active.job_id] = reason

    def _require_lease(self, lease: JobLease) -> JobLease:
        active = self._leases.get(lease.lease_id)
        if active is None or active.job_id != lease.job_id:
            raise KeyError(f"active lease not found: {lease.lease_id}")
        return active

    def _is_terminal(self, job_id: JobId) -> bool:
        return (
            job_id in self._cancelled_jobs
            or job_id in self._terminal_failures
            or job_id in self._dead_letters
            or job_id in self._completed_jobs
        )


class InMemoryCacheStore:
    """CacheStore implementation with explicit user scope."""

    def __init__(self) -> None:
        self._entries: dict[tuple[UserId, str, str], CacheEntry] = {}

    def get(self, scope: CacheScope, key: str) -> CacheEntry | None:
        return self._entries.get(self._cache_key(scope, key))

    def put(self, scope: CacheScope, key: str, payload: Mapping[str, object]) -> CacheEntry:
        cache_key = self._cache_key(scope, key)
        entry = CacheEntry(scope=scope, key=cache_key[2], payload=dict(payload))
        self._entries[cache_key] = entry
        return entry

    def delete(self, scope: CacheScope, key: str) -> bool:
        return self._entries.pop(self._cache_key(scope, key), None) is not None

    def _cache_key(self, scope: CacheScope, key: str) -> tuple[UserId, str, str]:
        scope_name = _validate_cache_segment("scope", scope.name)
        safe_key = _validate_cache_segment("cache_key", key)
        return (scope.user_id, scope_name, safe_key)


class InMemorySecretProvider:
    """SecretProvider implementation backed by an explicit secret mapping."""

    def __init__(self, secrets: Mapping[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def get_secret(self, name: str) -> SecretValue:
        if name not in self._secrets:
            raise KeyError(f"secret is not configured: {name}")
        return SecretValue(name=name, value=self._secrets[name])


class InMemoryProviderRegistry:
    """ProviderRegistry implementation for local tests."""

    def __init__(self, active_config: ProviderConfigSnapshot | None = None) -> None:
        self._active_config = active_config or ProviderConfigSnapshot(
            config_version_id=ProviderConfigVersionId.new(),
            status=ProviderConfigStatus.ACTIVE,
            provider_name="fake",
            model_roles={
                "article": "fake-text",
                "reconciliation": "fake-text",
                "transcription": "fake-transcribe",
                "translation": "fake-text",
            },
            prompt_versions={},
        )
        self._configs = {self._active_config.config_version_id: self._active_config}

    def get_active_config(self) -> ProviderConfigSnapshot:
        return self._active_config

    def get_config(
        self,
        config_version_id: ProviderConfigVersionId,
    ) -> ProviderConfigSnapshot | None:
        return self._configs.get(config_version_id)

    def model_for_role(
        self,
        config_version_id: ProviderConfigVersionId,
        role: str,
    ) -> str:
        config = self.get_config(config_version_id)
        if config is None:
            raise KeyError(f"provider config not found: {config_version_id}")
        return config.model_roles[role]


class InMemoryUsageLedger:
    """UsageLedger implementation backed by process memory."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def record_usage(self, record: UsageRecord) -> None:
        if record.cost_usd < 0:
            raise ValueError("usage cost_usd must be non-negative.")
        self._records.append(record)

    def list_run_usage(self, *, user_id: UserId, run_id: RunId) -> Sequence[UsageRecord]:
        return [
            record
            for record in self._records
            if record.user_id == user_id and record.run_id == run_id
        ]

    def total_run_cost_usd(self, *, user_id: UserId, run_id: RunId) -> float:
        return round(
            sum(
                record.cost_usd
                for record in self.list_run_usage(user_id=user_id, run_id=run_id)
            ),
            6,
        )


class InMemoryBudgetService:
    """BudgetService implementation backed by an in-memory usage ledger."""

    def __init__(
        self,
        *,
        usage_ledger: InMemoryUsageLedger | None = None,
        hard_cap_usd: float = 60.0,
    ) -> None:
        self.usage_ledger = usage_ledger or InMemoryUsageLedger()
        self.hard_cap_usd = float(hard_cap_usd)

    def check_run_budget(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        next_cost_usd: float,
    ) -> BudgetDecision:
        projected = self.usage_ledger.total_run_cost_usd(
            user_id=user_id,
            run_id=run_id,
        ) + max(0.0, float(next_cost_usd))
        blocked = projected > self.hard_cap_usd
        return BudgetDecision(
            allowed=not blocked,
            blocked=blocked,
            warning=False,
            projected_total_usd=round(projected, 6),
            hard_cap_usd=self.hard_cap_usd,
            reason="Hard cap exceeded." if blocked else "OK",
        )

    def record_actual_cost(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        cost_usd: float,
    ) -> None:
        self.usage_ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=user_id,
                run_id=run_id,
                job_id=JobId.new(),
                provider_name="budget",
                model_id="actual-cost",
                cost_usd=max(0.0, float(cost_usd)),
                usage={},
            )
        )


__all__ = [
    "InMemoryBudgetService",
    "InMemoryCacheStore",
    "InMemoryJobQueue",
    "InMemoryMetadataStore",
    "InMemoryObjectStore",
    "InMemoryProviderRegistry",
    "InMemorySecretProvider",
    "InMemoryUsageLedger",
]
