"""PostgreSQL metadata store adapter."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    JobLease,
    JobRecord,
    RunRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    JobId,
    JobStatus,
    ProviderConfigVersionId,
    RunId,
    RunStatus,
    ServiceIdentityId,
    ServiceIdentityStatus,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)


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

    def record_artifact(self, record: ArtifactRecord) -> None:
        raise NotImplementedError("Artifact metadata is implemented in step 3.2.3.")

    def get_artifact(
        self,
        *,
        user_id: UserId,
        artifact_id: ArtifactId,
    ) -> ArtifactRecord | None:
        raise NotImplementedError("Artifact metadata is implemented in step 3.2.3.")

    def _require_user(self, user_id: UserId) -> None:
        if self.get_user(user_id) is None:
            raise ValueError(f"user does not exist: {user_id}")

    def _require_run_owner(self, *, user_id: UserId, run_id: RunId) -> None:
        if self.get_run(user_id=user_id, run_id=run_id) is None:
            raise ValueError(f"run does not exist for user: {run_id}")

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
