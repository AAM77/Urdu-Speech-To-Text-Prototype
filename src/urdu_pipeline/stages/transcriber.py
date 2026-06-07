"""Transcription stage."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from urdu_pipeline.artifacts.store import (
    ArtifactStore,
    compute_text_checksum,
)
from urdu_pipeline.application.ports import (
    ArtifactSink,
    CacheScope,
    CacheStore,
    RunWorkspace,
    UsageLedger,
    UsageRecord,
)
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.cache.cache_keys import build_cache_key
from urdu_pipeline.config.model_roles import get_model_roles
from urdu_pipeline.config.pricing import MissingPricingError, get_pricing_table
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.costs.budget_guard import BudgetGuard
from urdu_pipeline.costs.estimator import estimate_transcription_cost
from urdu_pipeline.domain import JobId, ProviderRunId, RunId, UserId
from urdu_pipeline.infrastructure.filesystem import (
    FilesystemArtifactSink,
    FilesystemRunWorkspace,
)
from urdu_pipeline.logging_utils import get_logger, safe_log_event
from urdu_pipeline.prompts import load_prompt
from urdu_pipeline.providers.base import AudioTranscriptionProvider
from urdu_pipeline.providers.fake_provider import FakeAudioTranscriptionProvider
from urdu_pipeline.providers.requests import (
    AudioTranscriptionRequest,
    ProviderPromptMetadata,
    ProviderSourceData,
)
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    RawTranscriptChunk,
)

_LOGGER = get_logger("stages.transcriber")
_UNCERTAIN_RE = re.compile(r"\[غیر واضح\]|\[unclear\]")


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


def _chunk_workspace_relative_path(file_path: str) -> str:
    path = Path(file_path)
    if path.parts and path.parts[0] == "chunks":
        return str(Path(*path.parts[1:]))
    return file_path


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


class TranscriberStage:
    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        workspace: RunWorkspace | None = None,
        artifact_sink: ArtifactSink | None = None,
        provider: AudioTranscriptionProvider | None = None,
        settings: Settings | None = None,
        cache: ArtifactCache | CacheStore | None = None,
        cache_scope: CacheScope | None = None,
        budget_guard: BudgetGuard | None = None,
        usage_ledger: UsageLedger | None = None,
        user_id: UserId | None = None,
        run_id: RunId | None = None,
        job_id: JobId | None = None,
    ) -> None:
        if store is None and (workspace is None or artifact_sink is None):
            raise ValueError(
                "TranscriberStage requires either an ArtifactStore or both "
                "RunWorkspace and ArtifactSink."
            )
        if usage_ledger is not None and (
            user_id is None or run_id is None or job_id is None
        ):
            raise ValueError(
                "UsageLedger recording requires user_id, run_id, and job_id."
            )
        self.store = store
        self.workspace = workspace or FilesystemRunWorkspace.from_store(store)
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
            self.cache_scope = CacheScope(user_id=user_id, name="transcriber")
        self.budget_guard = budget_guard
        self.usage_ledger = usage_ledger
        self.user_id = user_id
        self.run_id = run_id
        self.job_id = job_id
        self.model_id = get_model_roles(self.settings).for_role("transcription")
        self.prompt_version = self.settings.prompt_version
        self._base_prompt = load_prompt("transcription", self.prompt_version)

    # ------------------------------------------------------------------
    def run(self, chunk_manifest: ChunkManifestArtifact) -> RawTranscriptArtifact:
        self.workspace.ensure()
        raw_chunks: list[RawTranscriptChunk] = []
        prev_text: str | None = None
        warnings: list[str] = []

        # Cost guard: estimate the entire transcription up-front when we have
        # pricing for the model.
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
            chunk_path = self.workspace.chunk_path(
                _chunk_workspace_relative_path(c.file_path)
            )
            cache_key = build_cache_key(
                input_hash=c.file_hash,
                stage_name="transcriber",
                model_provider=self.provider.name,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                chunk_length_seconds=chunk_manifest.chunk_length_seconds,
                overlap_seconds=chunk_manifest.overlap_seconds,
                context_mode="prev_chunk_tail",
            )
            cache_hit, cached_payload = self._cache_lookup(cache_key)
            if cache_hit and cached_payload:
                text_urdu = str(cached_payload.get("text", ""))
                provider_metadata = {"cache_hit": True}
            else:
                previous_tail = _previous_chunk_tail(prev_text)
                request = AudioTranscriptionRequest(
                    chunk_path=chunk_path,
                    model_id=self.model_id,
                    developer_instructions=self._base_prompt,
                    language_hint="ur",
                    source_data=ProviderSourceData.from_audio(
                        chunk_path,
                        metadata={
                            "chunk_id": c.chunk_id,
                            "chunk_index": c.chunk_index,
                            "previous_chunk_tail": previous_tail,
                        },
                    ),
                    prompt_metadata=ProviderPromptMetadata(
                        stage_name="transcriber",
                        prompt_id="transcription",
                        prompt_version=self.prompt_version,
                        model_provider=self.provider.name,
                    ),
                )
                result = self.provider.transcribe_chunk(request)
                text_urdu = result.text
                provider_metadata = result.provider_metadata
                self._cache_store(
                    cache_key,
                    {
                        "text": text_urdu,
                        "model_id": self.model_id,
                        "provider": self.provider.name,
                    },
                )
                self._record_usage(result)
                safe_log_event(
                    _LOGGER,
                    "transcribe_chunk_done",
                    chunk=c.chunk_id,
                    chars=len(text_urdu),
                    cache="miss",
                )
            raw_chunks.append(
                RawTranscriptChunk(
                    chunk_id=c.chunk_id,
                    chunk_index=c.chunk_index,
                    start_ms=c.start_ms,
                    end_ms=c.end_ms,
                    text_urdu=text_urdu,
                    uncertainty_markers=_UNCERTAIN_RE.findall(text_urdu),
                    provider_metadata=provider_metadata,
                )
            )
            prev_text = text_urdu

        artifact = self._build_artifact(
            chunk_manifest=chunk_manifest,
            raw_chunks=raw_chunks,
            estimated_cost=estimated_cost,
            warnings=warnings,
        )
        self.artifact_sink.write_artifact(artifact, "raw_urdu_transcript.json")
        self.artifact_sink.write_markdown(_to_markdown(artifact), "raw_urdu_transcript.md")
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

    # ------------------------------------------------------------------
    def _build_artifact(
        self,
        *,
        chunk_manifest: ChunkManifestArtifact,
        raw_chunks: list[RawTranscriptChunk],
        estimated_cost: float,
        warnings: list[str],
    ) -> RawTranscriptArtifact:
        full_text = "\n\n".join(c.text_urdu for c in raw_chunks)
        manifest = ArtifactManifest(
            artifact_id=f"raw_urdu_transcript_{uuid.uuid4().hex[:12]}",
            stage_name="transcriber",
            artifact_type="raw_urdu_transcript",
            source_input_hash=chunk_manifest.source_audio_hash,
            upstream_artifact_ids=[chunk_manifest.manifest.artifact_id],
            model_provider=self.provider.name,
            model_id=self.model_id,
            prompt_id="transcription",
            prompt_version=self.prompt_version,
            chunk_length_seconds=chunk_manifest.chunk_length_seconds,
            overlap_seconds=chunk_manifest.overlap_seconds,
            context_mode="prev_chunk_tail",
            estimated_cost_usd=estimated_cost,
            cache_hit=False,
            checksum=compute_text_checksum(full_text),
            warnings=warnings,
        )
        return RawTranscriptArtifact(
            source_audio_hash=chunk_manifest.source_audio_hash,
            chunk_manifest_artifact_id=chunk_manifest.manifest.artifact_id,
            chunks=raw_chunks,
            manifest=manifest,
        )


def _to_markdown(artifact: RawTranscriptArtifact) -> str:
    lines = [
        "# Raw Urdu Transcript",
        "",
        f"- Model: `{artifact.manifest.model_id}`",
        f"- Provider: `{artifact.manifest.model_provider}`",
        f"- Source audio hash: `{artifact.source_audio_hash}`",
        f"- Chunks: {len(artifact.chunks)}",
        "",
    ]
    for c in artifact.chunks:
        lines.append(f"## Chunk {c.chunk_index} ({c.chunk_id}) — {c.start_ms} ms → {c.end_ms} ms")
        lines.append("")
        lines.append(c.text_urdu)
        lines.append("")
    return "\n".join(lines)


def run_transcriber_stage(
    *,
    chunk_manifest: ChunkManifestArtifact,
    store: ArtifactStore,
    provider: AudioTranscriptionProvider | None = None,
    settings: Settings | None = None,
    cache: ArtifactCache | None = None,
    budget_guard: BudgetGuard | None = None,
) -> RawTranscriptArtifact:
    stage = TranscriberStage(
        store=store,
        provider=provider,
        settings=settings,
        cache=cache,
        budget_guard=budget_guard,
    )
    return stage.run(chunk_manifest)
