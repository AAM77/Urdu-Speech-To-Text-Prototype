"""PostgreSQL metadata adapter contract tests for user/auth/upload/run records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from urdu_pipeline.application.ports import (
    JobRecord,
    RunRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactStage,
    JobId,
    JobStatus,
    RunId,
    RunStatus,
    ServiceIdentityId,
    ServiceIdentityStatus,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.db.metadata import PostgresMetadataStore


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self._row: tuple[Any, ...] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.connection.executed.append((sql, params))
        statement = " ".join(sql.lower().split())
        if statement.startswith("insert into users"):
            self.connection.maybe_fail("users")
            assert params is not None
            self.connection.users[params[0]] = params
        elif statement.startswith("select user_id, username"):
            assert params is not None
            row = self.connection.users.get(params[0])
            self._row = row[:4] if row is not None else None
        elif statement.startswith("insert into service_identities"):
            self.connection.maybe_fail("service_identities")
            assert params is not None
            self.connection.service_identities[params[0]] = params
        elif statement.startswith("select service_identity_id"):
            assert params is not None
            row = self.connection.service_identities.get(params[0])
            self._row = row[:4] if row is not None else None
        elif statement.startswith("insert into uploads"):
            self.connection.maybe_fail("uploads")
            assert params is not None
            self.connection.uploads[params[1]] = params
        elif (
            statement.startswith("select user_id, upload_id, status")
            and "order by created_at, upload_id" in statement
        ):
            assert params is not None
            (user_id,) = params
            self._rows = [
                row for row in self.connection.uploads.values() if row[0] == user_id
            ]
        elif statement.startswith("select user_id, upload_id"):
            assert params is not None
            user_id, upload_id = params
            row = self.connection.uploads.get(upload_id)
            self._row = row if row is not None and row[0] == user_id else None
        elif statement.startswith("insert into runs"):
            self.connection.maybe_fail("runs")
            assert params is not None
            self.connection.runs[params[1]] = params
        elif statement.startswith("insert into jobs"):
            self.connection.maybe_fail("jobs")
            assert params is not None
            user_id, run_id, job_id, stage, status, created_at, queued_at = params
            self.connection.jobs[job_id] = {
                "user_id": user_id,
                "run_id": run_id,
                "job_id": job_id,
                "stage": stage,
                "status": status,
                "routing": {},
                "lease_owner_service_identity_id": None,
                "lease_id": None,
                "lease_expires_at": None,
                "created_at": created_at,
                "queued_at": queued_at,
                "started_at": None,
                "completed_at": None,
            }
        elif statement.startswith("select user_id, run_id, job_id"):
            assert params is not None
            user_id, job_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or job["user_id"] != user_id:
                self._row = None
            else:
                self._row = (
                    job["user_id"],
                    job["run_id"],
                    job["job_id"],
                    job["status"],
                    job["created_at"],
                )
        elif statement.startswith("update jobs") and "set status = 'claimed'" in statement:
            assert params is not None
            (
                worker_id,
                lease_id,
                expires_at,
                started_at,
                job_id,
                now,
            ) = params
            job = self.connection.jobs.get(job_id)
            if (
                job is None
                or job["status"] not in {"queued", "claimed", "running"}
                or (
                    job["status"] != "queued"
                    and job["lease_expires_at"] is not None
                    and job["lease_expires_at"] > now
                )
                or (
                    job["status"] != "queued"
                    and job["lease_expires_at"] is None
                )
            ):
                self._row = None
            else:
                job["status"] = "claimed"
                job["lease_owner_service_identity_id"] = worker_id
                job["lease_id"] = lease_id
                job["lease_expires_at"] = expires_at
                job["started_at"] = job["started_at"] or started_at
                self._row = (
                    job["user_id"],
                    job["run_id"],
                    job["job_id"],
                    job["lease_id"],
                    job["lease_expires_at"],
                    job["routing"],
                )
        elif statement.startswith("select coalesce(max(attempt_number)"):
            assert params is not None
            (job_id,) = params
            self._row = (self.connection.attempts_by_job.get(job_id, 0) + 1,)
        elif statement.startswith("insert into job_attempts"):
            assert params is not None
            (
                _job_attempt_id,
                job_id,
                user_id,
                run_id,
                attempt_number,
                status,
                worker_id,
                lease_id,
                started_at,
                created_at,
            ) = params
            self.connection.attempts_by_job[job_id] = attempt_number
            self.connection.job_attempts[(job_id, lease_id)] = {
                "user_id": user_id,
                "run_id": run_id,
                "attempt_number": attempt_number,
                "status": status,
                "worker_id": worker_id,
                "lease_id": lease_id,
                "started_at": started_at,
                "created_at": created_at,
                "completed_at": None,
                "error_message": None,
            }
        elif statement.startswith("update jobs") and "set lease_expires_at = %s" in statement:
            assert params is not None
            expires_at, job_id, lease_id, now = params
            job = self.connection.jobs.get(job_id)
            if (
                job is None
                or job["lease_id"] != lease_id
                or job["status"] not in {"claimed", "running"}
                or job["lease_expires_at"] <= now
            ):
                self._row = None
            else:
                job["lease_expires_at"] = expires_at
                self._row = (
                    job["job_id"],
                    job["lease_id"],
                    job["lease_expires_at"],
                    job["routing"],
                )
        elif statement.startswith("update jobs") and "set status = 'queued'" in statement:
            assert params is not None
            queued_at, job_id, lease_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or job["lease_id"] != lease_id:
                self._row = None
            else:
                job["status"] = "queued"
                job["lease_owner_service_identity_id"] = None
                job["lease_id"] = None
                job["lease_expires_at"] = None
                job["queued_at"] = queued_at
                self._row = (job["job_id"],)
        elif statement.startswith("update jobs") and "set status = 'failed'" in statement:
            assert params is not None
            completed_at, job_id, lease_id = params
            self._update_terminal_job(job_id, lease_id, "failed", completed_at)
        elif statement.startswith("update jobs") and "set status = 'dead_lettered'" in statement:
            assert params is not None
            completed_at, job_id, lease_id = params
            self._update_terminal_job(job_id, lease_id, "dead_lettered", completed_at)
        elif statement.startswith("update jobs") and "set status = 'cancelled'" in statement:
            assert params is not None
            completed_at, job_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or job["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "dead_lettered",
            }:
                self._row = None
            else:
                job["status"] = "cancelled"
                job["lease_owner_service_identity_id"] = None
                job["lease_id"] = None
                job["lease_expires_at"] = None
                job["completed_at"] = completed_at
                self._row = (job["job_id"],)
        elif statement.startswith("update job_attempts"):
            assert params is not None
            status, completed_at, reason, job_id, lease_id = params
            attempt = self.connection.job_attempts.get((job_id, lease_id))
            if attempt is not None:
                attempt["status"] = status
                attempt["completed_at"] = completed_at
                attempt["error_message"] = reason
        elif (
            statement.startswith("select user_id, run_id")
            and "order by created_at, run_id" in statement
        ):
            assert params is not None
            (user_id,) = params
            self._rows = [
                row for row in self.connection.runs.values() if row[0] == user_id
            ]
        elif statement.startswith("select user_id, run_id") and "where user_id = %s and run_id = %s" in statement:
            assert params is not None
            user_id, run_id = params
            row = self.connection.runs.get(run_id)
            self._row = row if row is not None and row[0] == user_id else None
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def _update_terminal_job(
        self,
        job_id: str,
        lease_id: str,
        status: str,
        completed_at: datetime,
    ) -> None:
        job = self.connection.jobs.get(job_id)
        if job is None or job["lease_id"] != lease_id:
            self._row = None
            return
        job["status"] = status
        job["lease_owner_service_identity_id"] = None
        job["lease_id"] = None
        job["lease_expires_at"] = None
        job["completed_at"] = completed_at
        self._row = (job["job_id"],)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.users: dict[str, tuple[Any, ...]] = {}
        self.service_identities: dict[str, tuple[Any, ...]] = {}
        self.uploads: dict[str, tuple[Any, ...]] = {}
        self.runs: dict[str, tuple[Any, ...]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.attempts_by_job: dict[str, int] = {}
        self.job_attempts: dict[tuple[str, str], dict[str, Any]] = {}
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_table: str | None = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def maybe_fail(self, table_name: str) -> None:
        if self.fail_on_table == table_name:
            raise RuntimeError(f"forced failure on {table_name}")


def test_postgres_metadata_store_creates_and_reads_user_and_service_identity():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    user = UserRecord(
        user_id=UserId.new(),
        username="madeel",
        status=UserStatus.ACTIVE,
        created_at=_utc(2026, 1, 1),
    )
    service = ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId.new(),
        name="processor",
        status=ServiceIdentityStatus.ACTIVE,
        created_at=_utc(2026, 1, 2),
    )

    store.create_user(user)
    store.create_service_identity(service)

    assert store.get_user(user.user_id) == user
    assert store.get_service_identity(service.service_identity_id) == service
    assert connection.commits == 2
    assert connection.rollbacks == 0


def test_postgres_metadata_store_enforces_upload_ownership_and_lists_by_user():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = UserRecord(UserId.new(), "owner", UserStatus.ACTIVE)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(owner)
    store.create_user(other)
    owner_upload = UploadRecord(
        user_id=owner.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.COMPLETED,
        original_filename="owner.mp3",
        created_at=_utc(2026, 1, 3),
    )
    other_upload = UploadRecord(
        user_id=other.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.COMPLETED,
        original_filename="other.mp3",
        created_at=_utc(2026, 1, 4),
    )

    store.create_upload(owner_upload)
    store.create_upload(other_upload)

    assert store.get_upload(user_id=owner.user_id, upload_id=owner_upload.upload_id) == owner_upload
    assert store.get_upload(user_id=owner.user_id, upload_id=other_upload.upload_id) is None
    assert store.list_uploads(user_id=owner.user_id) == [owner_upload]


def test_postgres_metadata_store_enforces_run_ownership_and_lists_by_user():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = UserRecord(UserId.new(), "owner", UserStatus.ACTIVE)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(owner)
    store.create_user(other)
    owner_run = RunRecord(
        user_id=owner.user_id,
        run_id=RunId.new(),
        status=RunStatus.RUNNING,
        created_at=_utc(2026, 1, 5),
    )
    other_run = RunRecord(
        user_id=other.user_id,
        run_id=RunId.new(),
        status=RunStatus.PENDING,
        created_at=_utc(2026, 1, 6),
    )

    store.create_run(owner_run)
    store.create_run(other_run)

    assert store.get_run(user_id=owner.user_id, run_id=owner_run.run_id) == owner_run
    assert store.get_run(user_id=owner.user_id, run_id=other_run.run_id) is None
    assert store.list_runs(user_id=owner.user_id) == [owner_run]


def test_postgres_metadata_store_rolls_back_failed_create():
    connection = FakeConnection()
    connection.fail_on_table = "users"
    store = PostgresMetadataStore(connection)
    user = UserRecord(UserId.new(), "rollback", UserStatus.ACTIVE)

    with pytest.raises(RuntimeError):
        store.create_user(user)

    assert store.get_user(user.user_id) is None
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_postgres_metadata_store_creates_and_reads_user_owned_job():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(other)
    job = JobRecord(
        user_id=owner.user_id,
        run_id=owner.run_id,
        job_id=JobId.new(),
        status=JobStatus.QUEUED,
        created_at=_utc(2026, 1, 7),
    )

    store.create_job(job, stage=ArtifactStage.TRANSLATOR)

    assert store.get_job(user_id=owner.user_id, job_id=job.job_id) == job
    assert store.get_job(user_id=other.user_id, job_id=job.job_id) is None
    assert connection.jobs[str(job.job_id)]["stage"] == "translator"


def test_postgres_metadata_store_claims_job_with_compare_and_set():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 8)

    first = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    second = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )

    assert first is not None
    assert first.job_id == job.job_id
    assert first.attempt_number == 1
    assert first.expires_at == now + timedelta(seconds=30)
    assert second is None
    assert store.get_job(user_id=owner.user_id, job_id=job.job_id).status == JobStatus.CLAIMED


def test_postgres_metadata_store_extends_and_expires_job_lease():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 9)
    lease = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert lease is not None

    extended = store.extend_job_lease(
        lease,
        lease_seconds=90,
        now=now + timedelta(seconds=10),
    )
    blocked = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=60),
    )
    reclaimed = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=120),
    )

    assert extended.expires_at == now + timedelta(seconds=100)
    assert blocked is None
    assert reclaimed is not None
    assert reclaimed.lease_id != lease.lease_id
    assert reclaimed.attempt_number == 2


def test_postgres_metadata_store_retries_job_after_active_lease():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 10)
    lease = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert lease is not None

    store.retry_job(lease, reason="temporary provider outage", now=now)
    retried = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )

    assert store.get_job(user_id=owner.user_id, job_id=job.job_id).status == JobStatus.CLAIMED
    assert retried is not None
    assert retried.attempt_number == 2


def test_postgres_metadata_store_cancels_terminal_fails_and_dead_letters_jobs():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    worker = _create_service(store)
    now = _utc(2026, 1, 11)

    cancelled = _create_job(store, owner)
    store.cancel_job(cancelled.job_id, reason="user requested cancellation", now=now)
    assert store.get_job(user_id=owner.user_id, job_id=cancelled.job_id).status == JobStatus.CANCELLED
    assert store.claim_job(
        job_id=cancelled.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    ) is None

    failed = _create_job(store, owner)
    failed_lease = store.claim_job(
        job_id=failed.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert failed_lease is not None
    store.mark_job_terminal_failure(failed_lease, reason="retry limit exceeded", now=now)
    assert store.get_job(user_id=owner.user_id, job_id=failed.job_id).status == JobStatus.FAILED

    dead_lettered = _create_job(store, owner)
    dead_letter_lease = store.claim_job(
        job_id=dead_lettered.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert dead_letter_lease is not None
    store.dead_letter_job(dead_letter_lease, reason="poison message", now=now)
    assert store.get_job(
        user_id=owner.user_id,
        job_id=dead_lettered.job_id,
    ).status == JobStatus.DEAD_LETTERED


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _create_user_and_run(store: PostgresMetadataStore) -> RunRecord:
    user = UserRecord(UserId.new(), f"user-{UserId.new()}", UserStatus.ACTIVE)
    store.create_user(user)
    run = RunRecord(
        user_id=user.user_id,
        run_id=RunId.new(),
        status=RunStatus.QUEUED,
        created_at=_utc(2026, 1, 6),
    )
    store.create_run(run)
    return run


def _create_service(store: PostgresMetadataStore) -> ServiceIdentityRecord:
    service = ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId.new(),
        name=f"worker-{ServiceIdentityId.new()}",
        status=ServiceIdentityStatus.ACTIVE,
    )
    store.create_service_identity(service)
    return service


def _create_job(store: PostgresMetadataStore, run: RunRecord) -> JobRecord:
    job = JobRecord(
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=JobId.new(),
        status=JobStatus.QUEUED,
        created_at=_utc(2026, 1, 7),
    )
    store.create_job(job, stage=ArtifactStage.CHUNKER)
    return job
