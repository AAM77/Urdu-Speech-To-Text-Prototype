"""PostgreSQL metadata adapter contract tests for user/auth/upload/run records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from urdu_pipeline.application.ports import (
    RunRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
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


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)
