"""PostgreSQL metadata store adapter."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    JobRecord,
    RunRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    JobId,
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

    def create_job(self, record: JobRecord) -> None:
        raise NotImplementedError("Job metadata is implemented in step 3.2.2.")

    def get_job(self, *, user_id: UserId, job_id: JobId) -> JobRecord | None:
        raise NotImplementedError("Job metadata is implemented in step 3.2.2.")

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
