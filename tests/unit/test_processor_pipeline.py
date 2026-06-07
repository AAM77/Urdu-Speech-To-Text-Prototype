"""Tests for processor translation + article-generation pipeline — Step 5.2.4.

Design under test
─────────────────
``run_translation_and_article(
    job_record, reconciled_artifact, *,
    artifact_repo, usage_ledger, budget_service,
    translator_fn, article_fn,
) -> tuple[ArtifactReference, ArtifactReference]``

    Orchestrates the final two pipeline stages inside the background processor:

    1. **Budget check before translation**: call
       ``budget_service.check_run_budget(next_cost_usd=0.0)``.
       If ``decision.blocked`` raise ``FatalJobError``.
    2. Call ``translator_fn(reconciled_artifact)``
       → ``(EnglishTranslationArtifact, dict)``.
    3. Record translation usage via ``usage_ledger.record_usage``.
    4. Persist the translation artifact via ``artifact_repo.save_artifact``
       (stage=TRANSLATOR, artifact_type=ENGLISH_TRANSLATION).
    5. **Budget check before article generation**: same pattern.
    6. Call ``article_fn(translation_artifact)``
       → ``(ArticleArtifact, dict)``.
    7. Record article usage.
    8. Persist the article artifact
       (stage=ARTICLE_GENERATOR, artifact_type=FINAL_ARTICLE).
    9. Return ``(translation_ref, article_ref)``.

Design decisions
────────────────
* Both stage functions return ``(artifact, usage_dict)`` where ``usage_dict``
  carries ``model_id`` and ``cost_usd`` for ledger recording — no filesystem,
  no provider call in this module.
* Budget check uses ``next_cost_usd=0.0`` so it gates on the *current* total:
  if we're already over the hard cap we stop immediately.
* The shared ``usage_ledger`` between the caller's ``InMemoryBudgetService``
  and the ``run_translation_and_article`` call enables the "translation runs
  but article is blocked" scenario.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import pytest

from urdu_pipeline.application.ports.services import (
    BudgetDecision,
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
    InMemoryBudgetService,
    InMemoryMetadataStore,
    InMemoryUsageLedger,
)
from urdu_pipeline.processor.lifecycle import FatalJobError
from urdu_pipeline.processor.pipeline import run_translation_and_article
from urdu_pipeline.schemas.articles import Article, ArticleArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    ReconciledSegment,
    ReconciledTranscriptArtifact,
)
from urdu_pipeline.schemas.translations import (
    EnglishTranslationArtifact,
    EnglishTranslationSegment,
)


# ── Minimal in-test ArtifactRepository ───────────────────────────────────────


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
            }
        )
        return ArtifactReference(
            user_id=user_id,
            run_id=run_id,
            stage=stage,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )

    def get_artifact_metadata(self, *, user_id, artifact_id):  # pragma: no cover
        raise NotImplementedError

    def load_artifact(self, *, user_id, artifact_id, artifact_format):  # pragma: no cover
        raise NotImplementedError

    def list_run_artifacts(self, *, user_id, run_id) -> Sequence[ArtifactReference]:  # pragma: no cover
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_job() -> JobRecord:
    return JobRecord(
        user_id=UserId.new(),
        run_id=RunId.new(),
        job_id=JobId.new(),
        status=JobStatus.RUNNING,
    )


def _make_reconciled(full_text: str = "اردو متن یہاں ہے") -> ReconciledTranscriptArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"reconciled_{uuid.uuid4().hex[:12]}",
        stage_name="transcript_reconciler",
        artifact_type="reconciled_urdu_transcript",
        source_input_hash="abc123",
        model_provider="deterministic",
        model_id="rapidfuzz-overlap",
        prompt_id="reconciliation",
        prompt_version="v1",
        cache_hit=False,
    )
    return ReconciledTranscriptArtifact(
        source_audio_hash="abc123",
        raw_transcript_artifact_id=manifest.artifact_id,
        segments=[
            ReconciledSegment(
                segment_id="seg_0001",
                source_chunk_ids=["chunk_0001"],
                text_urdu=full_text,
            )
        ],
        full_text_urdu=full_text,
        manifest=manifest,
    )


def _make_translation(
    reconciled: ReconciledTranscriptArtifact,
    text: str = "This is a test English translation.",
) -> EnglishTranslationArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"translation_{uuid.uuid4().hex[:12]}",
        stage_name="translator",
        artifact_type="english_translation",
        source_input_hash="abc123",
        model_provider="fake",
        model_id="fake-text-v1",
        prompt_id="translation",
        prompt_version="v1",
        cache_hit=False,
    )
    return EnglishTranslationArtifact(
        reconciled_transcript_artifact_id=reconciled.manifest.artifact_id,
        segments=[
            EnglishTranslationSegment(
                segment_id="eng_seg_0001",
                source_segment_id="seg_0001",
                text_english=text,
            )
        ],
        full_text_english=text,
        manifest=manifest,
    )


def _make_article(translation: EnglishTranslationArtifact) -> ArticleArtifact:
    manifest = ArtifactManifest(
        artifact_id=f"article_{uuid.uuid4().hex[:12]}",
        stage_name="article_generator",
        artifact_type="final_article",
        source_input_hash="abc123",
        model_provider="fake",
        model_id="fake-text-v1",
        prompt_id="article_generator",
        prompt_version="v1",
        cache_hit=False,
    )
    return ArticleArtifact(
        source_translation_artifact_id=translation.manifest.artifact_id,
        article=Article(
            title="Test Article",
            subtitle=None,
            body_markdown="## Test\n\nBody text here.",
        ),
        manifest=manifest,
    )


def _fake_translator(
    reconciled: ReconciledTranscriptArtifact,
) -> tuple[EnglishTranslationArtifact, dict[str, Any]]:
    translation = _make_translation(reconciled)
    usage = {"model_id": "fake-text-v1", "cost_usd": 0.05, "actual_usage": {"tokens": 100}}
    return translation, usage


def _fake_article_fn(
    translation: EnglishTranslationArtifact,
) -> tuple[ArticleArtifact, dict[str, Any]]:
    article = _make_article(translation)
    usage = {"model_id": "fake-text-v1", "cost_usd": 0.03, "actual_usage": {"tokens": 80}}
    return article, usage


def _unlimited_budget(ledger: InMemoryUsageLedger | None = None) -> InMemoryBudgetService:
    return InMemoryBudgetService(
        usage_ledger=ledger or InMemoryUsageLedger(),
        hard_cap_usd=1_000.0,
    )


# ── Return-value tests ────────────────────────────────────────────────────────


def test_run_translation_and_article_returns_two_references():
    job = _make_job()
    reconciled = _make_reconciled()
    ledger = InMemoryUsageLedger()
    t_ref, a_ref = run_translation_and_article(
        job,
        reconciled,
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    assert isinstance(t_ref, ArtifactReference)
    assert isinstance(a_ref, ArtifactReference)


def test_translation_ref_has_translator_stage_and_type():
    job = _make_job()
    ledger = InMemoryUsageLedger()
    t_ref, _ = run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    assert t_ref.stage == ArtifactStage.TRANSLATOR
    assert t_ref.artifact_type == ArtifactType.ENGLISH_TRANSLATION


def test_article_ref_has_article_generator_stage_and_type():
    job = _make_job()
    ledger = InMemoryUsageLedger()
    _, a_ref = run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    assert a_ref.stage == ArtifactStage.ARTICLE_GENERATOR
    assert a_ref.artifact_type == ArtifactType.FINAL_ARTICLE


# ── Artifact persistence tests ────────────────────────────────────────────────


def test_exactly_two_artifacts_saved_to_repo():
    job = _make_job()
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    assert len(repo.saved) == 2


def test_translation_payload_has_full_text_english():
    job = _make_job()
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    t_saved = next(s for s in repo.saved if s["stage"] == ArtifactStage.TRANSLATOR)
    assert "full_text_english" in t_saved["payload"]
    assert t_saved["payload"]["full_text_english"]


def test_article_payload_has_article_field():
    job = _make_job()
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    a_saved = next(s for s in repo.saved if s["stage"] == ArtifactStage.ARTICLE_GENERATOR)
    assert "article" in a_saved["payload"]
    assert a_saved["payload"]["article"]["title"]


def test_artifact_ownership_matches_job():
    job = _make_job()
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    for saved in repo.saved:
        assert saved["user_id"] == job.user_id
        assert saved["run_id"] == job.run_id


# ── Usage ledger tests ────────────────────────────────────────────────────────


def test_two_usage_records_persisted():
    job = _make_job()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    records = ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id)
    assert len(records) == 2


def test_usage_records_have_correct_user_run_job():
    job = _make_job()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(),
        artifact_repo=_FakeArtifactRepo(),
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    for record in ledger.list_run_usage(user_id=job.user_id, run_id=job.run_id):
        assert record.user_id == job.user_id
        assert record.run_id == job.run_id
        assert record.job_id == job.job_id


# ── Budget enforcement tests ──────────────────────────────────────────────────


def test_budget_exceeded_before_translation_raises_fatal_error():
    """If already over budget, translation is blocked before it starts."""
    job = _make_job()
    shared_ledger = InMemoryUsageLedger()
    budget = InMemoryBudgetService(usage_ledger=shared_ledger, hard_cap_usd=0.10)

    # Pre-load cost that exceeds the hard cap.
    shared_ledger.record_usage(
        UsageRecord(
            provider_run_id=ProviderRunId.new(),
            user_id=job.user_id,
            run_id=job.run_id,
            job_id=job.job_id,
            provider_name="fake",
            model_id="fake-asr",
            cost_usd=0.15,
        )
    )

    call_log: list[str] = []

    def _tracking_translator(rec):
        call_log.append("translate")
        return _fake_translator(rec)

    with pytest.raises(FatalJobError, match="budget"):
        run_translation_and_article(
            job,
            _make_reconciled(),
            artifact_repo=_FakeArtifactRepo(),
            usage_ledger=shared_ledger,
            budget_service=budget,
            translator_fn=_tracking_translator,
            article_fn=_fake_article_fn,
        )
    assert "translate" not in call_log, "translator_fn must not be called when budget is blocked"


def test_budget_exceeded_before_article_stops_after_translation():
    """Translation runs OK; article generation is blocked if budget exceeded after translation."""
    job = _make_job()
    shared_ledger = InMemoryUsageLedger()
    budget = InMemoryBudgetService(usage_ledger=shared_ledger, hard_cap_usd=0.10)
    call_log: list[str] = []

    def _costly_translator(reconciled):
        call_log.append("translate")
        artifact = _make_translation(reconciled)
        usage = {"model_id": "fake-text-v1", "cost_usd": 0.12, "actual_usage": {}}
        return artifact, usage

    def _tracking_article(translation):
        call_log.append("article")
        return _fake_article_fn(translation)

    with pytest.raises(FatalJobError, match="budget"):
        run_translation_and_article(
            job,
            _make_reconciled(),
            artifact_repo=_FakeArtifactRepo(),
            usage_ledger=shared_ledger,
            budget_service=budget,
            translator_fn=_costly_translator,
            article_fn=_tracking_article,
        )
    assert "translate" in call_log, "translation should have run"
    assert "article" not in call_log, "article_fn must not be called when budget is blocked"


# ── Prompt-safety: no raw user content in internal keys ───────────────────────


def test_translation_payload_does_not_expose_raw_urdu_as_key():
    """Urdu source text must not appear as a top-level key in the translation payload."""
    raw_urdu = "اردو متن یہاں ہے"
    job = _make_job()
    repo = _FakeArtifactRepo()
    ledger = InMemoryUsageLedger()
    run_translation_and_article(
        job,
        _make_reconciled(full_text=raw_urdu),
        artifact_repo=repo,
        usage_ledger=ledger,
        budget_service=_unlimited_budget(ledger),
        translator_fn=_fake_translator,
        article_fn=_fake_article_fn,
    )
    t_saved = next(s for s in repo.saved if s["stage"] == ArtifactStage.TRANSLATOR)
    for key in t_saved["payload"]:
        assert raw_urdu not in key
