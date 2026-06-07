"""Cleanup scheduler contracts for Stage 7.1.2."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from urdu_pipeline.application.ports import (
    BearerTokenRecord,
    ObjectMetadata,
    SessionRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    JobStatus,
    RunId,
    RunStatus,
    SessionId,
    TokenId,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.application.ports import RunRecord
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryMetadataStore,
    InMemoryObjectStore,
)


NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)


def _config():
    from urdu_pipeline.processor.cleanup_scheduler import CleanupSchedulerConfig

    return CleanupSchedulerConfig(
        upload_ttl=timedelta(hours=1),
        run_tmp_retention=timedelta(hours=1),
        session_retention=timedelta(hours=1),
        token_retention=timedelta(hours=1),
        retry_delay=timedelta(minutes=5),
    )


def _user(store: InMemoryMetadataStore) -> UserRecord:
    user = UserRecord(
        user_id=UserId.new(),
        username=f"user-{UserId.new()}",
        status=UserStatus.ACTIVE,
    )
    store.create_user(user)
    return user


def _upload(
    store: InMemoryMetadataStore,
    user: UserRecord,
    *,
    status: UploadStatus,
    created_at: datetime,
    multipart_upload_id: str | None = None,
) -> UploadRecord:
    record = UploadRecord(
        user_id=user.user_id,
        upload_id=UploadId.new(),
        status=status,
        original_filename="audio.wav",
        content_type="audio/wav",
        size_bytes=44,
        multipart_upload_id=multipart_upload_id,
        created_at=created_at,
    )
    store.create_upload(record)
    return record


def _run(
    store: InMemoryMetadataStore,
    user: UserRecord,
    *,
    status: RunStatus,
    created_at: datetime,
) -> RunRecord:
    run = RunRecord(
        user_id=user.user_id,
        run_id=RunId.new(),
        status=status,
        created_at=created_at,
    )
    store.create_run(run)
    return run


def _object_key(upload_id: UploadId) -> str:
    return f"uploads/{upload_id}"


def test_schedule_cleanup_tasks_creates_expired_upload_tasks_once():
    from urdu_pipeline.processor.cleanup_scheduler import schedule_cleanup_tasks

    store = InMemoryMetadataStore()
    user = _user(store)
    old_upload = _upload(
        store,
        user,
        status=UploadStatus.INITIALIZED,
        created_at=NOW - timedelta(hours=2),
    )
    _upload(
        store,
        user,
        status=UploadStatus.INITIALIZED,
        created_at=NOW - timedelta(minutes=15),
    )

    first = schedule_cleanup_tasks(store, now=NOW, config=_config())
    second = schedule_cleanup_tasks(store, now=NOW, config=_config())

    assert first.scheduled_count == 1
    assert second.scheduled_count == 0
    [task] = store.list_due_cleanup_tasks(now=NOW, limit=10)
    assert task.task_type == "expire_upload"
    assert task.user_id == user.user_id
    assert task.payload["upload_id"] == str(old_upload.upload_id)


def test_run_due_cleanup_expires_upload_and_deletes_object():
    from urdu_pipeline.processor.cleanup_scheduler import (
        run_cleanup_scheduler,
    )

    store = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user = _user(store)
    upload = _upload(
        store,
        user,
        status=UploadStatus.INITIALIZED,
        created_at=NOW - timedelta(hours=2),
    )
    objects.put_stream(
        _object_key(upload.upload_id),
        BytesIO(b"audio"),
        metadata=ObjectMetadata(content_type="audio/wav"),
    )

    result = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )

    assert result.scheduled_count == 1
    assert result.succeeded_count == 1
    assert store.get_upload(user_id=user.user_id, upload_id=upload.upload_id).status == UploadStatus.EXPIRED
    with pytest.raises(KeyError):
        objects.head_object(_object_key(upload.upload_id))


def test_run_due_cleanup_aborts_abandoned_multipart_upload():
    from urdu_pipeline.application.ports import MultipartUpload
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user = _user(store)
    upload_id = UploadId.new()
    mp_upload = objects.create_multipart_upload(f"uploads/{upload_id}")
    upload = UploadRecord(
        user_id=user.user_id,
        upload_id=upload_id,
        status=UploadStatus.UPLOADING,
        original_filename="audio.wav",
        content_type="audio/wav",
        size_bytes=44,
        multipart_upload_id=mp_upload.upload_id,
        created_at=NOW - timedelta(hours=2),
    )
    store.create_upload(upload)

    result = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )

    assert result.succeeded_count == 1
    assert store.get_upload(user_id=user.user_id, upload_id=upload.upload_id).status == UploadStatus.EXPIRED
    with pytest.raises(KeyError):
        objects.create_signed_part_upload_url(
            MultipartUpload(key=f"uploads/{upload_id}", upload_id=mp_upload.upload_id),
            part_number=1,
            expires_in=timedelta(minutes=5),
        )


def test_run_due_cleanup_deletes_terminal_run_tmp_chunks():
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user = _user(store)
    run = _run(
        store,
        user,
        status=RunStatus.SUCCEEDED,
        created_at=NOW - timedelta(hours=2),
    )
    tmp_prefix = f"tmp/users/{user.user_id}/runs/{run.run_id}/"
    objects.put_stream(tmp_prefix + "chunks/chunk_0001.wav", BytesIO(b"1"))
    objects.put_stream(tmp_prefix + "chunks/chunk_0002.wav", BytesIO(b"2"))
    objects.put_stream(f"tmp/users/{user.user_id}/runs/{RunId.new()}/keep.wav", BytesIO(b"3"))

    result = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )

    assert result.scheduled_count == 1
    assert result.succeeded_count == 1
    assert objects.list_prefix(tmp_prefix) == []
    assert len(objects.list_prefix(f"tmp/users/{user.user_id}/runs/")) == 1


def test_run_due_cleanup_purges_expired_sessions():
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    user = _user(store)
    expired = SessionRecord(
        session_id=SessionId.new(),
        user_id=user.user_id,
        token_hash="expired-session-hash",
        expires_at=NOW - timedelta(minutes=1),
        created_at=NOW - timedelta(hours=2),
    )
    active = SessionRecord(
        session_id=SessionId.new(),
        user_id=user.user_id,
        token_hash="active-session-hash",
        expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
    )
    store.create_session(expired)
    store.create_session(active)

    result = run_cleanup_scheduler(
        store,
        object_store=InMemoryObjectStore(),
        now=NOW,
        config=_config(),
    )

    assert result.purged_sessions == 1
    assert store.get_session_by_token_hash("expired-session-hash") is None
    assert store.get_session_by_token_hash("active-session-hash") == active


def test_run_due_cleanup_purges_revoked_bearer_tokens_after_retention():
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    user = _user(store)
    revoked = BearerTokenRecord(
        token_id=TokenId.new(),
        user_id=user.user_id,
        token_hash="revoked-token-hash",
        name="revoked",
        revoked_at=NOW - timedelta(hours=2),
        created_at=NOW - timedelta(days=1),
    )
    active = BearerTokenRecord(
        token_id=TokenId.new(),
        user_id=user.user_id,
        token_hash="active-token-hash",
        name="active",
        revoked_at=None,
        created_at=NOW,
    )
    store.create_bearer_token(revoked)
    store.create_bearer_token(active)

    result = run_cleanup_scheduler(
        store,
        object_store=InMemoryObjectStore(),
        now=NOW,
        config=_config(),
    )

    assert result.purged_tokens == 1
    assert store.get_bearer_token(revoked.token_id) is None
    assert store.get_bearer_token(active.token_id) == active


class _FlakyObjectStore(InMemoryObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_deletes = True

    def delete_object(self, key: str) -> None:
        if self.fail_deletes:
            raise RuntimeError("object store unavailable")
        return super().delete_object(key)


def test_failed_cleanup_retry_is_rescheduled_then_succeeds():
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    objects = _FlakyObjectStore()
    user = _user(store)
    upload = _upload(
        store,
        user,
        status=UploadStatus.INITIALIZED,
        created_at=NOW - timedelta(hours=2),
    )
    objects.put_stream(_object_key(upload.upload_id), BytesIO(b"audio"))

    failed = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )

    assert failed.retrying_count == 1
    task = store.get_cleanup_task(failed.task_ids[0])
    assert task.status.name == "RETRYING"
    assert task.attempts == 1
    assert task.run_at == NOW + timedelta(minutes=5)
    assert store.get_upload(user_id=user.user_id, upload_id=upload.upload_id).status == UploadStatus.INITIALIZED

    objects.fail_deletes = False
    retried = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW + timedelta(minutes=5),
        config=_config(),
    )

    assert retried.scheduled_count == 0
    assert retried.succeeded_count == 1
    task = store.get_cleanup_task(failed.task_ids[0])
    assert task.status.name == "SUCCEEDED"
    assert task.attempts == 2
    assert store.get_upload(user_id=user.user_id, upload_id=upload.upload_id).status == UploadStatus.EXPIRED


def test_succeeded_cleanup_task_is_not_run_again():
    from urdu_pipeline.processor.cleanup_scheduler import run_cleanup_scheduler

    store = InMemoryMetadataStore()
    objects = InMemoryObjectStore()
    user = _user(store)
    upload = _upload(
        store,
        user,
        status=UploadStatus.INITIALIZED,
        created_at=NOW - timedelta(hours=2),
    )
    objects.put_stream(_object_key(upload.upload_id), BytesIO(b"audio"))

    first = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )
    second = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=_config(),
    )

    assert first.succeeded_count == 1
    assert second.scheduled_count == 0
    assert second.executed_count == 0
