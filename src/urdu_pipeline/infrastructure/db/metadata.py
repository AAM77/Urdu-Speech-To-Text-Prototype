"""PostgreSQL metadata store adapter."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    CacheEntry,
    CacheScope,
    JobLease,
    JobRecord,
    ProviderConfigSnapshot,
    RunRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
    UsageRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    CleanupTaskId,
    CleanupTaskStatus,
    JobId,
    JobStatus,
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ProviderRunId,
    RunId,
    RunStatus,
    ServiceIdentityId,
    ServiceIdentityStatus,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)


@dataclass(frozen=True)
class ArtifactDocumentChunkRecord:
    artifact_id: ArtifactId
    chunk_index: int
    user_id: UserId
    run_id: RunId
    text_content: str
    token_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(frozen=True)
class StageEventRecord:
    user_id: UserId
    run_id: RunId
    job_id: JobId
    stage: ArtifactStage
    event_type: str
    severity: str = "info"
    message: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(frozen=True)
class PromptVersionRecord:
    prompt_version_id: str
    prompt_id: str
    prompt_version: str
    stage_name: ArtifactStage
    body: str
    checksum_sha256: str
    is_active: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(frozen=True)
class CleanupTaskRecord:
    cleanup_task_id: CleanupTaskId
    user_id: UserId | None
    run_id: RunId | None
    task_type: str
    status: CleanupTaskStatus
    run_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class PostgresMetadataStore:
    """MetadataStore implementation backed by a PostgreSQL connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_user(self, record: UserRecord) -> None:
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO users (user_id, username, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    record.username,
                    record.status.value,
                    record.created_at,
                    record.created_at,
                ),
            )
        )

    def get_user(self, user_id: UserId) -> UserRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, username, status, created_at
                FROM users
                WHERE user_id = %s
                """,
                (str(user_id),),
            )
            row = cursor.fetchone()
        return _user_from_row(row)

    def create_service_identity(self, record: ServiceIdentityRecord) -> None:
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO service_identities (
                    service_identity_id,
                    name,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(record.service_identity_id),
                    record.name,
                    record.status.value,
                    record.created_at,
                    record.created_at,
                ),
            )
        )

    def get_service_identity(
        self,
        service_identity_id: ServiceIdentityId,
    ) -> ServiceIdentityRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT service_identity_id, name, status, created_at
                FROM service_identities
                WHERE service_identity_id = %s
                """,
                (str(service_identity_id),),
            )
            row = cursor.fetchone()
        return _service_identity_from_row(row)

    def create_upload(self, record: UploadRecord) -> None:
        self._require_user(record.user_id)
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO uploads (
                    user_id,
                    upload_id,
                    status,
                    original_filename,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    str(record.upload_id),
                    record.status.value,
                    record.original_filename,
                    record.created_at,
                ),
            )
        )

    def get_upload(self, *, user_id: UserId, upload_id: UploadId) -> UploadRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, upload_id, status, original_filename, created_at
                FROM uploads
                WHERE user_id = %s AND upload_id = %s
                """,
                (str(user_id), str(upload_id)),
            )
            row = cursor.fetchone()
        return _upload_from_row(row)

    def list_uploads(self, *, user_id: UserId) -> Sequence[UploadRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, upload_id, status, original_filename, created_at
                FROM uploads
                WHERE user_id = %s
                ORDER BY created_at, upload_id
                """,
                (str(user_id),),
            )
            rows = cursor.fetchall()
        return [_upload_from_row(row) for row in rows]

    def create_run(self, record: RunRecord) -> None:
        self._require_user(record.user_id)
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO runs (
                    user_id,
                    run_id,
                    status,
                    provider_config_version_id,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    str(record.run_id),
                    record.status.value,
                    str(record.provider_config_version_id)
                    if record.provider_config_version_id is not None
                    else None,
                    record.created_at,
                ),
            )
        )

    def get_run(self, *, user_id: UserId, run_id: RunId) -> RunRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, run_id, status, provider_config_version_id, created_at
                FROM runs
                WHERE user_id = %s AND run_id = %s
                """,
                (str(user_id), str(run_id)),
            )
            row = cursor.fetchone()
        return _run_from_row(row)

    def list_runs(self, *, user_id: UserId) -> Sequence[RunRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, run_id, status, provider_config_version_id, created_at
                FROM runs
                WHERE user_id = %s
                ORDER BY created_at, run_id
                """,
                (str(user_id),),
            )
            rows = cursor.fetchall()
        return [_run_from_row(row) for row in rows]

    def create_job(
        self,
        record: JobRecord,
        *,
        stage: ArtifactStage = ArtifactStage.CHUNKER,
    ) -> None:
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO jobs (
                    user_id,
                    run_id,
                    job_id,
                    stage,
                    status,
                    created_at,
                    queued_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    str(record.run_id),
                    str(record.job_id),
                    stage.value,
                    record.status.value,
                    record.created_at,
                    record.created_at,
                ),
            )
        )

    def get_job(self, *, user_id: UserId, job_id: JobId) -> JobRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, run_id, job_id, status, created_at
                FROM jobs
                WHERE user_id = %s AND job_id = %s
                """,
                (str(user_id), str(job_id)),
            )
            row = cursor.fetchone()
        return _job_from_row(row)

    def claim_job(
        self,
        *,
        job_id: JobId,
        worker_id: ServiceIdentityId,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> JobLease | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        claimed_at = _coerce_now(now)
        lease_id = uuid.uuid4().hex
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        row: tuple[Any, ...] | None = None
        attempt_number: int | None = None
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'claimed',
                        lease_owner_service_identity_id = %s,
                        lease_id = %s,
                        lease_expires_at = %s,
                        started_at = COALESCE(started_at, %s)
                    WHERE job_id = %s
                      AND status IN ('queued', 'claimed', 'running')
                      AND (
                        status = 'queued'
                        OR lease_expires_at <= %s
                      )
                    RETURNING user_id, run_id, job_id, lease_id, lease_expires_at, routing
                    """,
                    (
                        str(worker_id),
                        lease_id,
                        expires_at,
                        claimed_at,
                        str(job_id),
                        claimed_at,
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    cursor.execute(
                        """
                        SELECT COALESCE(MAX(attempt_number), 0) + 1
                        FROM job_attempts
                        WHERE job_id = %s
                        """,
                        (str(job_id),),
                    )
                    attempt_number = int(cursor.fetchone()[0])
                    cursor.execute(
                        """
                        INSERT INTO job_attempts (
                            job_attempt_id,
                            job_id,
                            user_id,
                            run_id,
                            attempt_number,
                            status,
                            worker_service_identity_id,
                            lease_id,
                            started_at,
                            created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            uuid.uuid4().hex,
                            str(job_id),
                            row[0],
                            row[1],
                            attempt_number,
                            JobStatus.RUNNING.value,
                            str(worker_id),
                            lease_id,
                            claimed_at,
                            claimed_at,
                        ),
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        if row is None or attempt_number is None:
            return None
        return _lease_from_row(row, attempt_number=attempt_number)

    def extend_job_lease(
        self,
        lease: JobLease,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> JobLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        extended_at = _coerce_now(now)
        expires_at = extended_at + timedelta(seconds=lease_seconds)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET lease_expires_at = %s
                    WHERE job_id = %s
                      AND lease_id = %s
                      AND status IN ('claimed', 'running')
                      AND lease_expires_at > %s
                    RETURNING job_id, lease_id, lease_expires_at, routing
                    """,
                    (expires_at, str(lease.job_id), lease.lease_id, extended_at),
                )
                row = cursor.fetchone()
            if row is None:
                self.connection.rollback()
                raise ValueError(f"active lease not found for job: {lease.job_id}")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        return _lease_from_row(row, attempt_number=lease.attempt_number)

    def retry_job(
        self,
        lease: JobLease,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        retried_at = _coerce_now(now)
        self._transition_active_lease_to_queue(
            lease,
            reason=reason,
            now=retried_at,
        )

    def mark_job_terminal_failure(
        self,
        lease: JobLease,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        self._transition_active_lease_to_terminal(
            lease,
            status=JobStatus.FAILED,
            reason=reason,
            now=_coerce_now(now),
        )

    def cancel_job(
        self,
        job_id: JobId,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        del reason
        cancelled_at = _coerce_now(now)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled',
                        lease_owner_service_identity_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        completed_at = %s
                    WHERE job_id = %s
                      AND status NOT IN ('succeeded', 'failed', 'cancelled', 'dead_lettered')
                    RETURNING job_id
                    """,
                    (cancelled_at, str(job_id)),
                )
                row = cursor.fetchone()
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return row is not None

    def dead_letter_job(
        self,
        lease: JobLease,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        self._transition_active_lease_to_terminal(
            lease,
            status=JobStatus.DEAD_LETTERED,
            reason=reason,
            now=_coerce_now(now),
        )

    def record_artifact(
        self,
        record: ArtifactRecord,
        *,
        job_id: JobId | None = None,
        object_key: str | None = None,
    ) -> None:
        self._require_run_owner(user_id=record.user_id, run_id=record.run_id)
        safe_job_id = job_id
        if safe_job_id is None:
            raise ValueError("record_artifact requires job_id.")
        if self.get_job(user_id=record.user_id, job_id=safe_job_id) is None:
            raise ValueError(f"job does not exist for user: {safe_job_id}")
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO artifacts (
                    user_id,
                    run_id,
                    job_id,
                    artifact_id,
                    stage,
                    artifact_type,
                    object_key,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    str(record.run_id),
                    str(safe_job_id),
                    str(record.artifact_id),
                    record.stage.value,
                    record.artifact_type.value,
                    object_key or str(record.artifact_id),
                    record.created_at,
                ),
            )
        )

    def get_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, run_id, artifact_id, stage, artifact_type, created_at
                FROM artifacts
                WHERE user_id = %s AND artifact_id = %s
                """,
                (str(user_id), str(artifact_id)),
            )
            row = cursor.fetchone()
        return _artifact_from_row(row)

    def put_artifact_document_chunk(
        self,
        record: ArtifactDocumentChunkRecord,
    ) -> None:
        if len(record.text_content.encode("utf-8")) >= 256 * 1024:
            raise ValueError("artifact document chunk must be below 256 KB.")
        if self.get_artifact(user_id=record.user_id, artifact_id=record.artifact_id) is None:
            raise ValueError(f"artifact does not exist for user: {record.artifact_id}")
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO artifact_document_chunks (
                    artifact_id,
                    chunk_index,
                    user_id,
                    run_id,
                    text_content,
                    token_count,
                    metadata,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (artifact_id, chunk_index) DO UPDATE
                SET text_content = EXCLUDED.text_content,
                    token_count = EXCLUDED.token_count,
                    metadata = EXCLUDED.metadata
                """,
                (
                    str(record.artifact_id),
                    record.chunk_index,
                    str(record.user_id),
                    str(record.run_id),
                    record.text_content,
                    record.token_count,
                    dict(record.metadata),
                    record.created_at,
                ),
            )
        )

    def list_artifact_document_chunks(
        self,
        *,
        artifact_id: ArtifactId,
    ) -> Sequence[ArtifactDocumentChunkRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT artifact_id,
                       chunk_index,
                       user_id,
                       run_id,
                       text_content,
                       token_count,
                       metadata,
                       created_at
                FROM artifact_document_chunks
                WHERE artifact_id = %s
                ORDER BY chunk_index
                """,
                (str(artifact_id),),
            )
            rows = cursor.fetchall()
        return [_artifact_document_chunk_from_row(row) for row in rows]

    def record_stage_event(self, record: StageEventRecord) -> None:
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO stage_events (
                    stage_event_id,
                    user_id,
                    run_id,
                    job_id,
                    stage,
                    event_type,
                    severity,
                    message,
                    payload,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4().hex,
                    str(record.user_id),
                    str(record.run_id),
                    str(record.job_id),
                    record.stage.value,
                    record.event_type,
                    record.severity,
                    record.message,
                    dict(record.payload),
                    record.created_at,
                ),
            )
        )

    def list_stage_events(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[StageEventRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stage_event_id,
                       user_id,
                       run_id,
                       job_id,
                       stage,
                       event_type,
                       severity,
                       message,
                       payload,
                       created_at
                FROM stage_events
                WHERE user_id = %s AND run_id = %s
                ORDER BY created_at, stage_event_id
                """,
                (str(user_id), str(run_id)),
            )
            rows = cursor.fetchall()
        return [_stage_event_from_row(row) for row in rows]

    def save_provider_config(self, snapshot: ProviderConfigSnapshot) -> None:
        self._write(
            lambda cursor: self._save_provider_config(cursor, snapshot)
        )

    def get_active_config(self) -> ProviderConfigSnapshot:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT config_version_id, status, provider_name, created_at
                FROM provider_config_versions
                WHERE status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("no active provider config is available.")
            return self._provider_config_from_row(cursor, row)

    def get_config(
        self,
        config_version_id: ProviderConfigVersionId,
    ) -> ProviderConfigSnapshot | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT config_version_id, status, provider_name, created_at
                FROM provider_config_versions
                WHERE config_version_id = %s
                """,
                (str(config_version_id),),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._provider_config_from_row(cursor, row)

    def model_for_role(self, config_version_id: ProviderConfigVersionId, role: str) -> str:
        snapshot = self.get_config(config_version_id)
        if snapshot is None:
            raise KeyError(f"provider config not found: {config_version_id}")
        return snapshot.model_roles[role]

    def upsert_prompt_version(self, record: PromptVersionRecord) -> None:
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO prompt_versions (
                    prompt_version_id,
                    prompt_id,
                    prompt_version,
                    stage_name,
                    body,
                    checksum_sha256,
                    is_active,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (prompt_id, prompt_version) DO UPDATE
                SET body = EXCLUDED.body,
                    checksum_sha256 = EXCLUDED.checksum_sha256,
                    is_active = EXCLUDED.is_active
                """,
                (
                    record.prompt_version_id,
                    record.prompt_id,
                    record.prompt_version,
                    record.stage_name.value,
                    record.body,
                    record.checksum_sha256,
                    record.is_active,
                    record.created_at,
                ),
            )
        )

    def get_prompt_version(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
    ) -> PromptVersionRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT prompt_version_id,
                       prompt_id,
                       prompt_version,
                       stage_name,
                       body,
                       checksum_sha256,
                       is_active,
                       created_at
                FROM prompt_versions
                WHERE prompt_id = %s AND prompt_version = %s
                """,
                (prompt_id, prompt_version),
            )
            row = cursor.fetchone()
        return _prompt_version_from_row(row)

    def reserve_usage(self, record: UsageRecord) -> None:
        usage = dict(record.usage)
        usage["kind"] = "reservation"
        self._record_usage_with_payload(record, usage=usage)

    def release_usage(self, provider_run_id: ProviderRunId) -> None:
        self._write(
            lambda cursor: cursor.execute(
                """
                UPDATE usage_ledger
                SET cost_usd = 0,
                    usage = %s
                WHERE provider_run_id = %s
                """,
                ({"kind": "released"}, str(provider_run_id)),
            )
        )

    def record_usage(self, record: UsageRecord) -> None:
        usage = dict(record.usage)
        usage.setdefault("kind", "actual")
        self._record_usage_with_payload(record, usage=usage)

    def list_run_usage(self, *, user_id: UserId, run_id: RunId) -> Sequence[UsageRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT provider_run_id,
                       user_id,
                       run_id,
                       job_id,
                       provider_name,
                       model_id,
                       cost_usd,
                       usage,
                       created_at
                FROM usage_ledger
                WHERE user_id = %s AND run_id = %s
                ORDER BY created_at, provider_run_id
                """,
                (str(user_id), str(run_id)),
            )
            rows = cursor.fetchall()
        return [_usage_from_row(row) for row in rows]

    def total_run_cost_usd(self, *, user_id: UserId, run_id: RunId) -> float:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM usage_ledger
                WHERE user_id = %s
                  AND run_id = %s
                  AND COALESCE(usage ->> 'kind', 'actual') <> 'reservation'
                """,
                (str(user_id), str(run_id)),
            )
            row = cursor.fetchone()
        return float(row[0] or 0.0)

    def total_reserved_cost_usd(self, *, user_id: UserId, run_id: RunId) -> float:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM usage_ledger
                WHERE user_id = %s
                  AND run_id = %s
                  AND usage ->> 'kind' = 'reservation'
                """,
                (str(user_id), str(run_id)),
            )
            row = cursor.fetchone()
        return float(row[0] or 0.0)

    def get(self, scope: CacheScope, key: str) -> CacheEntry | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT scope_name, cache_key, payload, created_at
                FROM cache_entries
                WHERE user_id = %s AND scope_name = %s AND cache_key = %s
                """,
                (str(scope.user_id), scope.name, key),
            )
            row = cursor.fetchone()
        return _cache_entry_from_row(scope, row)

    def put(self, scope: CacheScope, key: str, payload: Mapping[str, Any]) -> CacheEntry:
        now = datetime.now(tz=timezone.utc)
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO cache_entries (
                    user_id,
                    scope_name,
                    cache_key,
                    payload,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, scope_name, cache_key) DO UPDATE
                SET payload = EXCLUDED.payload
                """,
                (str(scope.user_id), scope.name, key, dict(payload), now),
            )
        )
        entry = self.get(scope, key)
        if entry is None:
            raise RuntimeError("cache entry was not persisted")
        return entry

    def delete(self, scope: CacheScope, key: str) -> bool:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM cache_entries
                    WHERE user_id = %s AND scope_name = %s AND cache_key = %s
                    """,
                    (str(scope.user_id), scope.name, key),
                )
                deleted = getattr(cursor, "rowcount", 0) > 0
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return deleted

    def create_cleanup_task_once(self, record: CleanupTaskRecord) -> CleanupTaskRecord:
        self._write(
            lambda cursor: cursor.execute(
                """
                INSERT INTO cleanup_tasks (
                    cleanup_task_id,
                    user_id,
                    run_id,
                    task_type,
                    status,
                    run_at,
                    payload,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cleanup_task_id) DO NOTHING
                """,
                (
                    str(record.cleanup_task_id),
                    str(record.user_id) if record.user_id is not None else None,
                    str(record.run_id) if record.run_id is not None else None,
                    record.task_type,
                    record.status.value,
                    record.run_at,
                    dict(record.payload),
                    record.created_at,
                ),
            )
        )
        stored = self.get_cleanup_task(record.cleanup_task_id)
        if stored is None:
            raise RuntimeError("cleanup task was not persisted")
        return stored

    def get_cleanup_task(
        self,
        cleanup_task_id: CleanupTaskId,
    ) -> CleanupTaskRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT cleanup_task_id,
                       user_id,
                       run_id,
                       task_type,
                       status,
                       run_at,
                       payload,
                       created_at
                FROM cleanup_tasks
                WHERE cleanup_task_id = %s
                """,
                (str(cleanup_task_id),),
            )
            row = cursor.fetchone()
        return _cleanup_task_from_row(row)

    def _require_user(self, user_id: UserId) -> None:
        if self.get_user(user_id) is None:
            raise ValueError(f"user does not exist: {user_id}")

    def _require_run_owner(self, *, user_id: UserId, run_id: RunId) -> None:
        if self.get_run(user_id=user_id, run_id=run_id) is None:
            raise ValueError(f"run does not exist for user: {run_id}")

    def _save_provider_config(
        self,
        cursor: Any,
        snapshot: ProviderConfigSnapshot,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO provider_config_versions (
                config_version_id,
                status,
                provider_name,
                created_at
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                str(snapshot.config_version_id),
                snapshot.status.value,
                snapshot.provider_name,
                datetime.now(tz=timezone.utc),
            ),
        )
        for role, model_id in snapshot.model_roles.items():
            cursor.execute(
                """
                INSERT INTO provider_config_entries (
                    config_version_id,
                    role,
                    model_id,
                    prompt_version
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(snapshot.config_version_id),
                    role,
                    model_id,
                    snapshot.prompt_versions.get(role),
                ),
            )

    def _provider_config_from_row(
        self,
        cursor: Any,
        row: tuple[Any, ...],
    ) -> ProviderConfigSnapshot:
        config_version_id, status, provider_name, _created_at = row
        cursor.execute(
            """
            SELECT role, model_id, prompt_version
            FROM provider_config_entries
            WHERE config_version_id = %s
            ORDER BY role
            """,
            (str(config_version_id),),
        )
        entries = cursor.fetchall()
        return ProviderConfigSnapshot(
            config_version_id=ProviderConfigVersionId(str(config_version_id)),
            status=ProviderConfigStatus(status),
            provider_name=provider_name,
            model_roles={role: model_id for role, model_id, _prompt in entries},
            prompt_versions={
                role: prompt
                for role, _model_id, prompt in entries
                if prompt is not None
            },
        )

    def _record_usage_with_payload(
        self,
        record: UsageRecord,
        *,
        usage: Mapping[str, Any],
    ) -> None:
        self._write(lambda cursor: self._insert_usage(cursor, record, usage))

    def _insert_usage(
        self,
        cursor: Any,
        record: UsageRecord,
        usage: Mapping[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO provider_runs (
                provider_run_id,
                user_id,
                run_id,
                job_id,
                provider_name,
                model_id,
                started_at,
                raw_usage
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_run_id) DO NOTHING
            """,
            (
                str(record.provider_run_id),
                str(record.user_id),
                str(record.run_id),
                str(record.job_id),
                record.provider_name,
                record.model_id,
                record.created_at,
                dict(record.usage),
            ),
        )
        cursor.execute(
            """
            INSERT INTO usage_ledger (
                usage_ledger_id,
                provider_run_id,
                user_id,
                run_id,
                job_id,
                provider_name,
                model_id,
                cost_usd,
                usage,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4().hex,
                str(record.provider_run_id),
                str(record.user_id),
                str(record.run_id),
                str(record.job_id),
                record.provider_name,
                record.model_id,
                record.cost_usd,
                dict(usage),
                record.created_at,
            ),
        )

    def _transition_active_lease_to_queue(
        self,
        lease: JobLease,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued',
                        lease_owner_service_identity_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        queued_at = %s
                    WHERE job_id = %s
                      AND lease_id = %s
                      AND status IN ('claimed', 'running')
                    RETURNING job_id
                    """,
                    (now, str(lease.job_id), lease.lease_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"active lease not found for job: {lease.job_id}")
                _record_attempt_completion(
                    cursor,
                    lease=lease,
                    status=JobStatus.FAILED,
                    completed_at=now,
                    reason=reason,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _transition_active_lease_to_terminal(
        self,
        lease: JobLease,
        *,
        status: JobStatus,
        reason: str,
        now: datetime,
    ) -> None:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE jobs
                    SET status = '{status.value}',
                        lease_owner_service_identity_id = NULL,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        completed_at = %s
                    WHERE job_id = %s
                      AND lease_id = %s
                      AND status IN ('claimed', 'running')
                    RETURNING job_id
                    """,
                    (now, str(lease.job_id), lease.lease_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"active lease not found for job: {lease.job_id}")
                _record_attempt_completion(
                    cursor,
                    lease=lease,
                    status=JobStatus.FAILED,
                    completed_at=now,
                    reason=reason,
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _write(self, operation: Callable[[Any], None]) -> None:
        try:
            with self.connection.cursor() as cursor:
                operation(cursor)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


def _user_from_row(row: tuple[Any, ...] | None) -> UserRecord | None:
    if row is None:
        return None
    user_id, username, status, created_at = row
    return UserRecord(
        user_id=UserId(str(user_id)),
        username=username,
        status=UserStatus(status),
        created_at=created_at,
    )


def _service_identity_from_row(
    row: tuple[Any, ...] | None,
) -> ServiceIdentityRecord | None:
    if row is None:
        return None
    service_identity_id, name, status, created_at = row
    return ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId(str(service_identity_id)),
        name=name,
        status=ServiceIdentityStatus(status),
        created_at=created_at,
    )


def _upload_from_row(row: tuple[Any, ...] | None) -> UploadRecord | None:
    if row is None:
        return None
    user_id, upload_id, status, original_filename, created_at = row
    return UploadRecord(
        user_id=UserId(str(user_id)),
        upload_id=UploadId(str(upload_id)),
        status=UploadStatus(status),
        original_filename=original_filename,
        created_at=created_at,
    )


def _run_from_row(row: tuple[Any, ...] | None) -> RunRecord | None:
    if row is None:
        return None
    user_id, run_id, status, provider_config_version_id, created_at = row
    return RunRecord(
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        status=RunStatus(status),
        provider_config_version_id=(
            ProviderConfigVersionId(str(provider_config_version_id))
            if provider_config_version_id is not None
            else None
        ),
        created_at=created_at,
    )


def _job_from_row(row: tuple[Any, ...] | None) -> JobRecord | None:
    if row is None:
        return None
    user_id, run_id, job_id, status, created_at = row
    return JobRecord(
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        job_id=JobId(str(job_id)),
        status=JobStatus(status),
        created_at=created_at,
    )


def _artifact_from_row(row: tuple[Any, ...] | None) -> ArtifactRecord | None:
    if row is None:
        return None
    user_id, run_id, artifact_id, stage, artifact_type, created_at = row
    return ArtifactRecord(
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        artifact_id=ArtifactId(str(artifact_id)),
        stage=ArtifactStage(stage),
        artifact_type=ArtifactType(artifact_type),
        created_at=created_at,
    )


def _artifact_document_chunk_from_row(
    row: tuple[Any, ...],
) -> ArtifactDocumentChunkRecord:
    artifact_id, chunk_index, user_id, run_id, text_content, token_count, metadata, created_at = row
    return ArtifactDocumentChunkRecord(
        artifact_id=ArtifactId(str(artifact_id)),
        chunk_index=int(chunk_index),
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        text_content=text_content,
        token_count=token_count,
        metadata=dict(metadata),
        created_at=created_at,
    )


def _stage_event_from_row(row: tuple[Any, ...]) -> StageEventRecord:
    (
        _stage_event_id,
        user_id,
        run_id,
        job_id,
        stage,
        event_type,
        severity,
        message,
        payload,
        created_at,
    ) = row
    return StageEventRecord(
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        job_id=JobId(str(job_id)),
        stage=ArtifactStage(stage),
        event_type=event_type,
        severity=severity,
        message=message,
        payload=dict(payload),
        created_at=created_at,
    )


def _prompt_version_from_row(row: tuple[Any, ...] | None) -> PromptVersionRecord | None:
    if row is None:
        return None
    (
        prompt_version_id,
        prompt_id,
        prompt_version,
        stage_name,
        body,
        checksum_sha256,
        is_active,
        created_at,
    ) = row
    return PromptVersionRecord(
        prompt_version_id=prompt_version_id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        stage_name=ArtifactStage(stage_name),
        body=body,
        checksum_sha256=checksum_sha256,
        is_active=bool(is_active),
        created_at=created_at,
    )


def _usage_from_row(row: tuple[Any, ...]) -> UsageRecord:
    provider_run_id, user_id, run_id, job_id, provider_name, model_id, cost_usd, usage, created_at = row
    return UsageRecord(
        provider_run_id=ProviderRunId(str(provider_run_id)),
        user_id=UserId(str(user_id)),
        run_id=RunId(str(run_id)),
        job_id=JobId(str(job_id)),
        provider_name=provider_name,
        model_id=model_id,
        cost_usd=float(cost_usd),
        usage={key: value for key, value in dict(usage).items() if key != "kind"},
        created_at=created_at,
    )


def _cache_entry_from_row(scope: CacheScope, row: tuple[Any, ...] | None) -> CacheEntry | None:
    if row is None:
        return None
    _scope_name, cache_key, payload, created_at = row
    return CacheEntry(
        scope=scope,
        key=cache_key,
        payload=dict(payload),
        created_at=created_at,
    )


def _cleanup_task_from_row(row: tuple[Any, ...] | None) -> CleanupTaskRecord | None:
    if row is None:
        return None
    cleanup_task_id, user_id, run_id, task_type, status, run_at, payload, created_at = row
    return CleanupTaskRecord(
        cleanup_task_id=CleanupTaskId(str(cleanup_task_id)),
        user_id=UserId(str(user_id)) if user_id is not None else None,
        run_id=RunId(str(run_id)) if run_id is not None else None,
        task_type=task_type,
        status=CleanupTaskStatus(status),
        run_at=run_at,
        payload=dict(payload),
        created_at=created_at,
    )


def _lease_from_row(
    row: tuple[Any, ...],
    *,
    attempt_number: int,
) -> JobLease:
    job_id = row[2] if len(row) == 6 else row[0]
    lease_id = row[3] if len(row) == 6 else row[1]
    expires_at = row[4] if len(row) == 6 else row[2]
    routing = row[5] if len(row) == 6 else row[3]
    return JobLease(
        job_id=JobId(str(job_id)),
        lease_id=str(lease_id),
        attempt_number=attempt_number,
        expires_at=expires_at,
        routing=_string_routing(routing),
    )


def _record_attempt_completion(
    cursor: Any,
    *,
    lease: JobLease,
    status: JobStatus,
    completed_at: datetime,
    reason: str,
) -> None:
    cursor.execute(
        """
        UPDATE job_attempts
        SET status = %s,
            completed_at = %s,
            error_message = %s
        WHERE job_id = %s
          AND lease_id = %s
        """,
        (
            status.value,
            completed_at,
            reason,
            str(lease.job_id),
            lease.lease_id,
        ),
    )


def _coerce_now(now: datetime | None) -> datetime:
    return now or datetime.now(tz=timezone.utc)


def _string_routing(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}
