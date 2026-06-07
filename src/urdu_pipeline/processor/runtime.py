"""Runtime processor loop for API-backed local deployments."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, JobStatus
from urdu_pipeline.infrastructure.artifacts import ObjectStoreArtifactRepository
from urdu_pipeline.infrastructure.db.metadata import (
    PostgresMetadataStore,
    StageEventRecord,
)
from urdu_pipeline.infrastructure.db.migrations import connect_postgres
from urdu_pipeline.infrastructure.redis_queue import RedisJobQueue
from urdu_pipeline.infrastructure.s3 import S3ObjectStore
from urdu_pipeline.logging_utils import redact_event_message, redact_log_fields
from urdu_pipeline.processor.lifecycle import claim_and_run
from urdu_pipeline.stages.article_generator import run_article_stage
from urdu_pipeline.stages.chunker import run_chunker_stage
from urdu_pipeline.stages.transcriber import run_transcriber_stage
from urdu_pipeline.stages.transcript_reconciler import run_reconciler_stage
from urdu_pipeline.stages.translator import run_translator_stage


def run_processor(
    *,
    service_token: str,
    api_url: str,
    once: bool = False,
) -> int:
    """Run the background processor loop.

    Returns the number of jobs processed before exit. With ``once=True`` the
    processor claims at most one job and returns immediately.
    """
    settings = get_settings()
    _ping_api(api_url=api_url, service_token=service_token)
    connection = connect_postgres(settings.database_url)
    metadata_store = PostgresMetadataStore(connection)
    object_store = _build_object_store(settings)
    queue = RedisJobQueue(metadata_store=metadata_store, redis_url=settings.redis_url)
    processed = 0

    while True:
        try:
            worker_id = _resolve_worker_id(metadata_store)
            did_work = claim_and_run(
                queue,
                metadata_store,
                worker_id=worker_id,
                handler=lambda lease, store: _process_job(
                    lease.job_id,
                    metadata_store=store,
                    object_store=object_store,
                    settings=settings,
                ),
            )
        except Exception:
            if once:
                raise
            time.sleep(2.0)
            continue

        if did_work:
            processed += 1
        if once:
            return processed
        time.sleep(0.25 if did_work else 2.0)


def _process_job(
    job_id,
    *,
    metadata_store: Any,
    object_store: Any,
    settings: Settings,
) -> None:
    job = metadata_store.get_job_by_id(job_id)
    if job is None:
        raise RuntimeError(f"job not found: {job_id}")
    run = metadata_store.get_run(user_id=job.user_id, run_id=job.run_id)
    if run is None or run.upload_id is None:
        raise RuntimeError(f"run/upload not found for job: {job_id}")
    upload = metadata_store.get_upload(user_id=job.user_id, upload_id=run.upload_id)
    if upload is None:
        raise RuntimeError(f"upload not found for job: {job_id}")

    with TemporaryDirectory(prefix=f"{job.run_id}_", dir=_workspace_parent(settings)) as tmp:
        root = Path(tmp) / "run"
        store = ArtifactStore.for_existing_run(root)
        source_path = _materialize_upload(upload, object_store=object_store, root=root)
        repo = ObjectStoreArtifactRepository(
            metadata_store=metadata_store,
            object_store=object_store,
            job_id=job.job_id,
        )

        _record_event(metadata_store, job, ArtifactStage.CHUNKER, "stage_started")
        chunk_manifest = run_chunker_stage(audio_path=source_path, store=store)
        _save_stage_artifact(
            repo,
            job,
            ArtifactStage.CHUNKER,
            ArtifactType.CHUNK_MANIFEST,
            store.paths.artifacts / "chunk_manifest.json",
            store.paths.artifacts / "chunk_summary.md",
        )
        _record_event(metadata_store, job, ArtifactStage.CHUNKER, "stage_succeeded")

        _record_event(metadata_store, job, ArtifactStage.TRANSCRIBER, "stage_started")
        raw = run_transcriber_stage(chunk_manifest=chunk_manifest, store=store)
        _save_stage_artifact(
            repo,
            job,
            ArtifactStage.TRANSCRIBER,
            ArtifactType.RAW_URDU_TRANSCRIPT,
            store.paths.artifacts / "raw_urdu_transcript.json",
            store.paths.artifacts / "raw_urdu_transcript.md",
        )
        _record_event(metadata_store, job, ArtifactStage.TRANSCRIBER, "stage_succeeded")

        _record_event(metadata_store, job, ArtifactStage.TRANSCRIPT_RECONCILER, "stage_started")
        reconciled = run_reconciler_stage(raw=raw, store=store)
        _save_stage_artifact(
            repo,
            job,
            ArtifactStage.TRANSCRIPT_RECONCILER,
            ArtifactType.RECONCILED_URDU_TRANSCRIPT,
            store.paths.artifacts / "reconciled_urdu_transcript.json",
            store.paths.artifacts / "reconciled_urdu_transcript.md",
        )
        _record_event(metadata_store, job, ArtifactStage.TRANSCRIPT_RECONCILER, "stage_succeeded")

        _record_event(metadata_store, job, ArtifactStage.TRANSLATOR, "stage_started")
        translation = run_translator_stage(reconciled=reconciled, store=store)
        _save_stage_artifact(
            repo,
            job,
            ArtifactStage.TRANSLATOR,
            ArtifactType.ENGLISH_TRANSLATION,
            store.paths.artifacts / "english_translation.json",
            store.paths.artifacts / "english_translation.md",
        )
        _record_event(metadata_store, job, ArtifactStage.TRANSLATOR, "stage_succeeded")

        _record_event(metadata_store, job, ArtifactStage.ARTICLE_GENERATOR, "stage_started")
        run_article_stage(translation=translation, store=store)
        _save_stage_artifact(
            repo,
            job,
            ArtifactStage.ARTICLE_GENERATOR,
            ArtifactType.FINAL_ARTICLE,
            store.paths.artifacts / "final_article.json",
            store.paths.artifacts / "final_article.md",
        )
        _record_event(metadata_store, job, ArtifactStage.ARTICLE_GENERATOR, "stage_succeeded")

        # Verify no retry scratch objects remain under the canonical tmp prefix.
        object_store.delete_prefix(f"tmp/users/{job.user_id}/runs/{job.run_id}/")


def _save_stage_artifact(
    repo: ObjectStoreArtifactRepository,
    job,
    stage: ArtifactStage,
    artifact_type: ArtifactType,
    json_path: Path,
    markdown_path: Path | None,
) -> None:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = (
        markdown_path.read_text(encoding="utf-8")
        if markdown_path is not None and markdown_path.exists()
        else None
    )
    repo.save_artifact(
        user_id=job.user_id,
        run_id=job.run_id,
        stage=stage,
        artifact_type=artifact_type,
        artifact_id=ArtifactId.new(),
        payload=payload,
        markdown=markdown,
    )


def _materialize_upload(upload, *, object_store: Any, root: Path) -> Path:
    source_key = f"uploads/{upload.upload_id}"
    suffix = Path(upload.original_filename or "audio.wav").suffix or ".wav"
    target = root / "source" / f"input{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(object_store.get_stream(source_key).read())
    return target


def _record_event(
    metadata_store: Any,
    job,
    stage: ArtifactStage,
    event_type: str,
    *,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if not hasattr(metadata_store, "record_stage_event"):
        return
    event_payload = {"stage": stage.value}
    if payload:
        event_payload.update(payload)
    metadata_store.record_stage_event(
        StageEventRecord(
            user_id=job.user_id,
            run_id=job.run_id,
            job_id=job.job_id,
            stage=stage,
            event_type=event_type,
            severity="info",
            message=redact_event_message(message or event_type, fallback=event_type),
            payload=redact_log_fields(event_payload),
        )
    )


def _resolve_worker_id(metadata_store: PostgresMetadataStore):
    service_name = os.environ.get("SERVICE_IDENTITY_NAME", "processor")
    service = metadata_store.get_service_identity_by_name(service_name)
    if service is None:
        raise RuntimeError(f"service identity is not seeded: {service_name}")
    return service.service_identity_id


def _workspace_parent(settings: Settings) -> str:
    parent = settings.cache_root_path / "processor-workspaces"
    parent.mkdir(parents=True, exist_ok=True)
    return str(parent)


def _build_object_store(settings: Settings) -> S3ObjectStore:
    return S3ObjectStore(
        bucket=settings.object_store_bucket,
        endpoint_url=settings.object_store_endpoint_url,
        region_name=settings.object_store_region,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key,
        server_side_encryption=settings.object_store_server_side_encryption,
        sse_kms_key_id=settings.object_store_sse_kms_key_id,
    )


def _ping_api(*, api_url: str, service_token: str) -> None:
    request = urllib.request.Request(
        api_url.rstrip("/") + "/internal/ping",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"processor API ping failed: HTTP {response.status}")


__all__ = ["run_processor"]
