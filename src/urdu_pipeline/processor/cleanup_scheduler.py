"""Cleanup scheduler for expired backend resources."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from urdu_pipeline.application.ports import MultipartUpload, ObjectStore
from urdu_pipeline.application.ports.services import UploadRecord
from urdu_pipeline.domain import CleanupTaskId, CleanupTaskStatus, RunId, UploadId, UploadStatus, UserId
from urdu_pipeline.infrastructure.db.metadata import CleanupTaskRecord
from urdu_pipeline.logging_utils import redact_event_message

_SCHEDULER_NAMESPACE = uuid.UUID("4ef0f273-70fd-4952-921f-df0942a69e45")

_TASK_EXPIRE_UPLOAD = "expire_upload"
_TASK_DELETE_RUN_TMP_OBJECTS = "delete_run_tmp_objects"
_TASK_PURGE_EXPIRED_SESSIONS = "purge_expired_sessions"
_TASK_PURGE_REVOKED_TOKENS = "purge_revoked_tokens"


@dataclass(frozen=True)
class CleanupSchedulerConfig:
    upload_ttl: timedelta = timedelta(hours=24)
    run_tmp_retention: timedelta = timedelta(hours=24)
    session_retention: timedelta = timedelta(days=7)
    token_retention: timedelta = timedelta(days=30)
    retry_delay: timedelta = timedelta(minutes=15)
    batch_size: int = 100
    max_attempts: int = 3


@dataclass(frozen=True)
class CleanupSchedulerResult:
    scheduled_count: int = 0
    executed_count: int = 0
    succeeded_count: int = 0
    retrying_count: int = 0
    failed_count: int = 0
    deleted_objects: int = 0
    purged_sessions: int = 0
    purged_tokens: int = 0
    task_ids: list[CleanupTaskId] = field(default_factory=list)

    def merge(self, other: CleanupSchedulerResult) -> CleanupSchedulerResult:
        return CleanupSchedulerResult(
            scheduled_count=self.scheduled_count + other.scheduled_count,
            executed_count=self.executed_count + other.executed_count,
            succeeded_count=self.succeeded_count + other.succeeded_count,
            retrying_count=self.retrying_count + other.retrying_count,
            failed_count=self.failed_count + other.failed_count,
            deleted_objects=self.deleted_objects + other.deleted_objects,
            purged_sessions=self.purged_sessions + other.purged_sessions,
            purged_tokens=self.purged_tokens + other.purged_tokens,
            task_ids=[*self.task_ids, *other.task_ids],
        )


def run_cleanup_scheduler(
    metadata_store: Any,
    *,
    object_store: ObjectStore,
    now: datetime | None = None,
    config: CleanupSchedulerConfig | None = None,
) -> CleanupSchedulerResult:
    """Schedule and execute due cleanup work."""
    effective_now = _coerce_now(now)
    effective_config = config or CleanupSchedulerConfig()
    scheduled = schedule_cleanup_tasks(
        metadata_store,
        now=effective_now,
        config=effective_config,
    )
    executed = run_due_cleanup_tasks(
        metadata_store,
        object_store=object_store,
        now=effective_now,
        config=effective_config,
    )
    return scheduled.merge(executed)


def schedule_cleanup_tasks(
    metadata_store: Any,
    *,
    now: datetime | None = None,
    config: CleanupSchedulerConfig | None = None,
) -> CleanupSchedulerResult:
    """Create due cleanup task rows without executing them."""
    effective_now = _coerce_now(now)
    effective_config = config or CleanupSchedulerConfig()
    scheduled: list[CleanupTaskId] = []

    for upload in _call_sequence(
        metadata_store,
        "list_uploads_ready_to_expire",
        created_before=effective_now - effective_config.upload_ttl,
    ):
        task = _cleanup_task(
            task_type=_TASK_EXPIRE_UPLOAD,
            subject=str(upload.upload_id),
            now=effective_now,
            run_at=effective_now,
            user_id=upload.user_id,
            run_id=None,
            payload={
                "upload_id": str(upload.upload_id),
                "multipart_upload_id": upload.multipart_upload_id,
            },
            max_attempts=effective_config.max_attempts,
        )
        if _create_task_if_new(metadata_store, task):
            scheduled.append(task.cleanup_task_id)

    for run in _call_sequence(
        metadata_store,
        "list_terminal_runs_for_tmp_cleanup",
        created_before=effective_now - effective_config.run_tmp_retention,
    ):
        task = _cleanup_task(
            task_type=_TASK_DELETE_RUN_TMP_OBJECTS,
            subject=str(run.run_id),
            now=effective_now,
            run_at=effective_now,
            user_id=run.user_id,
            run_id=run.run_id,
            payload={"user_id": str(run.user_id), "run_id": str(run.run_id)},
            max_attempts=effective_config.max_attempts,
        )
        if _create_task_if_new(metadata_store, task):
            scheduled.append(task.cleanup_task_id)

    if _call_sequence(metadata_store, "list_expired_sessions", now=effective_now):
        task = _cleanup_task(
            task_type=_TASK_PURGE_EXPIRED_SESSIONS,
            subject=effective_now.isoformat(),
            now=effective_now,
            run_at=effective_now,
            user_id=None,
            run_id=None,
            payload={"expires_before": effective_now.isoformat()},
            max_attempts=effective_config.max_attempts,
        )
        if _create_task_if_new(metadata_store, task):
            scheduled.append(task.cleanup_task_id)

    revoked_before = effective_now - effective_config.token_retention
    if _call_sequence(metadata_store, "list_revoked_bearer_tokens", revoked_before=revoked_before):
        task = _cleanup_task(
            task_type=_TASK_PURGE_REVOKED_TOKENS,
            subject=revoked_before.isoformat(),
            now=effective_now,
            run_at=effective_now,
            user_id=None,
            run_id=None,
            payload={"revoked_before": revoked_before.isoformat()},
            max_attempts=effective_config.max_attempts,
        )
        if _create_task_if_new(metadata_store, task):
            scheduled.append(task.cleanup_task_id)

    return CleanupSchedulerResult(
        scheduled_count=len(scheduled),
        task_ids=scheduled,
    )


def run_due_cleanup_tasks(
    metadata_store: Any,
    *,
    object_store: ObjectStore,
    now: datetime | None = None,
    config: CleanupSchedulerConfig | None = None,
) -> CleanupSchedulerResult:
    """Execute due cleanup tasks and update task status."""
    effective_now = _coerce_now(now)
    effective_config = config or CleanupSchedulerConfig()
    claim = getattr(metadata_store, "claim_due_cleanup_tasks", None)
    if not callable(claim):
        return CleanupSchedulerResult()
    tasks = claim(now=effective_now, limit=effective_config.batch_size)
    result = CleanupSchedulerResult(executed_count=len(tasks), task_ids=[t.cleanup_task_id for t in tasks])

    for task in tasks:
        try:
            counters = _execute_task(
                task,
                metadata_store=metadata_store,
                object_store=object_store,
                now=effective_now,
            )
        except Exception as exc:
            last_error = _safe_error(exc)
            if task.attempts >= task.max_attempts:
                metadata_store.mark_cleanup_task_failed(
                    task.cleanup_task_id,
                    now=effective_now,
                    last_error=last_error,
                )
                result = result.merge(CleanupSchedulerResult(failed_count=1))
            else:
                metadata_store.mark_cleanup_task_retrying(
                    task.cleanup_task_id,
                    now=effective_now,
                    next_run_at=effective_now + effective_config.retry_delay,
                    last_error=last_error,
                )
                result = result.merge(CleanupSchedulerResult(retrying_count=1))
            continue

        metadata_store.mark_cleanup_task_succeeded(task.cleanup_task_id, now=effective_now)
        result = result.merge(CleanupSchedulerResult(succeeded_count=1, **counters))

    return result


def cleanup_task_id_for(task_type: str, subject: str) -> CleanupTaskId:
    """Build a deterministic cleanup task ID for idempotent scheduling."""
    return CleanupTaskId(f"cln_{uuid.uuid5(_SCHEDULER_NAMESPACE, f'{task_type}:{subject}').hex}")


def _execute_task(
    task: CleanupTaskRecord,
    *,
    metadata_store: Any,
    object_store: ObjectStore,
    now: datetime,
) -> dict[str, int]:
    if task.task_type == _TASK_EXPIRE_UPLOAD:
        deleted = _expire_upload_task(task, metadata_store=metadata_store, object_store=object_store)
        return {"deleted_objects": deleted}
    if task.task_type == _TASK_DELETE_RUN_TMP_OBJECTS:
        deleted = _delete_run_tmp_objects_task(task, object_store=object_store)
        return {"deleted_objects": deleted}
    if task.task_type == _TASK_PURGE_EXPIRED_SESSIONS:
        return {"purged_sessions": metadata_store.delete_expired_sessions(now=now)}
    if task.task_type == _TASK_PURGE_REVOKED_TOKENS:
        revoked_before = _parse_datetime(str(task.payload["revoked_before"]))
        return {
            "purged_tokens": metadata_store.delete_revoked_bearer_tokens(
                revoked_before=revoked_before,
            )
        }
    raise ValueError(f"unknown cleanup task type: {task.task_type}")


def _expire_upload_task(
    task: CleanupTaskRecord,
    *,
    metadata_store: Any,
    object_store: ObjectStore,
) -> int:
    upload_id = UploadId(str(task.payload["upload_id"]))
    user_id = UserId(str(task.user_id))
    upload: UploadRecord | None = metadata_store.get_upload(user_id=user_id, upload_id=upload_id)
    if upload is None or upload.status not in {UploadStatus.INITIALIZED, UploadStatus.UPLOADING}:
        return 0

    object_key = _upload_object_key(upload_id)
    if upload.multipart_upload_id:
        try:
            object_store.abort_multipart_upload(
                MultipartUpload(key=object_key, upload_id=upload.multipart_upload_id)
            )
        except KeyError:
            pass
    object_store.delete_object(object_key)
    metadata_store.update_upload(
        UploadRecord(
            user_id=upload.user_id,
            upload_id=upload.upload_id,
            status=UploadStatus.EXPIRED,
            original_filename=upload.original_filename,
            content_type=upload.content_type,
            size_bytes=upload.size_bytes,
            multipart_upload_id=upload.multipart_upload_id,
            created_at=upload.created_at,
        )
    )
    return 1


def _delete_run_tmp_objects_task(task: CleanupTaskRecord, *, object_store: ObjectStore) -> int:
    user_id = str(task.payload["user_id"])
    run_id = str(task.payload["run_id"])
    return object_store.delete_prefix(f"tmp/users/{user_id}/runs/{run_id}/")


def _cleanup_task(
    *,
    task_type: str,
    subject: str,
    now: datetime,
    run_at: datetime,
    user_id: UserId | None,
    run_id: RunId | None,
    payload: Mapping[str, Any],
    max_attempts: int,
) -> CleanupTaskRecord:
    return CleanupTaskRecord(
        cleanup_task_id=cleanup_task_id_for(task_type, subject),
        user_id=user_id,
        run_id=run_id,
        task_type=task_type,
        status=CleanupTaskStatus.PENDING,
        run_at=run_at,
        payload={key: value for key, value in payload.items() if value is not None},
        created_at=now,
        updated_at=now,
        max_attempts=max_attempts,
    )


def _create_task_if_new(metadata_store: Any, task: CleanupTaskRecord) -> bool:
    existing = metadata_store.get_cleanup_task(task.cleanup_task_id)
    if existing is not None:
        return False
    metadata_store.create_cleanup_task_once(task)
    return True


def _call_sequence(metadata_store: Any, method_name: str, **kwargs: Any) -> Sequence[Any]:
    method = getattr(metadata_store, method_name, None)
    if not callable(method):
        return []
    return list(method(**kwargs))


def _upload_object_key(upload_id: UploadId) -> str:
    return f"uploads/{upload_id}"


def _coerce_now(now: datetime | None) -> datetime:
    return now or datetime.now(tz=timezone.utc)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_error(exc: Exception) -> str:
    return redact_event_message(str(exc), fallback=type(exc).__name__) or type(exc).__name__


__all__ = [
    "CleanupSchedulerConfig",
    "CleanupSchedulerResult",
    "cleanup_task_id_for",
    "run_cleanup_scheduler",
    "run_due_cleanup_tasks",
    "schedule_cleanup_tasks",
]
