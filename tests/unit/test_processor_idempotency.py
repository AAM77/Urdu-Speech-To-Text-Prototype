"""Tests for processor idempotency and retry safety — Step 5.3.1.

Design under test
─────────────────
Processor stage functions are idempotent: if the artifact for a stage already
exists in the artifact repository, the stage is skipped (no re-run, no
duplicate artifact, no duplicate usage charge).

Functions covered
─────────────────
* ``find_stage_artifact(refs, stage, artifact_type)``
    Returns the first ``ArtifactReference`` matching ``stage`` and
    ``artifact_type``, or ``None`` if not found.

* ``stage_usage_key(run_id, stage_label, item_id=None)``
    Returns a deterministic string idempotency key for a usage record.

* ``run_chunker_stage`` (updated) — skips and returns existing artifact ref
  when a CHUNK_MANIFEST already exists for the run.

* ``run_transcription_and_reconciliation`` (updated) — skips the full stage
  when RAW_URDU_TRANSCRIPT + RECONCILED_URDU_TRANSCRIPT already exist.

* ``run_translation_and_article`` (updated) — skips when ENGLISH_TRANSLATION +
  FINAL_ARTICLE already exist.

* ``InMemoryUsageLedger.record_usage`` (updated) — when a ``UsageRecord`` has
  a non-``None`` ``idempotency_key``, a second call with the same key is a
  no-op (usage not doubled).

Crash-and-retry scenarios
──────────────────────────
Each test simulates a processor crash at a specific stage boundary, then retries
from the beginning.  After a retry:

* Each already-completed stage's artifact must appear exactly once.
* Usage must be charged exactly once per stage/chunk.
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
from urdu_pipeline.application.ports.storage import ArtifactReference
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
    InMemoryBudgetService,
    InMemoryMetadataStore,
    InMemoryObjectStore,
    InMemoryUsageLedger,
)
from urdu_pipeline.processor.idempotency import find_stage_artifact, stage_usage_key


# ── Shared test fixtures ──────────────────────────────────────────────────────


def _uid() -> UserId:
    return UserId.new()


def _rid() -> RunId:
    return RunId.new()


def _job(user_id: UserId | None = None, run_id: RunId | None = None) -> JobRecord:
    return JobRecord(
        user_id=user_id or _uid(),
        run_id=run_id or _rid(),
        job_id=JobId.new(),
        status=JobStatus.RUNNING,
    )


def _ref(
    *,
    user_id: UserId,
    run_id: RunId,
    stage: ArtifactStage,
    artifact_type: ArtifactType,
) -> ArtifactReference:
    return ArtifactReference(
        user_id=user_id,
        run_id=run_id,
        stage=stage,
        artifact_type=artifact_type,
        artifact_id=ArtifactId.new(),
    )


# ── Listing artifact repository ───────────────────────────────────────────────


@dataclass
class _ListingRepo:
    """ArtifactRepository that records saves and supports list_run_artifacts."""

    _artifacts: dict[tuple, ArtifactReference] = field(default_factory=dict)
    save_count: int = 0

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
        self.save_count += 1
        ref = ArtifactReference(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )
        self._artifacts[(user_id, run_id, stage, artifact_type)] = ref
        return ref

    def list_run_artifacts(
        self,
        *,
        user_id: UserId,
        run_id: RunId,
    ) -> Sequence[ArtifactReference]:
        return [
            ref
            for (uid, rid, _s, _t), ref in self._artifacts.items()
            if uid == user_id and rid == run_id
        ]

    def get_artifact_metadata(self, *, user_id, artifact_id):  # pragma: no cover
        raise NotImplementedError

    def load_artifact(self, *, user_id, artifact_id, artifact_format):  # pragma: no cover
        raise NotImplementedError


# ── find_stage_artifact ───────────────────────────────────────────────────────


def test_find_stage_artifact_returns_matching_ref():
    uid, rid = _uid(), _rid()
    refs = [
        _ref(user_id=uid, run_id=rid, stage=ArtifactStage.CHUNKER, artifact_type=ArtifactType.CHUNK_MANIFEST),
        _ref(user_id=uid, run_id=rid, stage=ArtifactStage.TRANSCRIBER, artifact_type=ArtifactType.RAW_URDU_TRANSCRIPT),
    ]
    found = find_stage_artifact(refs, ArtifactStage.CHUNKER, ArtifactType.CHUNK_MANIFEST)
    assert found is not None
    assert found.stage == ArtifactStage.CHUNKER


def test_find_stage_artifact_returns_none_when_not_found():
    uid, rid = _uid(), _rid()
    refs = [
        _ref(user_id=uid, run_id=rid, stage=ArtifactStage.CHUNKER, artifact_type=ArtifactType.CHUNK_MANIFEST),
    ]
    found = find_stage_artifact(refs, ArtifactStage.TRANSCRIBER, ArtifactType.RAW_URDU_TRANSCRIPT)
    assert found is None


def test_find_stage_artifact_returns_none_on_empty_list():
    assert find_stage_artifact([], ArtifactStage.CHUNKER, ArtifactType.CHUNK_MANIFEST) is None


# ── stage_usage_key ───────────────────────────────────────────────────────────


def test_stage_usage_key_is_deterministic():
    run_id = _rid()
    k1 = stage_usage_key(run_id, "transcriber", "chunk_0001")
    k2 = stage_usage_key(run_id, "transcriber", "chunk_0001")
    assert k1 == k2


def test_stage_usage_key_differs_for_different_chunks():
    run_id = _rid()
    k1 = stage_usage_key(run_id, "transcriber", "chunk_0001")
    k2 = stage_usage_key(run_id, "transcriber", "chunk_0002")
    assert k1 != k2


def test_stage_usage_key_differs_for_different_stages():
    run_id = _rid()
    k1 = stage_usage_key(run_id, "translator", None)
    k2 = stage_usage_key(run_id, "article_generator", None)
    assert k1 != k2


# ── UsageRecord idempotency key ───────────────────────────────────────────────


def test_usage_record_accepts_idempotency_key():
    record = UsageRecord(
        provider_run_id=ProviderRunId.new(),
        user_id=_uid(),
        run_id=_rid(),
        job_id=JobId.new(),
        provider_name="fake",
        model_id="m1",
        cost_usd=0.01,
        idempotency_key="run_abc:transcriber:chunk_0001",
    )
    assert record.idempotency_key == "run_abc:transcriber:chunk_0001"


def test_usage_record_idempotency_key_defaults_to_none():
    record = UsageRecord(
        provider_run_id=ProviderRunId.new(),
        user_id=_uid(),
        run_id=_rid(),
        job_id=JobId.new(),
        provider_name="fake",
        model_id="m1",
        cost_usd=0.01,
    )
    assert record.idempotency_key is None


def test_ledger_deduplicates_usage_record_with_same_key():
    ledger = InMemoryUsageLedger()
    uid, rid = _uid(), _rid()
    job_id = JobId.new()
    key = "run:stage:chunk_0001"

    def _record(cost: float) -> None:
        ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=uid,
                run_id=rid,
                job_id=job_id,
                provider_name="fake",
                model_id="m1",
                cost_usd=cost,
                idempotency_key=key,
            )
        )

    _record(0.10)  # first call
    _record(0.10)  # duplicate — must be dropped
    records = ledger.list_run_usage(user_id=uid, run_id=rid)
    assert len(records) == 1
    assert abs(records[0].cost_usd - 0.10) < 1e-9


def test_ledger_allows_different_idempotency_keys():
    ledger = InMemoryUsageLedger()
    uid, rid = _uid(), _rid()
    job_id = JobId.new()

    for i in range(3):
        ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=uid,
                run_id=rid,
                job_id=job_id,
                provider_name="fake",
                model_id="m1",
                cost_usd=0.01,
                idempotency_key=f"key_{i}",
            )
        )
    assert len(ledger.list_run_usage(user_id=uid, run_id=rid)) == 3


def test_ledger_without_key_always_records():
    ledger = InMemoryUsageLedger()
    uid, rid = _uid(), _rid()
    job_id = JobId.new()

    for _ in range(3):
        ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=uid,
                run_id=rid,
                job_id=job_id,
                provider_name="fake",
                model_id="m1",
                cost_usd=0.01,
            )
        )
    assert len(ledger.list_run_usage(user_id=uid, run_id=rid)) == 3


# ── run_chunker_stage idempotency ─────────────────────────────────────────────


def test_chunker_skips_when_artifact_already_exists():
    """If CHUNK_MANIFEST already in repo, run_chunker_stage returns it without re-running."""
    import tempfile
    from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace
    from urdu_pipeline.processor.chunker import run_chunker_stage
    from urdu_pipeline.schemas.chunks import AudioChunk, ChunkManifestArtifact
    from urdu_pipeline.schemas.manifests import ArtifactManifest

    job = _job()
    repo = _ListingRepo()
    existing_ref = _ref(
        user_id=job.user_id,
        run_id=job.run_id,
        stage=ArtifactStage.CHUNKER,
        artifact_type=ArtifactType.CHUNK_MANIFEST,
    )
    repo._artifacts[(job.user_id, job.run_id, ArtifactStage.CHUNKER, ArtifactType.CHUNK_MANIFEST)] = existing_ref
    repo.save_count = 0  # reset after pre-loading

    chunker_called = []

    def _chunker(audio_path):
        chunker_called.append(True)
        return _empty_chunk_manifest()

    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        result = run_chunker_stage(
            job,
            Path(tmp) / "audio.mp3",
            workspace=workspace,
            object_store=InMemoryObjectStore(),
            artifact_repo=repo,
            chunker_fn=_chunker,
        )

    assert result.artifact_id == existing_ref.artifact_id
    assert not chunker_called, "chunker_fn must not be called when artifact exists"
    assert repo.save_count == 0


def test_chunker_does_not_duplicate_on_retry():
    """Calling run_chunker_stage twice results in only 1 saved artifact."""
    import tempfile
    from urdu_pipeline.infrastructure.filesystem import FilesystemRunWorkspace
    from urdu_pipeline.processor.chunker import run_chunker_stage

    job = _job()
    repo = _ListingRepo()

    def _chunker_fn(audio_path):
        chunks_dir = audio_path.parent.parent / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        return _empty_chunk_manifest()

    with tempfile.TemporaryDirectory() as tmp:
        workspace = FilesystemRunWorkspace(Path(tmp))
        run_chunker_stage(
            job, Path(tmp) / "audio.mp3",
            workspace=workspace, object_store=InMemoryObjectStore(),
            artifact_repo=repo, chunker_fn=_chunker_fn,
        )
        run_chunker_stage(
            job, Path(tmp) / "audio.mp3",
            workspace=workspace, object_store=InMemoryObjectStore(),
            artifact_repo=repo, chunker_fn=_chunker_fn,
        )

    assert repo.save_count == 1


# ── run_transcription_and_reconciliation idempotency ─────────────────────────


def test_transcription_skips_when_both_artifacts_exist():
    from urdu_pipeline.processor.transcriber import run_transcription_and_reconciliation

    job = _job()
    repo = _ListingRepo()
    raw_ref = _ref(user_id=job.user_id, run_id=job.run_id, stage=ArtifactStage.TRANSCRIBER, artifact_type=ArtifactType.RAW_URDU_TRANSCRIPT)
    rec_ref = _ref(user_id=job.user_id, run_id=job.run_id, stage=ArtifactStage.TRANSCRIPT_RECONCILER, artifact_type=ArtifactType.RECONCILED_URDU_TRANSCRIPT)
    repo._artifacts[(job.user_id, job.run_id, ArtifactStage.TRANSCRIBER, ArtifactType.RAW_URDU_TRANSCRIPT)] = raw_ref
    repo._artifacts[(job.user_id, job.run_id, ArtifactStage.TRANSCRIPT_RECONCILER, ArtifactType.RECONCILED_URDU_TRANSCRIPT)] = rec_ref
    repo.save_count = 0

    store = _make_store_with_job(job)
    called = []

    def _tx(chunk):
        called.append(True)
        from urdu_pipeline.providers.base import TranscriptionResult
        return TranscriptionResult(text="x", model_id="m", actual_usage={})

    returned_raw, returned_rec = run_transcription_and_reconciliation(
        job, _empty_chunk_manifest(),
        metadata_store=store,
        artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_tx,
        reconciler_fn=lambda r: r,  # never reached
    )
    assert returned_raw.artifact_id == raw_ref.artifact_id
    assert returned_rec.artifact_id == rec_ref.artifact_id
    assert not called
    assert repo.save_count == 0


def test_transcription_does_not_duplicate_on_retry():
    from urdu_pipeline.processor.transcriber import run_transcription_and_reconciliation
    from urdu_pipeline.schemas.transcripts import (
        ReconciledSegment,
        ReconciledTranscriptArtifact,
    )
    from urdu_pipeline.schemas.manifests import ArtifactManifest

    job = _job()
    repo = _ListingRepo()
    store = _make_store_with_job(job)

    def _tx(chunk):
        from urdu_pipeline.providers.base import TranscriptionResult
        return TranscriptionResult(text="text", model_id="m", actual_usage={"cost_usd": 0.01})

    def _reconcile(raw):
        manifest = ArtifactManifest(
            artifact_id=f"rec_{uuid.uuid4().hex[:8]}",
            stage_name="transcript_reconciler",
            artifact_type="reconciled_urdu_transcript",
            source_input_hash="x",
            upstream_artifact_ids=[raw.manifest.artifact_id],
            model_provider="det",
            model_id="rap",
            prompt_id="reconciliation",
            prompt_version="v1",
            cache_hit=False,
        )
        return ReconciledTranscriptArtifact(
            source_audio_hash="x",
            raw_transcript_artifact_id=raw.manifest.artifact_id,
            segments=[],
            full_text_urdu="",
            manifest=manifest,
        )

    run_transcription_and_reconciliation(
        job, _empty_chunk_manifest(),
        metadata_store=store, artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_tx, reconciler_fn=_reconcile,
    )
    run_transcription_and_reconciliation(
        job, _empty_chunk_manifest(),
        metadata_store=store, artifact_repo=repo,
        usage_ledger=InMemoryUsageLedger(),
        chunk_transcriber_fn=_tx, reconciler_fn=_reconcile,
    )
    assert repo.save_count == 2  # raw + reconciled, NOT 4


# ── run_translation_and_article idempotency ────────────────────────────────────


def test_pipeline_skips_when_both_artifacts_exist():
    from urdu_pipeline.processor.pipeline import run_translation_and_article

    job = _job()
    repo = _ListingRepo()
    t_ref = _ref(user_id=job.user_id, run_id=job.run_id, stage=ArtifactStage.TRANSLATOR, artifact_type=ArtifactType.ENGLISH_TRANSLATION)
    a_ref = _ref(user_id=job.user_id, run_id=job.run_id, stage=ArtifactStage.ARTICLE_GENERATOR, artifact_type=ArtifactType.FINAL_ARTICLE)
    repo._artifacts[(job.user_id, job.run_id, ArtifactStage.TRANSLATOR, ArtifactType.ENGLISH_TRANSLATION)] = t_ref
    repo._artifacts[(job.user_id, job.run_id, ArtifactStage.ARTICLE_GENERATOR, ArtifactType.FINAL_ARTICLE)] = a_ref
    repo.save_count = 0

    ledger = InMemoryUsageLedger()
    called = []

    def _tx(rec):
        called.append("translate")
        return None, {}

    def _art(tr):
        called.append("article")
        return None, {}

    returned_t, returned_a = run_translation_and_article(
        job, _empty_reconciled(),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=InMemoryBudgetService(usage_ledger=ledger, hard_cap_usd=1000.0),
        translator_fn=_tx,
        article_fn=_art,
    )
    assert returned_t.artifact_id == t_ref.artifact_id
    assert returned_a.artifact_id == a_ref.artifact_id
    assert not called
    assert repo.save_count == 0


def test_pipeline_does_not_duplicate_on_retry():
    from urdu_pipeline.processor.pipeline import run_translation_and_article
    from tests.unit.test_processor_pipeline import _fake_translator, _fake_article_fn

    job = _job()
    repo = _ListingRepo()
    ledger = InMemoryUsageLedger()
    budget = InMemoryBudgetService(usage_ledger=ledger, hard_cap_usd=1000.0)

    run_translation_and_article(
        job, _empty_reconciled(),
        artifact_repo=repo, usage_ledger=ledger, budget_service=budget,
        translator_fn=_fake_translator, article_fn=_fake_article_fn,
    )
    run_translation_and_article(
        job, _empty_reconciled(),
        artifact_repo=repo, usage_ledger=ledger, budget_service=budget,
        translator_fn=_fake_translator, article_fn=_fake_article_fn,
    )
    assert repo.save_count == 2  # translation + article, NOT 4


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_store_with_job(job: JobRecord) -> InMemoryMetadataStore:
    store = InMemoryMetadataStore()
    store.create_user(UserRecord(user_id=job.user_id, username="u", status=UserStatus.ACTIVE))
    store.create_run(RunRecord(user_id=job.user_id, run_id=job.run_id, status=RunStatus.RUNNING))
    store.create_job(job)
    return store


def _empty_chunk_manifest():
    from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
    from urdu_pipeline.schemas.manifests import ArtifactManifest

    manifest = ArtifactManifest(
        artifact_id=f"cm_{uuid.uuid4().hex[:8]}",
        stage_name="chunker",
        artifact_type="chunk_manifest",
        source_input_hash="x",
        chunk_length_seconds=10,
        overlap_seconds=0,
        cache_hit=False,
    )
    return ChunkManifestArtifact(
        source_audio_path="input/a.mp3",
        source_audio_hash="x",
        source_audio_duration_ms=0,
        source_audio_format="mp3",
        chunk_length_seconds=10,
        overlap_seconds=0,
        chunks=[],
        manifest=manifest,
    )


def _empty_reconciled():
    from urdu_pipeline.schemas.transcripts import ReconciledTranscriptArtifact
    from urdu_pipeline.schemas.manifests import ArtifactManifest

    manifest = ArtifactManifest(
        artifact_id=f"rec_{uuid.uuid4().hex[:8]}",
        stage_name="transcript_reconciler",
        artifact_type="reconciled_urdu_transcript",
        source_input_hash="x",
        model_provider="det",
        model_id="rap",
        prompt_id="reconciliation",
        prompt_version="v1",
        cache_hit=False,
    )
    return ReconciledTranscriptArtifact(
        source_audio_hash="x",
        raw_transcript_artifact_id=manifest.artifact_id,
        segments=[],
        full_text_urdu="",
        manifest=manifest,
    )
