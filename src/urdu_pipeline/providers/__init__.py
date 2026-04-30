"""Provider abstractions: real and fake transcription / text generation."""

from urdu_pipeline.providers.base import (
    AudioTranscriptionProvider,
    TextGenerationProvider,
    TextGenerationResult,
    TranscriptionResult,
)
from urdu_pipeline.providers.fake_provider import (
    FakeAudioTranscriptionProvider,
    FakeTextGenerationProvider,
)

__all__ = [
    "AudioTranscriptionProvider",
    "FakeAudioTranscriptionProvider",
    "FakeTextGenerationProvider",
    "TextGenerationProvider",
    "TextGenerationResult",
    "TranscriptionResult",
]
