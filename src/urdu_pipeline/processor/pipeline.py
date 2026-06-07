"""Processor pipeline final stages — Step 5.2.4.

Orchestrates the translation and article-generation steps inside the background
processor:

1. **Budget check before translation**: calls
   ``budget_service.check_run_budget(next_cost_usd=0.0)`` against the
   current ledger total.  Raises ``FatalJobError`` if the hard cap is already
   exceeded — no further work is done.
2. Calls ``translator_fn(reconciled_artifact)``
   → ``(EnglishTranslationArtifact, usage_dict)``.
3. Records translation usage via ``usage_ledger.record_usage``.
4. Persists the translation artifact via ``artifact_repo.save_artifact``
   (``stage=TRANSLATOR``, ``artifact_type=ENGLISH_TRANSLATION``).
5. **Budget check before article generation**: same pattern; now includes the
   translation cost recorded in step 3, so if translation was expensive the
   article stage is blocked before starting.
6. Calls ``article_fn(translation_artifact)``
   → ``(ArticleArtifact, usage_dict)``.
7. Records article usage.
8. Persists the article artifact
   (``stage=ARTICLE_GENERATOR``, ``artifact_type=FINAL_ARTICLE``).
9. Returns ``(translation_ref, article_ref)``.

Injectability
─────────────
``translator_fn`` and ``article_fn`` are plain callables returning
``(artifact, usage_dict)``.  Production wrappers capture workspace/provider/
settings in closures; tests return fakes without any I/O.
"""

from __future__ import annotations

from typing import Any, Callable

from urdu_pipeline.application.ports.services import (
    BudgetService,
    JobRecord,
    UsageLedger,
    UsageRecord,
)
from urdu_pipeline.application.ports.storage import ArtifactReference, ArtifactRepository
from urdu_pipeline.domain import ArtifactId, ArtifactStage, ArtifactType, ProviderRunId
from urdu_pipeline.logging_utils import redact_event_message
from urdu_pipeline.processor.idempotency import find_stage_artifact, stage_usage_key
from urdu_pipeline.processor.lifecycle import FatalJobError, TransientJobError
from urdu_pipeline.providers.base import ProviderFatalError, ProviderTransientError
from urdu_pipeline.schemas.articles import ArticleArtifact
from urdu_pipeline.schemas.transcripts import ReconciledTranscriptArtifact
from urdu_pipeline.schemas.translations import EnglishTranslationArtifact


def _enforce_budget(job_record: JobRecord, budget_service: BudgetService, stage: str) -> None:
    """Raise FatalJobError if the run is already over its hard budget cap."""
    decision = budget_service.check_run_budget(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        next_cost_usd=0.0,
    )
    if decision.blocked:
        raise FatalJobError(
            f"budget hard cap exceeded before {stage}: "
            f"projected={decision.projected_total_usd:.4f} USD, "
            f"cap={decision.hard_cap_usd:.4f} USD."
        )


def _record_stage_usage(
    job_record: JobRecord,
    usage_ledger: UsageLedger,
    usage_dict: dict[str, Any],
    stage_label: str,
    idempotency_key: str | None = None,
) -> None:
    model_id = str(usage_dict.get("model_id", "unknown"))
    cost_usd = float(usage_dict.get("cost_usd", 0.0))
    actual_usage = dict(usage_dict.get("actual_usage", {}))
    usage_ledger.record_usage(
        UsageRecord(
            provider_run_id=ProviderRunId.new(),
            user_id=job_record.user_id,
            run_id=job_record.run_id,
            job_id=job_record.job_id,
            provider_name=str(usage_dict.get("provider_name", stage_label)),
            model_id=model_id,
            cost_usd=cost_usd,
            usage=actual_usage,
            idempotency_key=idempotency_key,
        )
    )


def run_translation_and_article(
    job_record: JobRecord,
    reconciled_artifact: ReconciledTranscriptArtifact,
    *,
    artifact_repo: ArtifactRepository,
    usage_ledger: UsageLedger,
    budget_service: BudgetService,
    translator_fn: Callable[
        [ReconciledTranscriptArtifact],
        tuple[EnglishTranslationArtifact, dict[str, Any]],
    ],
    article_fn: Callable[
        [EnglishTranslationArtifact],
        tuple[ArticleArtifact, dict[str, Any]],
    ],
) -> tuple[ArtifactReference, ArtifactReference]:
    """Run translation then article generation, with per-stage budget guards.

    Parameters
    ──────────
    job_record
        Current job — provides ``user_id``, ``run_id``, ``job_id`` for
        artifact ownership, usage ledger records, and budget checks.
    reconciled_artifact
        Reconciled Urdu transcript (output of the reconciliation stage).
    artifact_repo
        Artifact repository for persisting translation + article artifacts.
    usage_ledger
        Shared usage ledger.  Must be the same instance as the one used by
        ``budget_service`` so that costs recorded here affect subsequent
        budget checks within this call.
    budget_service
        Budget decision service.  Checked (with ``next_cost_usd=0.0``) before
        each stage to gate on the current ledger total.
    translator_fn
        Callable that translates the reconciled transcript and returns
        ``(EnglishTranslationArtifact, usage_dict)``.
    article_fn
        Callable that generates the final article from the translation and
        returns ``(ArticleArtifact, usage_dict)``.

    Returns
    ───────
    ``(translation_ref, article_ref)`` — both ``ArtifactReference`` instances
    returned by the repository.

    Raises
    ──────
    FatalJobError
        If the current run cost already exceeds the hard cap before either stage.
    Any exception from ``translator_fn`` or ``article_fn`` propagates unchanged.
    """
    existing = artifact_repo.list_run_artifacts(
        user_id=job_record.user_id, run_id=job_record.run_id
    )
    t_existing = find_stage_artifact(
        existing, ArtifactStage.TRANSLATOR, ArtifactType.ENGLISH_TRANSLATION
    )
    a_existing = find_stage_artifact(
        existing, ArtifactStage.ARTICLE_GENERATOR, ArtifactType.FINAL_ARTICLE
    )
    if t_existing and a_existing:
        return t_existing, a_existing

    _enforce_budget(job_record, budget_service, stage="translation")

    translation_artifact, translation_usage = _call_provider_stage(
        "translation",
        translator_fn,
        reconciled_artifact,
    )

    _record_stage_usage(
        job_record, usage_ledger, translation_usage,
        stage_label="translator",
        idempotency_key=stage_usage_key(job_record.run_id, "translator"),
    )

    translation_ref = artifact_repo.save_artifact(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        stage=ArtifactStage.TRANSLATOR,
        artifact_type=ArtifactType.ENGLISH_TRANSLATION,
        artifact_id=ArtifactId.new(),
        payload=translation_artifact.model_dump(),
    )

    _enforce_budget(job_record, budget_service, stage="article generation")

    article_artifact, article_usage = _call_provider_stage(
        "article_generation",
        article_fn,
        translation_artifact,
    )

    _record_stage_usage(
        job_record, usage_ledger, article_usage,
        stage_label="article_generator",
        idempotency_key=stage_usage_key(job_record.run_id, "article_generator"),
    )

    article_ref = artifact_repo.save_artifact(
        user_id=job_record.user_id,
        run_id=job_record.run_id,
        stage=ArtifactStage.ARTICLE_GENERATOR,
        artifact_type=ArtifactType.FINAL_ARTICLE,
        artifact_id=ArtifactId.new(),
        payload=article_artifact.model_dump(),
    )

    return translation_ref, article_ref


def _call_provider_stage(stage: str, fn: Callable[[Any], Any], value: Any) -> Any:
    try:
        return fn(value)
    except ProviderTransientError as exc:
        raise TransientJobError(
            _provider_failure_message(
                stage,
                exc,
                fallback="provider transient failure",
            )
        ) from exc
    except ProviderFatalError as exc:
        raise FatalJobError(
            _provider_failure_message(
                stage,
                exc,
                fallback="provider fatal failure",
            )
        ) from exc


def _provider_failure_message(
    stage: str,
    exc: Exception,
    *,
    fallback: str,
) -> str:
    safe_detail = redact_event_message(str(exc), fallback=type(exc).__name__)
    return f"{fallback} during {stage}: {safe_detail}"


__all__ = ["run_translation_and_article"]
