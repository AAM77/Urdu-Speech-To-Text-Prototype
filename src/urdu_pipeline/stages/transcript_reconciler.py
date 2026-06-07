"""Transcript reconciliation stage.

Two-layer approach:

1. Deterministic overlap cleanup using rapidfuzz to find the longest fuzzy
   suffix of chunk N that matches a prefix of chunk N+1, then drop the
   duplicated text from chunk N+1.
2. Optional model-assisted polishing (only used when the user explicitly
   enables it in settings; the prototype default is deterministic-only).

Uncertainty markers like `[غیر واضح]` are always preserved.
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable

from urdu_pipeline.artifacts.store import ArtifactStore, compute_text_checksum
from urdu_pipeline.application.ports import ArtifactSink
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.infrastructure.filesystem import FilesystemArtifactSink
from urdu_pipeline.logging_utils import get_logger
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    ReconciledSegment,
    ReconciledTranscriptArtifact,
)

_LOGGER = get_logger("stages.reconciler")
_UNCERTAIN = "[غیر واضح]"
_TOKEN_RE = re.compile(r"\S+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _find_overlap_token_count(
    prev_tokens: list[str],
    next_tokens: list[str],
    *,
    max_window: int = 80,
    min_window: int = 4,
    similarity_threshold: int = 85,
) -> int:
    """Return how many leading tokens of `next_tokens` overlap with the
    trailing tokens of `prev_tokens`.

    Returns 0 if no good overlap is found. Uses rapidfuzz when available,
    otherwise falls back to exact-prefix matching.
    """
    try:
        from rapidfuzz import fuzz  # type: ignore
    except ImportError:  # pragma: no cover
        fuzz = None

    upper = min(max_window, len(prev_tokens), len(next_tokens))
    best = 0
    for w in range(upper, min_window - 1, -1):
        prev_window = " ".join(prev_tokens[-w:])
        next_window = " ".join(next_tokens[:w])
        if fuzz is None:
            if prev_window == next_window:
                return w
            continue
        score = fuzz.ratio(prev_window, next_window)
        if score >= similarity_threshold and w > best:
            best = w
            # Try shorter windows only to confirm; the first ratio match at the
            # largest window is fine.
            return best
    return best


def _merge_two(prev_text: str, next_text: str) -> tuple[str, int]:
    prev_tokens = _tokenize(prev_text)
    next_tokens = _tokenize(next_text)
    if not prev_tokens or not next_tokens:
        return next_text, 0
    n = _find_overlap_token_count(prev_tokens, next_tokens)
    if n == 0:
        return next_text, 0
    trimmed = " ".join(next_tokens[n:])
    return trimmed, n


def _stitch(texts: Iterable[str]) -> tuple[str, list[int]]:
    """Concatenate texts, dropping duplicated overlap content between them.

    Returns the stitched text plus a list of how many tokens were trimmed
    from each subsequent chunk (length = len(texts) - 1).
    """
    texts = list(texts)
    if not texts:
        return "", []
    stitched_parts = [texts[0].strip()]
    trims: list[int] = []
    for nxt in texts[1:]:
        trimmed, n = _merge_two(stitched_parts[-1], nxt)
        trims.append(n)
        if trimmed.strip():
            stitched_parts.append(trimmed.strip())
    return "\n\n".join(stitched_parts), trims


class ReconcilerStage:
    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        artifact_sink: ArtifactSink | None = None,
        settings: Settings | None = None,
    ) -> None:
        if store is None and artifact_sink is None:
            raise ValueError("ReconcilerStage requires an ArtifactStore or ArtifactSink.")
        self.store = store
        self.artifact_sink = artifact_sink or FilesystemArtifactSink(store)
        self.settings = settings or get_settings()

    def run(self, raw: RawTranscriptArtifact) -> ReconciledTranscriptArtifact:
        sorted_chunks = sorted(raw.chunks, key=lambda c: c.chunk_index)
        segments: list[ReconciledSegment] = []
        running_text_parts: list[str] = []

        if not sorted_chunks:
            full_text = ""
        else:
            # Build stitched text and per-chunk segments.
            texts = [c.text_urdu for c in sorted_chunks]
            full_text, trims = _stitch(texts)

            # Map each contributing chunk to a segment record.
            running_overlap = [0] + trims  # tokens trimmed from start of each chunk after #0
            for c, trimmed in zip(sorted_chunks, running_overlap):
                tokens = _tokenize(c.text_urdu)
                kept_tokens = tokens[trimmed:]
                segment_text = " ".join(kept_tokens).strip()
                if not segment_text and not c.text_urdu.strip():
                    continue
                segments.append(
                    ReconciledSegment(
                        segment_id=f"seg_{c.chunk_index:04d}",
                        source_chunk_ids=[c.chunk_id],
                        approx_start_ms=c.start_ms,
                        approx_end_ms=c.end_ms,
                        text_urdu=segment_text or c.text_urdu,
                        warnings=[
                            f"trimmed_overlap_tokens={trimmed}"
                        ] if trimmed > 0 else [],
                    )
                )
                running_text_parts.append(segment_text or c.text_urdu)

        manifest = ArtifactManifest(
            artifact_id=f"reconciled_urdu_transcript_{uuid.uuid4().hex[:12]}",
            stage_name="transcript_reconciler",
            artifact_type="reconciled_urdu_transcript",
            source_input_hash=raw.source_audio_hash,
            upstream_artifact_ids=[raw.manifest.artifact_id],
            model_provider="deterministic",
            model_id="rapidfuzz-overlap",
            prompt_id="reconciliation",
            prompt_version=self.settings.prompt_version,
            chunk_length_seconds=raw.manifest.chunk_length_seconds,
            overlap_seconds=raw.manifest.overlap_seconds,
            context_mode="overlap_dedupe",
            estimated_cost_usd=0.0,
            cache_hit=False,
            checksum=compute_text_checksum(full_text),
            warnings=[],
        )

        artifact = ReconciledTranscriptArtifact(
            source_audio_hash=raw.source_audio_hash,
            raw_transcript_artifact_id=raw.manifest.artifact_id,
            segments=segments,
            full_text_urdu=full_text,
            manifest=manifest,
        )
        self.artifact_sink.write_artifact(artifact, "reconciled_urdu_transcript.json")
        self.artifact_sink.write_markdown(
            _to_markdown(artifact),
            "reconciled_urdu_transcript.md",
        )
        return artifact


def _to_markdown(artifact: ReconciledTranscriptArtifact) -> str:
    lines = [
        "# Reconciled Urdu Transcript",
        "",
        f"- Model: `{artifact.manifest.model_id}` (provider: `{artifact.manifest.model_provider}`)",
        f"- Segments: {len(artifact.segments)}",
        "",
    ]
    for s in artifact.segments:
        lines.append(
            f"## {s.segment_id} — chunks: {', '.join(s.source_chunk_ids)}"
        )
        lines.append("")
        lines.append(s.text_urdu)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Full reconciled text")
    lines.append("")
    lines.append(artifact.full_text_urdu)
    lines.append("")
    return "\n".join(lines)


def run_reconciler_stage(
    *,
    raw: RawTranscriptArtifact,
    store: ArtifactStore,
    settings: Settings | None = None,
) -> ReconciledTranscriptArtifact:
    return ReconcilerStage(store=store, settings=settings).run(raw)
