"""Processor transcription + reconciliation stage — Step 5.2.3.

Orchestrates the transcription and reconciliation steps inside the background
processor:

1. For each chunk in the manifest:
   a. Check for external cancellation (user called ``DELETE /runs/{run_id}``).
      If the job's current status in the metadata store is ``CANCELLED``, raise
      ``FatalJobError`` immediately — no further chunks are transcribed.
   b. Call ``chunk_transcriber_fn(chunk)`` → ``TranscriptionResult``.
   c. Record per-chunk usage via ``usage_ledger.record_usage``.
2. Assemble a ``RawTranscriptArtifact`` from all chunk results.
3. Persist the raw artifact via ``artifact_repo.save_artifact``
   (``stage=TRANSCRIBER``, ``artifact_type=RAW_URDU_TRANSCRIPT``).
4. Call ``reconciler_fn(raw_artifact)`` → ``ReconciledTranscriptArtifact``.
5. Persist the reconciled artifact via ``artifact_repo.save_artifact``
   (``stage=TRANSCRIPT_RECONCILER``, ``artifact_type=RECONCILED_URDU_TRANSCRIPT``).
6. Return ``(raw_ref, reconciled_ref)``.

Injectability
─────────────
Both ``chunk_transcriber_fn`` and ``reconciler_fn`` are plain callables so
this module can be unit-tested without ffmpeg, network calls, or a real
audio provider.

Production wrappers live in the processor orchestrator (future step) and
capture workspace/provider/settings in closures.
"""

from __future__ import annotations

import uuid
from typing import Callable

from urdu_pipeline.application.ports.services import JobRecord, MetadataStore, UsageLedger, UsageRecord
from urdu_pipeline.application.ports.storage import ArtifactReference, ArtifactRepository
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobStatus,
    ProviderRunId,
)
from urdu_pipeline.processor.idempotency import find_stage_artifact, stage_usage_key
from urdu_pipeline.processor.lifecycle import FatalJobError
from urdu_pipeline.providers.base import TranscriptionResult
from urdu_pipeline.schemas.chunks import AudioChunk, ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    RawTranscriptChunk,
    ReconciledTranscriptArtifact,
)


def _check_cancellation(
    job_record: JobRecord,
    metadata_store: MetadataStore,
) -> None:
    """Raise FatalJobError if the job has been externally cancelled."""
    current = metadata_store.get_job_by_id(job_record.job_id)
    if current is not None and current.status == JobStatus.CANCELLED:
        raise FatalJobError(
            f"job {job_record.job_id} was cancelled; stopping transcription."
        )


def run_transcription_and_reconciliation(
    job_record: JobRecord,
    chunk_manifest: ChunkManifestArtifact,
    *,
    metadata_store: MetadataStore,
    artifact_repo: ArtifactRepository,
    usage_ledger: UsageLedger,
    chunk_transcriber_fn: Callable[[AudioChunk], TranscriptionResult],
    reconciler_fn: Callable[[RawTranscriptArtifact], ReconciledTranscriptArtifact],
) -> tuple[ArtifactReference, ArtifactReference]:
    """Transcribe all chunks, reconcile, and persist both artifacts.

    Parameters
    ──────────
    job_record
        Current job — provides ``user_id``, ``run_id``, ``job_id`` for ownership
        checks, cancellation polling, and usage ledger records.
    chunk_manifest
        Chunk manifest produced by ``run_chunker_stage`` (Step 5.2.2).
    metadata_store
        Metadata store; polled before each chunk to detect external cancellation.
    artifact_repo
        Artifact repository for persisting raw + reconciled transcript artifacts.
    usage_ledger
        Usage ledger for recording per-chunk provider cost and metadata.
    chunk_transcriber_fn
        Callable that transcribes a single audio chunk and returns a
        ``TranscriptionResult``.  In production this wraps ``TranscriberStage``
        with a workspace/provider closure; in tests it returns fake text.
    reconciler_fn
        Callable that reconciles a ``RawTranscriptArtifact`` into a
        ``ReconciledTranscriptArtifact``.  The deterministic ``ReconcilerStage``
        is pure and suitable for direct use in both tests and production.

    Returns
    ───────
    ``(raw_artifact_ref, reconciled_artifact_ref)`` — both
    ``ArtifactReference`` instances returned by the repository.

    Raises
    ──────
    FatalJobError
        If the job is cancelled (detected before any chunk or between chunks).
    Any exception raised by ``chunk_transcriber_fn`` or ``reconciler_fn``
    propagates unchanged.
    """
    existing = artifact_repo.list_run_artifacts(
        user_id=job_record.user_id, run_id=job_record.run_id
    )
    raw_existing = find_stage_artifact(
        existing, ArtifactStage.TRANSCRIBER, ArtifactType.RAW_URDU_TRANSCRIPT
    )
    rec_existing = find_stage_artifact(
        existing, ArtifactStage.TRANSCRIPT_RECONCILER, ArtifactType.RECONCILED_URDU_TRANSCRIPT
    )
    if raw_existing and rec_existing:
        return raw_existing, rec_existing

    raw_chunks: list[RawTranscriptChunk] = []
    sorted_chunks = sorted(chunk_manifest.chunks, key=lambda c: c.chunk_index)

    for chunk in sorted_chunks:
        _check_cancellation(job_record, metadata_store)

        result = chunk_transcriber_fn(chunk)

        raw_chunks.append(
            RawTranscriptChunk(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                start_ms=chunk.start_ms,
                end_ms=chunk.end_ms,
                text_urdu=result.text,
                provider_metadata=result.provider_metadata,
            )
        )

        usage_ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=job_record.user_id,
                run_id=job_record.run_id,
                job_id=job_record.job_id,
                provider_name=getattr(result, "provider_name", "unknown"),
                model_id=result.model_id,
                cost_usd=float(result.actual_usage.get("cost_usd", 0.0)),
                usage=dict(result.actual_usage),
                idempotency_key=stage_usage_key(
                    job_record.run_id, "transcriber", chunk.chunk_id
                ),
            )
        )

    raw_artifact = _build_raw_artifact(
        chunk_manifest=chunk_manifest,
        raw_chunks=raw_chunks,
    )

    raw_ref = artifact_repo.save_artifact(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        stage=ArtifactStage.TRANSCRIBER,
        artifact_type=ArtifactType.RAW_URDU_TRANSCRIPT,
        artifact_id=ArtifactId.new(),
        payload=raw_artifact.model_dump(),
    )

    reconciled_artifact = reconciler_fn(raw_artifact)

    reconciled_ref = artifact_repo.save_artifact(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        stage=ArtifactStage.TRANSCRIPT_RECONCILER,
        artifact_type=ArtifactType.RECONCILED_URDU_TRANSCRIPT,
        artifact_id=ArtifactId.new(),
        payload=reconciled_artifact.model_dump(),
    )

    return raw_ref, reconciled_ref


def _build_raw_artifact(
    *,
    chunk_manifest: ChunkManifestArtifact,
    raw_chunks: list[RawTranscriptChunk],
) -> RawTranscriptArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"raw_urdu_transcript_{uuid.uuid4().hex[:12]}",
        stage_name="transcriber",
        artifact_type="raw_urdu_transcript",
        source_input_hash=chunk_manifest.source_audio_hash,
        upstream_artifact_ids=[chunk_manifest.manifest.artifact_id],
        chunk_length_seconds=chunk_manifest.chunk_length_seconds,
        overlap_seconds=chunk_manifest.overlap_seconds,
        cache_hit=False,
    )
    return RawTranscriptArtifact(
        source_audio_hash=chunk_manifest.source_audio_hash,
        chunk_manifest_artifact_id=chunk_manifest.manifest.artifact_id,
        chunks=raw_chunks,
        manifest=manifest,
    )


__all__ = ["run_transcription_and_reconciliation"]
