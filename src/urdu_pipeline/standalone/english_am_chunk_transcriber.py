"""Standalone English → American English chunk transcription.

Uses the same layout as ``TranscriberStage``: consumes a chunk manifest produced
by the chunker and writes JSON + Markdown under the run ``artifacts/`` folder.

Artifacts:
  - ``raw_am_english_transcript.json``
  - ``raw_am_english_transcript.md``
"""

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
from urdu_pipeline.costs.estimator import estimate_transcription_cost
from urdu_pipeline.logging_utils import get_logger, safe_log_event
from urdu_pipeline.prompts import load_prompt
from urdu_pipeline.providers.base import AudioTranscriptionProvider
from urdu_pipeline.providers.fake_provider import FakeAudioTranscriptionProvider
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawAmericanEnglishChunk,
    RawAmericanEnglishTranscriptArtifact,
)

_LOGGER = get_logger("standalone.english_am_chunk_transcriber")
_UNCLEAR_RE = re.compile(r"\[unclear\]|\[غیر واضح\]")

PROMPT_ID = "transcription_english_am"
_CACHE_STAGE_LABEL = "english_am_chunk_transcriber"


def _build_provider(settings: Settings) -> AudioTranscriptionProvider:
    if settings.pipeline_provider_mode == "real":
        settings.require_real_provider_ready()
        from urdu_pipeline.providers.openai_audio import OpenAIAudioProvider

        return OpenAIAudioProvider()
    return FakeAudioTranscriptionProvider()


def _previous_chunk_tail(prev_text: str | None, max_chars: int = 600) -> str:
    if not prev_text:
        return ""
    return prev_text[-max_chars:].strip()


def _build_chunk_prompt(base_prompt: str, prev_tail: str) -> str:
    if not prev_tail:
        return base_prompt
    return (
        base_prompt
        + "\n\n## Previous chunk tail (for context only — do not transcribe):\n"
        + prev_tail
    )


class EnglishAmChunkTranscriber:
    """Transcribe English audio chunks to American English (Whisper ``language=en``)."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        provider: AudioTranscriptionProvider | None = None,
        settings: Settings | None = None,
        cache: ArtifactCache | None = None,
        budget_guard: BudgetGuard | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.provider = provider or _build_provider(self.settings)
        self.cache = cache or ArtifactCache(settings=self.settings)
        self.budget_guard = budget_guard
        self.model_id = get_model_roles(self.settings).for_role("transcription")
        self.prompt_version = self.settings.prompt_version
        self._base_prompt = load_prompt(PROMPT_ID, self.prompt_version)

    def run(self, chunk_manifest: ChunkManifestArtifact) -> RawAmericanEnglishTranscriptArtifact:
        chunks_root = self.store.paths.root
        raw_chunks: list[RawAmericanEnglishChunk] = []
        prev_text: str | None = None
        warnings: list[str] = []

        pricing = get_pricing_table()
        total_seconds = sum(c.duration_ms for c in chunk_manifest.chunks) / 1000.0
        estimated_cost = 0.0
        try:
            estimated_cost = estimate_transcription_cost(
                duration_seconds=total_seconds,
                model_id=self.model_id,
                pricing=pricing,
            ).estimated_cost_usd
        except MissingPricingError as e:
            if self.settings.pipeline_provider_mode == "real":
                raise
            warnings.append(f"pricing-missing: {e}")

        if self.budget_guard is not None and self.settings.pipeline_provider_mode == "real":
            self.budget_guard.must_check(estimated_cost)

        for c in chunk_manifest.chunks:
            chunk_path = chunks_root / c.file_path
            cache_key = build_cache_key(
                input_hash=c.file_hash,
                stage_name=_CACHE_STAGE_LABEL,
                model_provider=self.provider.name,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                chunk_length_seconds=chunk_manifest.chunk_length_seconds,
                overlap_seconds=chunk_manifest.overlap_seconds,
                context_mode="prev_chunk_tail",
            )
            cached = self.cache.lookup(cache_key)
            if cached.hit and cached.payload:
                text_en = cached.payload.get("text", "")
                provider_metadata = {"cache_hit": True}
            else:
                prompt = _build_chunk_prompt(self._base_prompt, _previous_chunk_tail(prev_text))
                result = self.provider.transcribe_chunk(
                    chunk_path=chunk_path,
                    prompt=prompt,
                    model_id=self.model_id,
                    language_hint="en",
                )
                text_en = result.text
                provider_metadata = result.provider_metadata
                self.cache.store(
                    cache_key,
                    {
                        "text": text_en,
                        "model_id": self.model_id,
                        "provider": self.provider.name,
                    },
                )
                safe_log_event(
                    _LOGGER,
                    "english_am_transcribe_chunk_done",
                    chunk=c.chunk_id,
                    chars=len(text_en),
                    cache="miss",
                )
            raw_chunks.append(
                RawAmericanEnglishChunk(
                    chunk_id=c.chunk_id,
                    chunk_index=c.chunk_index,
                    start_ms=c.start_ms,
                    end_ms=c.end_ms,
                    text_english=text_en,
                    uncertainty_markers=_UNCLEAR_RE.findall(text_en),
                    provider_metadata=provider_metadata,
                )
            )
            prev_text = text_en

        artifact = self._build_artifact(
            chunk_manifest=chunk_manifest,
            raw_chunks=raw_chunks,
            estimated_cost=estimated_cost,
            warnings=warnings,
        )
        self.store.write_artifact(artifact, "raw_am_english_transcript.json")
        self.store.write_markdown(_to_markdown(artifact), "raw_am_english_transcript.md")
        return artifact

    def _build_artifact(
        self,
        *,
        chunk_manifest: ChunkManifestArtifact,
        raw_chunks: list[RawAmericanEnglishChunk],
        estimated_cost: float,
        warnings: list[str],
    ) -> RawAmericanEnglishTranscriptArtifact:
        full_text = "\n\n".join(c.text_english for c in raw_chunks)
        manifest = ArtifactManifest(
            artifact_id=f"raw_am_english_transcript_{uuid.uuid4().hex[:12]}",
            stage_name="english_chunk_transcriber",
            artifact_type="raw_am_english_transcript",
            source_input_hash=chunk_manifest.source_audio_hash,
            upstream_artifact_ids=[chunk_manifest.manifest.artifact_id],
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_id=PROMPT_ID,
            prompt_version=self.prompt_version,
            chunk_length_seconds=chunk_manifest.chunk_length_seconds,
            overlap_seconds=chunk_manifest.overlap_seconds,
            context_mode="prev_chunk_tail",
            estimated_cost_usd=estimated_cost,
            cache_hit=False,
            checksum=compute_text_checksum(full_text),
            warnings=warnings,
        )
        return RawAmericanEnglishTranscriptArtifact(
            source_audio_hash=chunk_manifest.source_audio_hash,
            chunk_manifest_artifact_id=chunk_manifest.manifest.artifact_id,
            chunks=raw_chunks,
            manifest=manifest,
        )


def run_english_am_transcriber(
    *,
    chunk_manifest: ChunkManifestArtifact,
    store: ArtifactStore,
    provider: AudioTranscriptionProvider | None = None,
    settings: Settings | None = None,
    cache: ArtifactCache | None = None,
    budget_guard: BudgetGuard | None = None,
) -> RawAmericanEnglishTranscriptArtifact:
    stage = EnglishAmChunkTranscriber(
        store=store,
        provider=provider,
        settings=settings,
        cache=cache,
        budget_guard=budget_guard,
    )
    return stage.run(chunk_manifest)


def _to_markdown(artifact: RawAmericanEnglishTranscriptArtifact) -> str:
    lines = [
        "# Raw American English Transcript",
        "",
        f"- Model: `{artifact.manifest.model_id}`",
        f"- Provider: `{artifact.manifest.model_provider}`",
        f"- Source audio hash: `{artifact.source_audio_hash}`",
        f"- Chunks: {len(artifact.chunks)}",
        "",
    ]
    for c in artifact.chunks:
        lines.append(
            f"## Chunk {c.chunk_index} ({c.chunk_id}) — {c.start_ms} ms → {c.end_ms} ms"
        )
        lines.append("")
        lines.append(c.text_english)
        lines.append("")
    return "\n".join(lines)

