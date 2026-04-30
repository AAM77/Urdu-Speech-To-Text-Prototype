"""Translation stage: Urdu transcript -> American English."""

from __future__ import annotations

import re
import uuid

from urdu_pipeline.artifacts.store import ArtifactStore, compute_text_checksum
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.cache.cache_keys import build_cache_key
from urdu_pipeline.config.model_roles import get_model_roles
from urdu_pipeline.config.pricing import MissingPricingError, get_pricing_table
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.costs.budget_guard import BudgetGuard
from urdu_pipeline.costs.estimator import estimate_text_cost, rough_token_count
from urdu_pipeline.logging_utils import get_logger
from urdu_pipeline.prompts import load_glossary, load_prompt
from urdu_pipeline.providers.base import TextGenerationProvider
from urdu_pipeline.providers.fake_provider import FakeTextGenerationProvider
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


def _render_prompt(template: str, glossary_text: str, source_text: str) -> str:
    return (
        template
        .replace("{{GLOSSARY}}", glossary_text.strip())
        .replace("{{SOURCE_TEXT}}", source_text)
    )


class TranslatorStage:
    def __init__(
        self,
        *,
        store: ArtifactStore,
        provider: TextGenerationProvider | None = None,
        settings: Settings | None = None,
        cache: ArtifactCache | None = None,
        budget_guard: BudgetGuard | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.provider = provider or _build_provider(self.settings)
        self.cache = cache or ArtifactCache(settings=self.settings)
        self.budget_guard = budget_guard
        self.model_id = get_model_roles(self.settings).for_role("translation")
        self.prompt_version = self.settings.prompt_version
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
        cached = self.cache.lookup(cache_key)
        if cached.hit and cached.payload:
            text_english = cached.payload.get("text", "")
        else:
            prompt = _render_prompt(self._template, self._glossary, source)
            result = self.provider.generate(
                prompt=prompt,
                input_text="",  # the source is already embedded in the prompt
                model_id=self.model_id,
            )
            text_english = result.text
            self.cache.store(
                cache_key,
                {
                    "text": text_english,
                    "model_id": self.model_id,
                    "provider": self.provider.name,
                },
            )

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
            cache_hit=cached.hit,
            checksum=compute_text_checksum(text_english),
            warnings=warnings,
        )

        artifact = EnglishTranslationArtifact(
            reconciled_transcript_artifact_id=reconciled.manifest.artifact_id,
            segments=segments,
            full_text_english=text_english,
            manifest=manifest,
        )
        self.store.write_artifact(artifact, "english_translation.json")
        self.store.write_markdown(_to_markdown(artifact), "english_translation.md")
        return artifact


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
