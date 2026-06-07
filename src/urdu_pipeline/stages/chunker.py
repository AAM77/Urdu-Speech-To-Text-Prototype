"""Chunker stage.

Splits a long audio file into N chunks of `chunk_length` with `overlap`
between adjacent chunks. Chunk N starts at `(N-1) * (chunk_length - overlap)`.
The final chunk may be shorter than `chunk_length`.

ffmpeg / ffprobe must be on PATH. We invoke them via `subprocess` to avoid
adding heavy Python wrappers for the prototype.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from urdu_pipeline.artifacts.store import (
    ArtifactStore,
    compute_file_hash,
    sanitize_filename,
)
from urdu_pipeline.application.ports import ArtifactSink, RunWorkspace
from urdu_pipeline.config.settings import Settings, get_settings
from urdu_pipeline.infrastructure.filesystem import (
    FilesystemArtifactSink,
    FilesystemRunWorkspace,
)
from urdu_pipeline.logging_utils import get_logger, safe_log_event
from urdu_pipeline.schemas.chunks import (
    AudioChunk,
    ChunkManifestArtifact,
)
from urdu_pipeline.schemas.manifests import ArtifactManifest

_LOGGER = get_logger("stages.chunker")

# OpenAI's transcription upload limit is currently 25 MB; the prototype warns
# at MAX_CHUNK_MB (default 24 MB) so users can re-encode if needed.


# -----------------------------------------------------------------------------
# Pure helpers (no I/O) — used by tests
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class PlannedChunk:
    chunk_index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def plan_chunks(
    duration_seconds: float,
    chunk_length_seconds: int,
    overlap_seconds: int,
) -> list[PlannedChunk]:
    """Compute the chunk boundaries for an audio file.

    Each new chunk starts after `chunk_length - overlap` seconds. The final
    chunk is allowed to be shorter than `chunk_length`.
    """
    if chunk_length_seconds <= 0:
        raise ValueError("chunk_length_seconds must be positive.")
    if overlap_seconds < 0 or overlap_seconds >= chunk_length_seconds:
        raise ValueError("0 <= overlap_seconds < chunk_length_seconds is required.")
    if duration_seconds <= 0:
        return []

    total_ms = int(round(duration_seconds * 1000))
    chunk_ms = int(round(chunk_length_seconds * 1000))
    step_ms = int(round((chunk_length_seconds - overlap_seconds) * 1000))

    out: list[PlannedChunk] = []
    start = 0
    idx = 1
    while start < total_ms:
        end = min(start + chunk_ms, total_ms)
        out.append(PlannedChunk(chunk_index=idx, start_ms=start, end_ms=end))
        if end >= total_ms:
            break
        start += step_ms
        idx += 1
    return out


# -----------------------------------------------------------------------------
# ffprobe / ffmpeg wrappers
# -----------------------------------------------------------------------------
class FFmpegNotFoundError(RuntimeError):
    pass


def _require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegNotFoundError(
                f"`{tool}` was not found on PATH. Install ffmpeg (see README) "
                "before running the chunker."
            )


def probe_audio_duration_seconds(audio_path: Path) -> float:
    _require_ffmpeg()
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout or "{}")
    duration = float(payload.get("format", {}).get("duration", 0.0))
    return duration


def _slice_chunk_with_ffmpeg(
    *,
    source: Path,
    target: Path,
    start_ms: int,
    duration_ms: int,
    output_format: str,
) -> None:
    """Cut a single chunk by re-encoding to the target format.

    Re-encoding (rather than `-c copy`) keeps the per-chunk timing accurate
    and avoids container quirks for very short tail chunks.
    """
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-t",
        f"{duration_ms / 1000:.3f}",
        "-i",
        str(source),
        "-vn",
    ]
    if output_format == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "64k", "-ar", "16000", "-ac", "1"]
    elif output_format == "wav":
        args += ["-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1"]
    else:
        # Generic: let ffmpeg pick a sane encoder for the requested extension.
        args += ["-ar", "16000", "-ac", "1"]
    args.append(str(target))
    subprocess.run(args, check=True, capture_output=True)


# -----------------------------------------------------------------------------
# Stage
# -----------------------------------------------------------------------------
class ChunkerStage:
    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        workspace: RunWorkspace | None = None,
        artifact_sink: ArtifactSink | None = None,
        settings: Settings | None = None,
        chunk_length_seconds: int | None = None,
        overlap_seconds: int | None = None,
    ) -> None:
        if store is None and (workspace is None or artifact_sink is None):
            raise ValueError(
                "ChunkerStage requires either an ArtifactStore or both "
                "RunWorkspace and ArtifactSink."
            )
        self.store = store
        self.workspace = workspace or FilesystemRunWorkspace.from_store(store)
        self.artifact_sink = artifact_sink or FilesystemArtifactSink(store)
        self.settings = settings or get_settings()
        self.chunk_length_seconds = (
            chunk_length_seconds or self.settings.default_chunk_length_seconds
        )
        self.overlap_seconds = (
            overlap_seconds if overlap_seconds is not None else self.settings.default_overlap_seconds
        )

    def run(self, audio_path: Path | str) -> ChunkManifestArtifact:
        self.workspace.ensure()
        audio_path = Path(audio_path).expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.settings.is_audio_extension_allowed(audio_path):
            raise ValueError(
                f"Audio extension {audio_path.suffix!r} is not in the configured "
                f"ACCEPTED_AUDIO_EXTENSIONS list "
                f"({sorted(self.settings.accepted_audio_extensions_set)})."
            )

        # Copy input audio into the run for reproducibility.
        copied = self.workspace.input_path(sanitize_filename(audio_path.name))
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(audio_path.read_bytes())
        source_hash = compute_file_hash(copied)
        ext_for_chunks = audio_path.suffix.lstrip(".").lower() or "mp3"

        duration_seconds = probe_audio_duration_seconds(copied)
        planned = plan_chunks(
            duration_seconds=duration_seconds,
            chunk_length_seconds=self.chunk_length_seconds,
            overlap_seconds=self.overlap_seconds,
        )
        safe_log_event(
            _LOGGER,
            "chunker_planned",
            run=self.workspace.root.name,
            duration_s=int(duration_seconds),
            chunks=len(planned),
            chunk_len_s=self.chunk_length_seconds,
            overlap_s=self.overlap_seconds,
        )

        chunks: list[AudioChunk] = []
        warnings: list[str] = []
        max_bytes = int(self.settings.max_chunk_mb * 1024 * 1024)
        for p in planned:
            chunk_filename = sanitize_filename(f"chunk_{p.chunk_index:04d}.{ext_for_chunks}")
            chunk_path = self.workspace.chunk_path(chunk_filename)
            chunk_path.parent.mkdir(parents=True, exist_ok=True)
            _slice_chunk_with_ffmpeg(
                source=copied,
                target=chunk_path,
                start_ms=p.start_ms,
                duration_ms=p.duration_ms,
                output_format=ext_for_chunks,
            )
            size = chunk_path.stat().st_size
            if size > max_bytes:
                warnings.append(
                    f"chunk_{p.chunk_index:04d} is {size / 1024 / 1024:.1f} MB, "
                    f"above MAX_CHUNK_MB={self.settings.max_chunk_mb}. The OpenAI "
                    "transcription upload limit is 25 MB."
                )
            chunks.append(
                AudioChunk(
                    chunk_id=f"chunk_{p.chunk_index:04d}",
                    source_audio_hash=source_hash,
                    chunk_index=p.chunk_index,
                    start_ms=p.start_ms,
                    end_ms=p.end_ms,
                    duration_ms=p.duration_ms,
                    overlap_before_ms=(self.overlap_seconds * 1000) if p.chunk_index > 1 else 0,
                    overlap_after_ms=(self.overlap_seconds * 1000) if p.chunk_index < len(planned) else 0,
                    file_path=str(chunk_path.relative_to(self.workspace.root)),
                    file_hash=compute_file_hash(chunk_path),
                    file_size_bytes=size,
                    audio_format=ext_for_chunks,
                )
            )

        manifest = ArtifactManifest(
            artifact_id=f"chunk_manifest_{uuid.uuid4().hex[:12]}",
            stage_name="chunker",
            artifact_type="chunk_manifest",
            source_input_hash=source_hash,
            chunk_length_seconds=self.chunk_length_seconds,
            overlap_seconds=self.overlap_seconds,
            cache_hit=False,
            warnings=warnings,
        )

        artifact = ChunkManifestArtifact(
            source_audio_path=str(copied.relative_to(self.workspace.root)),
            source_audio_hash=source_hash,
            source_audio_duration_ms=int(round(duration_seconds * 1000)),
            source_audio_format=ext_for_chunks,
            chunk_length_seconds=self.chunk_length_seconds,
            overlap_seconds=self.overlap_seconds,
            chunks=chunks,
            manifest=manifest,
        )

        # Persist artifact + human summary
        self.artifact_sink.write_artifact(artifact, "chunk_manifest.json")
        self.artifact_sink.write_markdown(
            _build_chunk_summary_md(artifact),
            "chunk_summary.md",
        )
        return artifact


def _build_chunk_summary_md(artifact: ChunkManifestArtifact) -> str:
    lines = [
        "# Chunk Summary",
        "",
        f"- Source audio: `{artifact.source_audio_path}`",
        f"- Source audio hash: `{artifact.source_audio_hash}`",
        f"- Duration (ms): {artifact.source_audio_duration_ms}",
        f"- Format: `{artifact.source_audio_format}`",
        f"- Chunk length (s): {artifact.chunk_length_seconds}",
        f"- Overlap (s): {artifact.overlap_seconds}",
        f"- Chunk count: {len(artifact.chunks)}",
        "",
        "| # | start_ms | end_ms | duration_ms | file |",
        "|---|---------:|-------:|------------:|------|",
    ]
    for c in artifact.chunks:
        lines.append(
            f"| {c.chunk_index} | {c.start_ms} | {c.end_ms} | {c.duration_ms} | `{c.file_path}` |"
        )
    if artifact.manifest.warnings:
        lines += ["", "## Warnings", ""]
        for w in artifact.manifest.warnings:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def run_chunker_stage(
    *,
    audio_path: Path | str,
    store: ArtifactStore,
    settings: Settings | None = None,
    chunk_length_seconds: int | None = None,
    overlap_seconds: int | None = None,
) -> ChunkManifestArtifact:
    stage = ChunkerStage(
        store=store,
        settings=settings,
        chunk_length_seconds=chunk_length_seconds,
        overlap_seconds=overlap_seconds,
    )
    return stage.run(audio_path)
