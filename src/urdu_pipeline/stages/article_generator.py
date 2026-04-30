"""Article generation stage: English translation -> standalone American English article."""

from __future__ import annotations

import json
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
from urdu_pipeline.prompts import load_prompt
from urdu_pipeline.providers.base import TextGenerationProvider
from urdu_pipeline.providers.fake_provider import FakeTextGenerationProvider
from urdu_pipeline.schemas.articles import Article, ArticleArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.translations import EnglishTranslationArtifact

_LOGGER = get_logger("stages.article_generator")


def _build_provider(settings: Settings) -> TextGenerationProvider:
    if settings.pipeline_provider_mode == "real":
        settings.require_real_provider_ready()
        from urdu_pipeline.providers.openai_text import OpenAITextProvider

        return OpenAITextProvider()
    return FakeTextGenerationProvider()


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_article_payload(text: str) -> Article:
    """Try to parse the model's response as JSON; fall back to plain Markdown."""
    candidate = text.strip()
    # Strip Markdown code fences if present.
    m = _FENCED_JSON_RE.search(candidate)
    if m:
        candidate = m.group(1)

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Fallback: treat the whole response as the body, fabricate a title.
        first_line = next((l for l in text.splitlines() if l.strip()), "Untitled")
        title = first_line.lstrip("#").strip()[:120] or "Untitled"
        return Article(
            title=title,
            subtitle=None,
            body_markdown=text.strip(),
            warnings=["model_did_not_return_json"],
        )

    return Article(
        title=str(data.get("title") or "Untitled"),
        subtitle=(str(data["subtitle"]) if data.get("subtitle") else None),
        body_markdown=str(data.get("body_markdown") or ""),
        warnings=list(data.get("warnings") or []),
    )


class ArticleGeneratorStage:
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
        self.model_id = get_model_roles(self.settings).for_role("article")
        self.prompt_version = self.settings.prompt_version
        self._template = load_prompt("article", self.prompt_version)

    def run(self, translation: EnglishTranslationArtifact) -> ArticleArtifact:
        source = translation.full_text_english
        warnings: list[str] = []

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
            stage_name="article_generator",
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            context_mode="full_translation",
        )
        cached = self.cache.lookup(cache_key)
        if cached.hit and cached.payload:
            article = Article.model_validate(cached.payload["article"])
        else:
            prompt = self._template.replace("{{SOURCE_TEXT}}", source)
            result = self.provider.generate(
                prompt=prompt,
                input_text="",
                model_id=self.model_id,
            )
            article = _parse_article_payload(result.text)
            self.cache.store(
                cache_key,
                {
                    "article": article.model_dump(mode="json"),
                    "model_id": self.model_id,
                    "provider": self.provider.name,
                },
            )

        manifest = ArtifactManifest(
            artifact_id=f"final_article_{uuid.uuid4().hex[:12]}",
            stage_name="article_generator",
            artifact_type="final_article",
            source_input_hash=compute_text_checksum(source),
            upstream_artifact_ids=[translation.manifest.artifact_id],
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_id="article",
            prompt_version=self.prompt_version,
            context_mode="full_translation",
            estimated_cost_usd=estimated_cost,
            cache_hit=cached.hit,
            checksum=compute_text_checksum(article.body_markdown),
            warnings=warnings + article.warnings,
        )
        artifact = ArticleArtifact(
            source_translation_artifact_id=translation.manifest.artifact_id,
            article=article,
            manifest=manifest,
        )
        self.store.write_artifact(artifact, "final_article.json")
        self.store.write_markdown(_to_markdown(article), "final_article.md")
        return artifact


def _to_markdown(article: Article) -> str:
    parts = [f"# {article.title}", ""]
    if article.subtitle:
        parts += [f"_{article.subtitle}_", ""]
    parts.append(article.body_markdown.strip())
    parts.append("")
    return "\n".join(parts)


def run_article_stage(
    *,
    translation: EnglishTranslationArtifact,
    store: ArtifactStore,
    provider: TextGenerationProvider | None = None,
    settings: Settings | None = None,
    cache: ArtifactCache | None = None,
    budget_guard: BudgetGuard | None = None,
) -> ArticleArtifact:
    return ArticleGeneratorStage(
        store=store,
        provider=provider,
        settings=settings,
        cache=cache,
        budget_guard=budget_guard,
    ).run(translation)
