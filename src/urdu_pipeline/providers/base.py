"""Provider interfaces (sync, prototype-grade)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class TranscriptionResult:
    text: str
    model_id: str
    duration_seconds: float = 0.0
    actual_usage: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextGenerationResult:
    text: str
    model_id: str
    actual_usage: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class AudioTranscriptionProvider(Protocol):
    """Speech-to-text provider for one audio chunk at a time."""

    name: str

    def transcribe_chunk(
        self,
        chunk_path: Path,
        prompt: str,
        model_id: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult: ...


class TextGenerationProvider(Protocol):
    """Text-in / text-out generation provider."""

    name: str

    def generate(
        self,
        prompt: str,
        input_text: str,
        model_id: str,
        max_output_tokens: int | None = None,
    ) -> TextGenerationResult: ...
