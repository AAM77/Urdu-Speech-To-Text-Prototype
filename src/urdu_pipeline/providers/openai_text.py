"""Real OpenAI text generation provider (used for reconciliation / translation / article).

Uses the Responses API when available (preferred for current GPT-5.x family)
and falls back to chat.completions if the SDK doesn't expose Responses.

NOTE: Verify the current set of supported models, endpoints, and parameter
names against https://platform.openai.com/docs/api-reference before enabling
real-provider mode for a given model.
"""

from __future__ import annotations

from urdu_pipeline.config.settings import get_settings
from urdu_pipeline.logging_utils import get_logger, safe_log_event
from urdu_pipeline.providers.base import TextGenerationResult
from urdu_pipeline.providers.requests import (
    TextGenerationRequest,
    coerce_text_generation_request,
)

_LOGGER = get_logger("providers.openai_text")


class OpenAITextProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
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

    def generate(
        self,
        request: TextGenerationRequest | str | None = None,
        input_text: str | None = None,
        model_id: str | None = None,
        max_output_tokens: int | None = None,
        *,
        prompt: str | None = None,
    ) -> TextGenerationResult:
        request_obj = coerce_text_generation_request(
            request,
            input_text=input_text,
            model_id=model_id,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
        )
        full_prompt = request_obj.full_prompt_text()
        safe_log_event(
            _LOGGER,
            "text_generate_start",
            model=request_obj.model_id,
            prompt_chars=len(request_obj.instruction_text),
            input_chars=len(request_obj.source_text),
        )

        text, usage = self._call_responses_or_chat(
            request_obj.model_id,
            full_prompt,
            request_obj.max_output_tokens,
        )

        safe_log_event(
            _LOGGER,
            "text_generate_done",
            model=request_obj.model_id,
            output_chars=len(text or ""),
        )
        return TextGenerationResult(
            text=text or "",
            model_id=request_obj.model_id,
            actual_usage=usage or {},
            provider_metadata={},
        )

    # ------------------------------------------------------------------
    def _call_responses_or_chat(
        self,
        model_id: str,
        full_prompt: str,
        max_output_tokens: int | None,
    ) -> tuple[str, dict]:
        # Prefer the Responses API if present.
        responses_api = getattr(self._client, "responses", None)
        if responses_api is not None and hasattr(responses_api, "create"):
            kwargs: dict = {"model": model_id, "input": full_prompt}
            if max_output_tokens:
                kwargs["max_output_tokens"] = max_output_tokens
            resp = responses_api.create(**kwargs)
            text = getattr(resp, "output_text", None)
            if text is None:
                # Fallback: walk the structured response.
                pieces: list[str] = []
                for item in getattr(resp, "output", []) or []:
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if t:
                            pieces.append(t)
                text = "".join(pieces)
            usage = getattr(resp, "usage", None)
            usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if usage else {})
            return text or "", usage_dict

        # Fallback: chat.completions
        chat_api = self._client.chat.completions
        resp = chat_api.create(
            model=model_id,
            messages=[{"role": "user", "content": full_prompt}],
            max_tokens=max_output_tokens,
        )
        text = resp.choices[0].message.content if resp.choices else ""
        usage = resp.usage
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if usage else {})
        return text or "", usage_dict
