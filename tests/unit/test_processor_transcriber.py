"""Tests for processor transcription + reconciliation stage — Step 5.2.3.

Design under test
─────────────────
``run_transcription_and_reconciliation(
    job_record, chunk_manifest, *,
    metadata_store, artifact_repo, usage_ledger,
    chunk_transcriber_fn, reconciler_fn,
) -> tuple[ArtifactReference, ArtifactReference]``

    Orchestrates transcription and reconciliation inside the background processor:

    1. For each chunk in ``chunk_manifest.chunks``:
       a. Check whether the job has been cancelled externally by reading the
          current status from ``metadata_store.get_job_by_id``.  If cancelled,
          raise ``FatalJobError`` immediately.
       b. Call ``chunk_transcriber_fn(chunk)`` → ``TranscriptionResult``.
       c. Record the per-chunk usage via ``usage_ledger.record_usage``.
    2. Assemble a ``RawTranscriptArtifact`` from all chunk results.
    3. Persist the raw artifact via ``artifact_repo.save_artifact``
       (stage=TRANSCRIBER, artifact_type=RAW_URDU_TRANSCRIPT).
    4. Call ``reconciler_fn(raw_artifact)`` → ``ReconciledTranscriptArtifact``.
    5. Persist the reconciled artifact via ``artifact_repo.save_artifact``
       (stage=TRANSCRIPT_RECONCILER, artifact_type=RECONCILED_URDU_TRANSCRIPT).
    6. Return ``(raw_ref, reconciled_ref)``.

Design decisions
────────────────
* ``chunk_transcriber_fn`` is a ``Callable[[AudioChunk], TranscriptionResult]``
  so tests can return deterministic text without touching the filesystem or
  network.  The production wrapper captures the workspace/provider in a closure.
* ``reconciler_fn`` is a ``Callable[[RawTranscriptArtifact], ReconciledTranscriptArtifact]``
  (the deterministic ``ReconcilerStage`` is pure and safe to use in tests).
* Cancellation is checked *before* each chunk so that at most one extra provider
  call occurs if a job is cancelled mid-run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pytest

from urdu_pipeline.application.ports.services import (
    JobRecord,
    RunRecord,
    UserRecord,
    UsageRecord,
)
from urdu_pipeline.application.ports.storage import ArtifactFormat, ArtifactReference
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    JobId,
    JobStatus,
    ProviderRunId,
    RunId,
    RunStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.in_memory import (
    InMemoryMetadataStore,
    InMemoryUsageLedger,
)
from urdu_pipeline.processor.lifecycle import FatalJobError
from urdu_pipeline.processor.transcriber import run_transcription_and_reconciliation
from urdu_pipeline.providers.base import TranscriptionResult
from urdu_pipeline.schemas.chunks import AudioChunk, ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    ReconciledTranscriptArtifact,
)


# ── Minimal in-test ArtifactRepository ────────────────────────────────────────


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

    def list_run_artifacts(self, *, user_id, run_id) -> Sequence[ArtifactReference]:  # pragma: no cover
        return []


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_store() -> InMemoryMetadataStore:
    return InMemoryMetadataStore()


def _make_job(
    store: InMemoryMetadataStore,
    *,
    status: JobStatus = JobStatus.RUNNING,
) -> JobRecord:
    user_id = UserId.new()
    run_id = RunId.new()
    store.create_user(UserRecord(user_id=user_id, username="tx_user", status=UserStatus.ACTIVE))
    run = RunRecord(user_id=user_id, run_id=run_id, status=RunStatus.RUNNING)
    store.create_run(run)
    job = JobRecord(
        user_id=user_id,
        run_id=run_id,
        job_id=JobId.new(),
        status=status,
    )
    store.create_job(job)
    return job


def _build_manifest(num_chunks: int = 2) -> ChunkManifestArtifact:
    """Return a minimal ChunkManifestArtifact with ``num_chunks`` chunks."""
    chunks: list[AudioChunk] = []
    for i in range(1, num_chunks + 1):
        chunks.append(
            AudioChunk(
                chunk_id=f"chunk_{i:04d}",
                source_audio_hash="src_hash_abc",
                chunk_index=i,
                start_ms=(i - 1) * 10_000,
                end_ms=i * 10_000,
                duration_ms=10_000,
                file_path=f"chunks/chunk_{i:04d}.mp3",
                file_hash=f"chunk_hash_{i:04d}",
                file_size_bytes=100,
                audio_format="mp3",
            )
        )
    manifest = ArtifactManifest(
        artifact_id=f"chunk_manifest_{uuid.uuid4().hex[:12]}",
        stage_name="chunker",
        artifact_type="chunk_manifest",
        source_input_hash="src_hash_abc",
        chunk_length_seconds=10,
        overlap_seconds=0,
        cache_hit=False,
    )
    return ChunkManifestArtifact(
        source_audio_path="input/lecture.mp3",
        source_audio_hash="src_hash_abc",
        source_audio_duration_ms=num_chunks * 10_000,
        source_audio_format="mp3",
        chunk_length_seconds=10,
        overlap_seconds=0,
        chunks=chunks,
        manifest=manifest,
    )


def _fake_transcriber(text_template: str = "اردو متن chunk={idx}"):
    """Return a chunk_transcriber_fn that returns deterministic Urdu text."""
    call_count = 0

    def fn(chunk: AudioChunk) -> TranscriptionResult:
        nonlocal call_count
        call_count += 1
        return TranscriptionResult(
            text=text_template.format(idx=chunk.chunk_index),
            model_id="fake-asr-v1",
            duration_seconds=10.0,
            actual_usage={"cost_usd": 0.01 * chunk.chunk_index},
            provider_metadata={"fake": True, "call": call_count},
        )

    fn.call_count = lambda: call_count  # type: ignore[attr-defined]
    return fn


def _real_reconciler(raw: RawTranscriptArtifact) -> ReconciledTranscriptArtifact:
    """Reconcile using the real deterministic ReconcilerStage (no filesystem)."""
    from urdu_pipeline.stages.transcript_reconciler import ReconcilerStage
    from urdu_pipeline.infrastructure.filesystem import FilesystemArtifactSink
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sink_path = Path(tmp)
        (sink_path / "artifacts").mkdir(parents=True, exist_ok=True)

        class _InlineSink:
            def write_artifact(self, model, filename):
                return sink_path / "artifacts" / filename

            def write_markdown(self, text, filename):
                p = sink_path / "artifacts" / filename
                p.write_text(text)
                return p

        stage = ReconcilerStage(artifact_sink=_InlineSink())
        return stage.run(raw)


def _trivial_reconciler(raw: RawTranscriptArtifact) -> ReconciledTranscriptArtifact:
    """Minimal reconciler that skips overlap stitching — fast for most tests."""
    import uuid as _uuid
    from urdu_pipeline.schemas.transcripts import ReconciledSegment
    from urdu_pipeline.schemas.manifests import ArtifactManifest

    segments = [
        ReconciledSegment(
            segment_id=f"seg_{c.chunk_index:04d}",
            source_chunk_ids=[c.chunk_id],
            approx_start_ms=c.start_ms,
            approx_end_ms=c.end_ms,
            text_urdu=c.text_urdu,
        )
        for c in sorted(raw.chunks, key=lambda c: c.chunk_index)
    ]
    full_text = "\n\n".join(s.text_urdu for s in segments)
    manifest = ArtifactManifest(
        artifact_id=f"reconciled_{_uuid.uuid4().hex[:12]}",
        stage_name="transcript_reconciler",
        artifact_type="reconciled_urdu_transcript",
        source_input_hash=raw.source_audio_hash,
        upstream_artifact_ids=[raw.manifest.artifact_id],
        model_provider="deterministic",
        model_id="trivial",
        prompt_id="reconciliation",
        prompt_version="v1",
        cache_hit=False,
    )
    return ReconciledTranscriptArtifact(
        source_audio_hash=raw.source_audio_hash,
        raw_transcript_artifact_id=raw.manifest.artifact_id,
        segments=segments,
        full_text_urdu=full_text,
        manifest=manifest,
    )


# ── Return-value tests ────────────────────────────────────────────────────────


def test_run_transcription_returns_two_artifact_references():
    store = _make_store()
    job = _make_job(store)
    raw_ref, rec_ref = run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=2),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    assert isinstance(raw_ref, ArtifactReference)
    assert isinstance(rec_ref, ArtifactReference)


def test_raw_transcript_artifact_ref_has_transcriber_stage():
    store = _make_store()
    job = _make_job(store)
    raw_ref, _ = run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=1),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    assert raw_ref.stage == ArtifactStage.TRANSCRIBER
    assert raw_ref.artifact_type == ArtifactType.RAW_URDU_TRANSCRIPT


def test_reconciled_artifact_ref_has_reconciler_stage():
    store = _make_store()
    job = _make_job(store)
    _, rec_ref = run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=1),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    assert rec_ref.stage == ArtifactStage.TRANSCRIPT_RECONCILER
    assert rec_ref.artifact_type == ArtifactType.RECONCILED_URDU_TRANSCRIPT


# ── Artifact persistence tests ────────────────────────────────────────────────


def test_two_artifacts_saved_to_repo():
    store = _make_store()
    job = _make_job(store)
    repo = _FakeArtifactRepo()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=2),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    assert len(repo.saved) == 2


def test_raw_artifact_payload_contains_chunk_list():
    store = _make_store()
    job = _make_job(store)
    repo = _FakeArtifactRepo()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=3),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    raw_saved = next(s for s in repo.saved if s["stage"] == ArtifactStage.TRANSCRIBER)
    assert "chunks" in raw_saved["payload"]
    assert len(raw_saved["payload"]["chunks"]) == 3


def test_reconciled_artifact_payload_contains_full_text():
    store = _make_store()
    job = _make_job(store)
    repo = _FakeArtifactRepo()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=2),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    rec_saved = next(
        s for s in repo.saved if s["stage"] == ArtifactStage.TRANSCRIPT_RECONCILER
    )
    assert "full_text_urdu" in rec_saved["payload"]
    assert rec_saved["payload"]["full_text_urdu"]  # non-empty


def test_artifact_ownership_matches_job():
    store = _make_store()
    job = _make_job(store)
    repo = _FakeArtifactRepo()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=1),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    for saved in repo.saved:
        assert saved["user_id"] == job.user_id
        assert saved["run_id"] == job.run_id


# ── Usage ledger tests ────────────────────────────────────────────────────────


def test_usage_recorded_for_each_chunk():
    store = _make_store()
    job = _make_job(store)
    ledger = InMemoryUsageLedger()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=3),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    records = ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id)
    assert len(records) == 3


def test_usage_records_have_correct_user_and_run():
    store = _make_store()
    job = _make_job(store)
    ledger = InMemoryUsageLedger()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=2),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    for record in ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id):
        assert record.user_id == job.user_id
        assert record.run_id == job.run_id
        assert record.job_id == job.job_id


def test_zero_chunks_records_no_usage():
    store = _make_store()
    job = _make_job(store)
    ledger = InMemoryUsageLedger()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=0),
        metadata_store=store,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    records = ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id)
    assert len(records) == 0


# ── Cancellation tests ────────────────────────────────────────────────────────


def test_cancellation_before_first_chunk_raises_fatal_error():
    """If the job is already CANCELLED before transcription starts, raise immediately."""
    store = _make_store()
    job = _make_job(store, status=JobStatus.RUNNING)
    # Mark the job as cancelled in the store before calling.
    from dataclasses import replace
    cancelled_job = replace(job, status=JobStatus.CANCELLED)
    store.update_job(cancelled_job)

    transcriber = _fake_transcriber()
    with pytest.raises(FatalJobError, match="cancel"):
        run_transcription_and_reconciliation(
            job,
            _build_manifest(num_chunks=2),
            metadata_store=store,
            artifact_repo=_FakeArtifactRepo(),
            usage_ledger=InMemoryUsageLedger(),
            chunk_transcriber_fn=transcriber,
            reconciler_fn=_trivial_reconciler,
        )
    # No chunks should have been transcribed.
    assert transcriber.call_count() == 0


def test_cancellation_between_chunks_stops_loop():
    """After chunk 1 is transcribed, cancelling before chunk 2 raises FatalJobError."""
    store = _make_store()
    job = _make_job(store, status=JobStatus.RUNNING)
    call_log: list[int] = []

    from dataclasses import replace

    def _cancelling_transcriber(chunk: AudioChunk) -> TranscriptionResult:
        call_log.append(chunk.chunk_index)
        # Simulate external cancellation after processing chunk 1.
        if chunk.chunk_index == 1:
            cancelled = replace(job, status=JobStatus.CANCELLED)
            store.update_job(cancelled)
        return TranscriptionResult(
            text=f"chunk {chunk.chunk_index} text",
            model_id="fake",
            actual_usage={"cost_usd": 0.0},
        )

    with pytest.raises(FatalJobError, match="cancel"):
        run_transcription_and_reconciliation(
            job,
            _build_manifest(num_chunks=3),
            metadata_store=store,
            artifact_repo=_FakeArtifactRepo(),
            usage_ledger=InMemoryUsageLedger(),
            chunk_transcriber_fn=_cancelling_transcriber,
            reconciler_fn=_trivial_reconciler,
        )
    # Chunk 1 was transcribed; chunk 2 was stopped before transcription.
    assert call_log == [1]


# ── Zero-chunk edge case ──────────────────────────────────────────────────────


def test_zero_chunks_saves_empty_artifacts():
    """Zero chunks produces valid (empty) raw + reconciled artifacts."""
    store = _make_store()
    job = _make_job(store)
    repo = _FakeArtifactRepo()
    run_transcription_and_reconciliation(
        job,
        _build_manifest(num_chunks=0),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_fake_transcriber(),
        reconciler_fn=_trivial_reconciler,
    )
    assert len(repo.saved) == 2
    raw_saved = next(s for s in repo.saved if s["stage"] == ArtifactStage.TRANSCRIBER)
    assert raw_saved["payload"]["chunks"] == []
