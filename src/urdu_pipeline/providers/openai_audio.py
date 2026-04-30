"""Real OpenAI audio transcription provider.

Wraps the OpenAI Python SDK's audio transcription endpoint. Behavior is gated
by `Settings.pipeline_provider_mode == "real"` and `OPENAI_API_KEY` being set.

NOTE: Verify current model IDs, endpoints, and limits against
https://platform.openai.com/docs/api-reference/audio before relying on the
defaults. The SDK / API surface evolves frequently.
"""

from __future__ import annotations

from pathlib import Path

from urdu_pipeline.config.settings import get_settings
from urdu_pipeline.logging_utils import get_logger, safe_log_event
from urdu_pipeline.providers.base import TranscriptionResult

_LOGGER = get_logger("providers.openai_audio")


class OpenAIAudioProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        # Defer import so unit tests / fake mode never need the SDK installed.
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "openai package is not installed. Install dependencies first."
            ) from e

        s = get_settings()
        self._client = OpenAI(
            api_key=api_key or s.openai_api_key,
            organization=s.openai_org_id or None,
            project=s.openai_project_id or None,
        )

    def transcribe_chunk(
        self,
        chunk_path: Path,
        prompt: str,
        model_id: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        lang = language_hint or "ur"
        safe_log_event(
            _LOGGER,
            "transcribe_chunk_start",
            model=model_id,
            file=chunk_path.name,
            language=lang,
        )
        with chunk_path.open("rb") as fh:
            # The OpenAI SDK accepts file-like objects for audio.transcriptions.
            response = self._client.audio.transcriptions.create(
                model=model_id,
                file=fh,
                prompt=prompt,
                language=lang,
                response_format="text",
            )

        # SDK returns either a string (response_format="text") or an object
        # with `.text`. Handle both shapes defensively.
        text = response if isinstance(response, str) else getattr(response, "text", str(response))

        safe_log_event(
            _LOGGER,
            "transcribe_chunk_done",
            model=model_id,
            file=chunk_path.name,
            chars=len(text or ""),
        )
        return TranscriptionResult(
            text=text or "",
            model_id=model_id,
            duration_seconds=0.0,
            actual_usage={},
            provider_metadata={"file": chunk_path.name},
        )
