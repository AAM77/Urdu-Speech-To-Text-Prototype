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
from urdu_pipeline.providers.requests import (
    AudioTranscriptionRequest,
    ProviderPromptMetadata,
    ProviderRequestChecksums,
    ProviderSourceData,
    TextGenerationRequest,
)

__all__ = [
    "AudioTranscriptionRequest",
    "AudioTranscriptionProvider",
    "FakeAudioTranscriptionProvider",
    "FakeTextGenerationProvider",
    "ProviderPromptMetadata",
    "ProviderRequestChecksums",
    "ProviderSourceData",
    "TextGenerationProvider",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TranscriptionResult",
]
