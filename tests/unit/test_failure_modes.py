"""Failure-mode contracts for cloud-agnostic backend processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Mapping, Sequence

import pytest

from urdu_pipeline.application.ports import (
    ArtifactReference,
    ObjectInfo,
    ObjectMetadata,
)
from urdu_pipeline.application.ports.services import (
    JobLease,
    JobRecord,
    QueueMessage,
    RunRecord,
    UploadRecord,
    UserRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobId,
    JobStatus,
    RunId,
    RunStatus,
    ServiceIdentityId,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryJobQueue,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemoryUsageLedger,
)
from urdu_pipeline.processor.lifecycle import (
    TransientJobError,
    claim_and_run,
)
from urdu_pipeline.schemas.chunks import AudioChunk, ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    ReconciledSegment,
    ReconciledTranscriptArtifact,
)


NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
WORKER_ID = ServiceIdentityId.new()


def _seed_job(store: InMemoryMetadataStore) -> tuple[JobRecord, RunRecord]:
    user = UserRecord(
        user_id=UserId.new(),
        username=f"failure-user-{uuid.uuid4().hex[:8]}",
        status=UserStatus.ACTIVE,
    )
    store.create_user(user)
    run = RunRecord(
        user_id=user.user_id,
        run_id=RunId.new(),
        status=RunStatus.QUEUED,
        upload_id=UploadId.new(),
    )
    store.create_run(run)
    job = JobRecord(
        user_id=user.user_id,
        run_id=run.run_id,
        job_id=JobId.new(),
        status=JobStatus.QUEUED,
    )
    store.create_job(job)
    return job, run


def _job_repo(
    *,
    metadata: InMemoryMetadataStore | None = None,
    objects: InMemoryObjectStore | None = None,
    job: JobRecord | None = None,
):
    from urdu_pipeline.infrastructure.artifacts import ObjectStoreArtifactRepository

    return ObjectStoreArtifactRepository(
        metadata_store=metadata or InMemoryMetadataStore(),
        object_store=objects or InMemoryObjectStore(),
        job_id=job.job_id if job is not None else JobId.new(),
    )


class _DatabaseOutageOnRunning(InMemoryMetadataStore):
    def update_job(self, record: JobRecord) -> None:
        if record.status == JobStatus.RUNNING:
            raise RuntimeError("database unavailable api_key=sk-test-secret")
        super().update_job(record)


def test_database_outage_before_handler_releases_lease_for_retry():
    queue = InMemoryJobQueue()
    store = _DatabaseOutageOnRunning()
    job, _run = _seed_job(store)
    queue.enqueue(QueueMessage(job_id=job.job_id))
    handler_called = False

    def handler(_lease: JobLease, _store: InMemoryMetadataStore) -> None:
        nonlocal handler_called
        handler_called = True

    with pytest.raises(RuntimeError, match="database unavailable"):
        claim_and_run(queue, store, worker_id=WORKER_ID, handler=handler)

    assert handler_called is False
    retry_lease = queue.claim(worker_id=WORKER_ID, lease_seconds=30)
    assert retry_lease is not None
    assert retry_lease.job_id == job.job_id
    assert retry_lease.attempt_number == 2


class _FailingCompleteQueue(InMemoryJobQueue):
    def complete(self, lease: JobLease) -> None:
        raise RuntimeError("queue unavailable while completing job")


def test_queue_outage_does_not_publish_success_status():
    queue = _FailingCompleteQueue()
    store = InMemoryMetadataStore()
    job, run = _seed_job(store)
    queue.enqueue(QueueMessage(job_id=job.job_id))

    with pytest.raises(RuntimeError, match="queue unavailable"):
        claim_and_run(
            queue,
            store,
            worker_id=WORKER_ID,
            handler=lambda _lease, _store: None,
        )

    updated_job = store.get_job_by_id(job.job_id)
    updated_run = store.get_run(user_id=run.user_id, run_id=run.run_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.RUNNING
    assert updated_run is not None
    assert updated_run.status == RunStatus.RUNNING


class _FailingJsonObjectStore(InMemoryObjectStore):
    def put_stream(self, key, body, *, metadata=None) -> ObjectInfo:
        if key.endswith(".json"):
            raise RuntimeError("object store unavailable")
        return super().put_stream(key, body, metadata=metadata)


def test_object_store_outage_does_not_record_artifact_metadata():
    metadata = InMemoryMetadataStore()
    objects = _FailingJsonObjectStore()
    job, _run = _seed_job(metadata)
    artifact_id = ArtifactId.new()
    repo = _job_repo(metadata=metadata, objects=objects, job=job)

    with pytest.raises(RuntimeError, match="object store unavailable"):
        repo.save_artifact(
            user_id=job.user_id,
            run_id=job.run_id,
            stage=ArtifactStage.CHUNKER,
            artifact_type=ArtifactType.CHUNK_MANIFEST,
            artifact_id=artifact_id,
            payload={"ok": True},
        )

    assert metadata.get_artifact(user_id=job.user_id, artifact_id=artifact_id) is None
    assert objects.list_prefix("artifacts/") == []


class _FailingMarkdownObjectStore(InMemoryObjectStore):
    def put_stream(self, key, body, *, metadata=None) -> ObjectInfo:
        if key.endswith(".md"):
            raise RuntimeError("markdown artifact write failed")
        return super().put_stream(key, body, metadata=metadata)


def test_partial_artifact_write_removes_orphaned_json_object():
    metadata = InMemoryMetadataStore()
    objects = _FailingMarkdownObjectStore()
    job, _run = _seed_job(metadata)
    artifact_id = ArtifactId.new()
    repo = _job_repo(metadata=metadata, objects=objects, job=job)

    with pytest.raises(RuntimeError, match="markdown artifact write failed"):
        repo.save_artifact(
            user_id=job.user_id,
            run_id=job.run_id,
            stage=ArtifactStage.TRANSLATOR,
            artifact_type=ArtifactType.ENGLISH_TRANSLATION,
            artifact_id=artifact_id,
            payload={"full_text_english": "safe short text"},
            markdown="# Translation\n\nsafe short text",
        )

    assert metadata.get_artifact(user_id=job.user_id, artifact_id=artifact_id) is None
    assert objects.list_prefix("artifacts/") == []


@dataclass
class _FakeArtifactRepo:
    saved: list[dict[str, Any]] = field(default_factory=list)

    def save_artifact(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
        stage: ArtifactStage,
        artifact_type: ArtifactType,
        artifact_id: ArtifactId,
        payload: Mapping[str, Any],
        markdown: str | None = None,
    ) -> ArtifactReference:
        self.saved.append(
            {
                "user_id": user_id,
                "run_id": run_id,
                "stage": stage,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "payload": dict(payload),
                "markdown": markdown,
            }
        )
        return ArtifactReference(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            has_markdown=markdown is not None,
        )

    def get_artifact_metadata(self, *, user_id, artifact_id):  # pragma: no cover
        raise NotImplementedError

    def load_artifact(self, *, user_id, artifact_id, artifact_format):  # pragma: no cover
        raise NotImplementedError

    def list_run_artifacts(self, *, user_id, run_id) -> Sequence[ArtifactReference]:
        return []


def _chunk_manifest() -> ChunkManifestArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"chunk_manifest_{uuid.uuid4().hex[:12]}",
        stage_name="chunker",
        artifact_type="chunk_manifest",
        source_input_hash="src_hash",
        chunk_length_seconds=10,
        overlap_seconds=0,
        cache_hit=False,
    )
    return ChunkManifestArtifact(
        source_audio_path="input/audio.wav",
        source_audio_hash="src_hash",
        source_audio_duration_ms=10_000,
        source_audio_format="wav",
        chunk_length_seconds=10,
        overlap_seconds=0,
        chunks=[
            AudioChunk(
                chunk_id="chunk_0001",
                source_audio_hash="src_hash",
                chunk_index=1,
                start_ms=0,
                end_ms=10_000,
                duration_ms=10_000,
                file_path="chunks/chunk_0001.wav",
                file_hash="chunk_hash",
                file_size_bytes=44,
                audio_format="wav",
            )
        ],
        manifest=manifest,
    )


def _reconciler(raw: RawTranscriptArtifact) -> ReconciledTranscriptArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"reconciled_{uuid.uuid4().hex[:12]}",
        stage_name="transcript_reconciler",
        artifact_type="reconciled_urdu_transcript",
        source_input_hash=raw.source_audio_hash,
        upstream_artifact_ids=[raw.manifest.artifact_id],
        model_provider="deterministic",
        model_id="test-reconciler",
        cache_hit=False,
    )
    return ReconciledTranscriptArtifact(
        source_audio_hash=raw.source_audio_hash,
        raw_transcript_artifact_id=raw.manifest.artifact_id,
        segments=[
            ReconciledSegment(
                segment_id="seg_0001",
                source_chunk_ids=["chunk_0001"],
                text_urdu="متن",
            )
        ],
        full_text_urdu="متن",
        manifest=manifest,
    )


def test_provider_transient_failure_is_classified_for_retry_without_side_effects():
    from urdu_pipeline.processor.transcriber import run_transcription_and_reconciliation
    from urdu_pipeline.providers.base import ProviderTransientError

    metadata = InMemoryMetadataStore()
    job, _run = _seed_job(metadata)
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()

    def provider_outage(_chunk: AudioChunk):
        raise ProviderTransientError("provider rate limited api_key=sk-test-secret")

    with pytest.raises(TransientJobError) as exc_info:
        run_transcription_and_reconciliation(
            job,
            _chunk_manifest(),
            metadata_store=metadata,
            artifact_repo=repo,
            usage_ledger=ledger,
            chunk_transcriber_fn=provider_outage,
            reconciler_fn=_reconciler,
        )

    assert "sk-test-secret" not in str(exc_info.value)
    assert repo.saved == []
    assert ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id) == []


def test_retry_exhaustion_dead_letter_reason_is_redacted():
    queue = InMemoryJobQueue()
    store = InMemoryMetadataStore()
    job, run = _seed_job(store)
    queue.enqueue(QueueMessage(job_id=job.job_id))

    def always_transient(_lease: JobLease, _store: InMemoryMetadataStore) -> None:
        raise TransientJobError("temporary provider failure api_key=sk-test-secret")

    for _ in range(3):
        claim_and_run(
            queue,
            store,
            worker_id=WORKER_ID,
            handler=always_transient,
            max_attempts=3,
        )

    reason = queue._dead_letters[job.job_id]
    assert "sk-test-secret" not in reason
    updated_job = store.get_job_by_id(job.job_id)
    updated_run = store.get_run(user_id=run.user_id, run_id=run.run_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.FAILED
    assert updated_run is not None
    assert updated_run.status == RunStatus.FAILED


class _CleanupFailureObjectStore(InMemoryObjectStore):
    def delete_object(self, key: str) -> None:
        raise RuntimeError("cleanup delete failed token=sk-test-secret")


def test_cleanup_failure_after_retry_exhaustion_is_failed_and_observable():
    from urdu_pipeline.processor.cleanup_scheduler import (
        CleanupSchedulerConfig,
        run_cleanup_scheduler,
    )

    store = InMemoryMetadataStore()
    objects = _CleanupFailureObjectStore()
    user = UserRecord(
        user_id=UserId.new(),
        username="cleanup-failure-user",
        status=UserStatus.ACTIVE,
    )
    store.create_user(user)
    upload = UploadRecord(
        user_id=user.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.INITIALIZED,
        original_filename="audio.wav",
        content_type="audio/wav",
        size_bytes=44,
        created_at=NOW - timedelta(hours=2),
    )
    store.create_upload(upload)
    objects.put_stream(
        f"uploads/{upload.upload_id}",
        BytesIO(b"audio"),
        metadata=ObjectMetadata(content_type="audio/wav"),
    )

    result = run_cleanup_scheduler(
        store,
        object_store=objects,
        now=NOW,
        config=CleanupSchedulerConfig(
            upload_ttl=timedelta(hours=1),
            retry_delay=timedelta(minutes=5),
            max_attempts=1,
        ),
    )

    assert result.failed_count == 1
    task = store.get_cleanup_task(result.task_ids[0])
    assert task is not None
    assert task.status.name == "FAILED"
    assert task.completed_at == NOW
    assert task.last_error is not None
    assert "sk-test-secret" not in task.last_error
