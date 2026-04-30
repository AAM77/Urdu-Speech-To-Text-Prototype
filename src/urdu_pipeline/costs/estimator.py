"""Cost estimators for transcription minutes and text-model tokens."""

from __future__ import annotations

import math
from dataclasses import dataclass

from urdu_pipeline.config.pricing import (
    MissingPricingError,
    PricingTable,
    get_pricing_table,
)


@dataclass(frozen=True)
class CostEstimate:
    model_id: str
    estimated_cost_usd: float
    detail: dict


def rough_token_count(text: str) -> int:
    """Cheap, no-network token estimate.

    Uses tiktoken when available, otherwise falls back to a 4-chars-per-token
    heuristic. Always returns >= 1.
    """
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore

        # cl100k_base is a reasonable default for current GPT models.
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text)))
    except Exception:
        return max(1, math.ceil(len(text) / 4))


def estimate_transcription_cost(
    duration_seconds: float,
    model_id: str,
    *,
    pricing: PricingTable | None = None,
) -> CostEstimate:
    pricing = pricing or get_pricing_table()
    price = pricing.transcription_price(model_id)
    if price is None:
        raise MissingPricingError(
            f"No pricing found for transcription model {model_id!r}."
        )
    minutes = max(0.0, duration_seconds) / 60.0
    cost = minutes * price.per_minute
    return CostEstimate(
        model_id=model_id,
        estimated_cost_usd=round(cost, 6),
        detail={
            "duration_seconds": duration_seconds,
            "minutes": round(minutes, 4),
            "per_minute_usd": price.per_minute,
        },
    )


def estimate_text_cost(
    input_text: str,
    model_id: str,
    *,
    expected_output_tokens: int = 0,
    pricing: PricingTable | None = None,
) -> CostEstimate:
    pricing = pricing or get_pricing_table()
    price = pricing.text_price(model_id)
    if price is None:
        raise MissingPricingError(
            f"No pricing found for text model {model_id!r}."
        )
    input_tokens = rough_token_count(input_text)
    output_tokens = max(0, expected_output_tokens)
    in_cost = (input_tokens / 1_000_000.0) * price.input_per_million
    out_cost = (output_tokens / 1_000_000.0) * price.output_per_million
    return CostEstimate(
        model_id=model_id,
        estimated_cost_usd=round(in_cost + out_cost, 6),
        detail={
            "input_tokens": input_tokens,
            "expected_output_tokens": output_tokens,
            "input_price_per_million_usd": price.input_per_million,
            "output_price_per_million_usd": price.output_per_million,
        },
    )
