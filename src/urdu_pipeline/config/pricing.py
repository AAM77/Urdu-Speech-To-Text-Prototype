"""Pricing table for cost estimation.

Pricing is configurable and **fails closed**: if a real model has no entry,
the budget guard refuses to run real-provider calls until the user adds an
override. Verify current prices at https://openai.com/api/pricing/ before
relying on these defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModelKind = Literal["transcription", "text"]


@dataclass(frozen=True)
class TextModelPrice:
    """Per-1M-token price (USD)."""
    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class TranscriptionModelPrice:
    """Per-minute price (USD)."""
    per_minute: float


# -----------------------------------------------------------------------------
# Default pricing snapshot.
# These values are best-effort defaults and MUST be verified against
# https://openai.com/api/pricing/ before enabling real-provider mode for a
# given model. Update via `PricingTable.with_overrides(...)` or by editing
# this file.
# -----------------------------------------------------------------------------
_DEFAULT_TEXT_PRICES: dict[str, TextModelPrice] = {
    "gpt-5.5": TextModelPrice(input_per_million=5.00, output_per_million=15.00),
    "gpt-5.4": TextModelPrice(input_per_million=2.50, output_per_million=10.00),
    "gpt-5.4-mini": TextModelPrice(input_per_million=0.15, output_per_million=0.60),
    "gpt-4o": TextModelPrice(input_per_million=2.50, output_per_million=10.00),
    "gpt-4o-mini": TextModelPrice(input_per_million=0.15, output_per_million=0.60),
    # Fake / test stand-ins are free.
    "fake-text": TextModelPrice(input_per_million=0.0, output_per_million=0.0),
}

_DEFAULT_TRANSCRIPTION_PRICES: dict[str, TranscriptionModelPrice] = {
    "gpt-4o-transcribe": TranscriptionModelPrice(per_minute=0.006),
    "gpt-4o-mini-transcribe": TranscriptionModelPrice(per_minute=0.003),
    "whisper-1": TranscriptionModelPrice(per_minute=0.006),
    "fake-transcribe": TranscriptionModelPrice(per_minute=0.0),
}


@dataclass(frozen=True)
class PricingTable:
    text: dict[str, TextModelPrice]
    transcription: dict[str, TranscriptionModelPrice]

    def text_price(self, model_id: str) -> TextModelPrice | None:
        return self.text.get(model_id)

    def transcription_price(self, model_id: str) -> TranscriptionModelPrice | None:
        return self.transcription.get(model_id)

    def with_overrides(
        self,
        text: dict[str, TextModelPrice] | None = None,
        transcription: dict[str, TranscriptionModelPrice] | None = None,
    ) -> "PricingTable":
        return PricingTable(
            text={**self.text, **(text or {})},
            transcription={**self.transcription, **(transcription or {})},
        )


def get_pricing_table() -> PricingTable:
    """Return a fresh copy of the pricing table.

    Deliberately not cached so tests can mutate one returned instance without
    affecting another.
    """
    return PricingTable(
        text=dict(_DEFAULT_TEXT_PRICES),
        transcription=dict(_DEFAULT_TRANSCRIPTION_PRICES),
    )


class MissingPricingError(RuntimeError):
    """Raised when a real-provider call lacks a pricing entry."""
