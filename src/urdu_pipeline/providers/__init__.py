"""Provider abstractions: real and fake transcription / text generation."""

from urdu_pipeline.providers.base import (
    AudioTranscriptionProvider,
    ProviderError,
    ProviderFatalError,
    ProviderTransientError,
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
    "ProviderError",
    "ProviderFatalError",
    "ProviderPromptMetadata",
    "ProviderRequestChecksums",
    "ProviderSourceData",
    "ProviderTransientError",
    "TextGenerationProvider",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TranscriptionResult",
]
