"""Provider interfaces (sync, prototype-grade)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from urdu_pipeline.providers.requests import (
    AudioTranscriptionRequest,
    TextGenerationRequest,
)


class ProviderError(Exception):
    """Base class for provider adapter failures."""


class ProviderTransientError(ProviderError):
    """Provider failure that should be retried by the processor lifecycle."""


class ProviderFatalError(ProviderError):
    """Provider failure that should fail the job without retry."""


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
        chunk_path: AudioTranscriptionRequest | Path | str,
        prompt: str | None = None,
        model_id: str | None = None,
        language_hint: str | None = None,
    ) -> TranscriptionResult: ...


class TextGenerationProvider(Protocol):
    """Text-in / text-out generation provider."""

    name: str

    def generate(
        self,
        request: TextGenerationRequest | str | None = None,
        input_text: str | None = None,
        model_id: str | None = None,
        max_output_tokens: int | None = None,
        *,
        prompt: str | None = None,
    ) -> TextGenerationResult: ...


__all__ = [
    "AudioTranscriptionProvider",
    "ProviderError",
    "ProviderFatalError",
    "ProviderTransientError",
    "TextGenerationProvider",
    "TextGenerationResult",
    "TranscriptionResult",
]
