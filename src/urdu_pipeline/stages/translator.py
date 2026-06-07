"""Translation stage: Urdu transcript -> American English."""

from __future__ import annotations

import re
import uuid

from urdu_pipeline.artifacts.store import ArtifactStore, compute_text_checksum
from urdu_pipeline.application.ports import (
    ArtifactSink,
    CacheScope,
    CacheStore,
    ProviderRegistry,
    UsageLedger,
    UsageRecord,
)
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.cache.cache_keys import build_cache_key
from urdu_pipeline.config.model_roles import get_model_roles
from urdu_pipeline.config.pricing import MissingPricingError, get_pricing_table
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.costs.budget_guard import BudgetGuard
from urdu_pipeline.costs.estimator import estimate_text_cost, rough_token_count
from urdu_pipeline.domain import JobId, ProviderRunId, RunId, UserId
from urdu_pipeline.infrastructure.filesystem import FilesystemArtifactSink
from urdu_pipeline.logging_utils import get_logger
from urdu_pipeline.prompts import load_glossary, load_prompt
from urdu_pipeline.providers.base import TextGenerationProvider
from urdu_pipeline.providers.fake_provider import FakeTextGenerationProvider
from urdu_pipeline.providers.requests import (
    ProviderPromptMetadata,
    ProviderSourceData,
    TextGenerationRequest,
)
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import ReconciledTranscriptArtifact
from urdu_pipeline.schemas.translations import (
    EnglishTranslationArtifact,
    EnglishTranslationSegment,
)

_LOGGER = get_logger("stages.translator")
_UNCERTAIN_RE = re.compile(r"\[unclear\]|\[غیر واضح\]")


def _build_provider(settings: Settings) -> TextGenerationProvider:
    if settings.pipeline_provider_mode == "real":
        settings.require_real_provider_ready()
        from urdu_pipeline.providers.openai_text import OpenAITextProvider

        return OpenAITextProvider()
    return FakeTextGenerationProvider()


def _render_instructions(template: str, glossary_text: str) -> str:
    return (
        template.replace("{{GLOSSARY}}", glossary_text.strip())
        .replace("{{SOURCE_TEXT}}", "[Urdu source data is supplied separately.]")
    )


def _usage_cost_usd(usage: dict[str, object]) -> float:
    for key in ("cost_usd", "total_cost_usd", "estimated_cost_usd"):
        value = usage.get(key)
        if value is None:
            continue
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


class TranslatorStage:
    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        artifact_sink: ArtifactSink | None = None,
        provider: TextGenerationProvider | None = None,
        settings: Settings | None = None,
        cache: ArtifactCache | CacheStore | None = None,
        cache_scope: CacheScope | None = None,
        budget_guard: BudgetGuard | None = None,
        provider_registry: ProviderRegistry | None = None,
        usage_ledger: UsageLedger | None = None,
        user_id: UserId | None = None,
        run_id: RunId | None = None,
        job_id: JobId | None = None,
    ) -> None:
        if store is None and artifact_sink is None:
            raise ValueError("TranslatorStage requires an ArtifactStore or ArtifactSink.")
        if usage_ledger is not None and (
            user_id is None or run_id is None or job_id is None
        ):
            raise ValueError(
                "UsageLedger recording requires user_id, run_id, and job_id."
            )
        self.store = store
        self.artifact_sink = artifact_sink or FilesystemArtifactSink(store)
        self.settings = settings or get_settings()
        self.provider = provider or _build_provider(self.settings)
        self.cache: ArtifactCache | None = None
        self.cache_store: CacheStore | None = None
        if cache is None:
            self.cache = ArtifactCache(settings=self.settings)
        elif hasattr(cache, "lookup") and hasattr(cache, "store"):
            self.cache = cache
        else:
            self.cache_store = cache
        self.cache_scope = cache_scope
        if self.cache_store is not None and self.cache_scope is None:
            if user_id is None:
                raise ValueError("CacheStore requires cache_scope or user_id.")
            self.cache_scope = CacheScope(user_id=user_id, name="translator")
        self.budget_guard = budget_guard
        self.provider_registry = provider_registry
        self.provider_config = (
            provider_registry.get_active_config() if provider_registry is not None else None
        )
        fallback_model_id = get_model_roles(self.settings).for_role("translation")
        self.model_id = (
            self.provider_config.model_roles.get("translation", fallback_model_id)
            if self.provider_config is not None
            else fallback_model_id
        )
        self.prompt_version = (
            self.provider_config.prompt_versions.get(
                "translation",
                self.settings.prompt_version,
            )
            if self.provider_config is not None
            else self.settings.prompt_version
        )
        self.usage_ledger = usage_ledger
        self.user_id = user_id
        self.run_id = run_id
        self.job_id = job_id
        self._template = load_prompt("translation", self.prompt_version)
        self._glossary = load_glossary()

    # ------------------------------------------------------------------
    def run(self, reconciled: ReconciledTranscriptArtifact) -> EnglishTranslationArtifact:
        source = reconciled.full_text_urdu
        warnings: list[str] = []

        # Cost estimate (best-effort).
        estimated_cost = 0.0
        try:
            estimated_cost = estimate_text_cost(
                input_text=source,
                model_id=self.model_id,
                expected_output_tokens=rough_token_count(source),
                pricing=get_pricing_table(),
            ).estimated_cost_usd
        except MissingPricingError as e:
            if self.settings.pipeline_provider_mode == "real":
                raise
            warnings.append(f"pricing-missing: {e}")

        if self.budget_guard is not None and self.settings.pipeline_provider_mode == "real":
            self.budget_guard.must_check(estimated_cost)

        cache_key = build_cache_key(
            input_hash=compute_text_checksum(source),
            stage_name="translator",
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            context_mode="reconciled_full_text",
        )
        cache_hit, cached_payload = self._cache_lookup(cache_key)
        if cache_hit and cached_payload:
            text_english = str(cached_payload.get("text", ""))
        else:
            request = TextGenerationRequest(
                model_id=self.model_id,
                developer_instructions=_render_instructions(
                    self._template,
                    self._glossary,
                ),
                schema_instructions="Return only the final translated text in Markdown.",
                source_data=ProviderSourceData.from_text(
                    source,
                    metadata={
                        "source_artifact_id": reconciled.manifest.artifact_id,
                        "source_language": "ur",
                    },
                ),
                prompt_metadata=ProviderPromptMetadata(
                    stage_name="translator",
                    prompt_id="translation",
                    prompt_version=self.prompt_version,
                    model_provider=self.provider.name,
                    extra={
                        "provider_config_version_id": str(
                            self.provider_config.config_version_id
                        )
                        if self.provider_config is not None
                        else None
                    },
                ),
            )
            result = self.provider.generate(request)
            text_english = result.text
            self._cache_store(
                cache_key,
                {
                    "text": text_english,
                    "model_id": self.model_id,
                    "provider": self.provider.name,
                },
            )
            self._record_usage(result)

        # One-segment-per-source-segment mapping in this prototype: the model
        # returns the full translation, and we attribute it to the union of all
        # source segment ids. Future versions can split per-segment.
        segments: list[EnglishTranslationSegment] = []
        if reconciled.segments:
            segments.append(
                EnglishTranslationSegment(
                    segment_id="english_segment_0001",
                    source_segment_id=reconciled.segments[0].segment_id,
                    text_english=text_english,
                    preserved_uncertainty=bool(_UNCERTAIN_RE.search(text_english)),
                    terminology_notes=[],
                )
            )
        else:
            segments.append(
                EnglishTranslationSegment(
                    segment_id="english_segment_0001",
                    source_segment_id="reconciled_root",
                    text_english=text_english,
                    preserved_uncertainty=bool(_UNCERTAIN_RE.search(text_english)),
                )
            )

        manifest = ArtifactManifest(
            artifact_id=f"english_translation_{uuid.uuid4().hex[:12]}",
            stage_name="translator",
            artifact_type="english_translation",
            source_input_hash=compute_text_checksum(source),
            upstream_artifact_ids=[reconciled.manifest.artifact_id],
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_id="translation",
            prompt_version=self.prompt_version,
            context_mode="reconciled_full_text",
            estimated_cost_usd=estimated_cost,
            cache_hit=cache_hit,
            checksum=compute_text_checksum(text_english),
            warnings=warnings,
        )

        artifact = EnglishTranslationArtifact(
            reconciled_transcript_artifact_id=reconciled.manifest.artifact_id,
            segments=segments,
            full_text_english=text_english,
            manifest=manifest,
        )
        self.artifact_sink.write_artifact(artifact, "english_translation.json")
        self.artifact_sink.write_markdown(_to_markdown(artifact), "english_translation.md")
        return artifact

    def _cache_lookup(self, cache_key: str) -> tuple[bool, dict[str, object] | None]:
        if self.cache_store is not None:
            entry = self.cache_store.get(self.cache_scope, cache_key)
            if entry is None:
                return False, None
            return True, dict(entry.payload)
        cached = self.cache.lookup(cache_key)
        return cached.hit, cached.payload

    def _cache_store(self, cache_key: str, payload: dict[str, object]) -> None:
        if self.cache_store is not None:
            self.cache_store.put(self.cache_scope, cache_key, payload)
            return
        self.cache.store(cache_key, payload)

    def _record_usage(self, result) -> None:
        if self.usage_ledger is None:
            return
        usage = dict(result.actual_usage)
        self.usage_ledger.record_usage(
            UsageRecord(
                provider_run_id=ProviderRunId.new(),
                user_id=self.user_id,
                run_id=self.run_id,
                job_id=self.job_id,
                provider_name=self.provider.name,
                model_id=result.model_id,
                cost_usd=_usage_cost_usd(usage),
                usage=usage,
            )
        )


def _to_markdown(artifact: EnglishTranslationArtifact) -> str:
    lines = [
        "# American English Translation",
        "",
        f"- Model: `{artifact.manifest.model_id}` (provider: `{artifact.manifest.model_provider}`)",
        "",
        artifact.full_text_english,
        "",
    ]
    return "\n".join(lines)


def run_translator_stage(
    *,
    reconciled: ReconciledTranscriptArtifact,
    store: ArtifactStore,
    provider: TextGenerationProvider | None = None,
    settings: Settings | None = None,
    cache: ArtifactCache | None = None,
    budget_guard: BudgetGuard | None = None,
) -> EnglishTranslationArtifact:
    return TranslatorStage(
        store=store,
        provider=provider,
        settings=settings,
        cache=cache,
        budget_guard=budget_guard,
    ).run(reconciled)
