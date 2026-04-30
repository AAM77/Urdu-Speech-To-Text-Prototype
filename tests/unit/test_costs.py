"""Cost estimator + budget guard tests."""

from __future__ import annotations

import pytest

from urdu_pipeline.config.pricing import (
    MissingPricingError,
    PricingTable,
    TextModelPrice,
    TranscriptionModelPrice,
    get_pricing_table,
)
from urdu_pipeline.config.settings import reset_settings_cache
from urdu_pipeline.costs.budget_guard import (
    BudgetGuard,
    BudgetViolationError,
)
from urdu_pipeline.costs.estimator import (
    estimate_text_cost,
    estimate_transcription_cost,
    rough_token_count,
)


def test_estimate_transcription_cost_basic():
    pricing = PricingTable(
        text={},
        transcription={"m": TranscriptionModelPrice(per_minute=0.1)},
    )
    est = estimate_transcription_cost(60.0, "m", pricing=pricing)
    assert est.estimated_cost_usd == pytest.approx(0.1)
    assert est.detail["minutes"] == pytest.approx(1.0)


def test_estimate_text_cost_uses_input_and_output():
    pricing = PricingTable(
        text={"m": TextModelPrice(input_per_million=10.0, output_per_million=20.0)},
        transcription={},
    )
    est = estimate_text_cost(
        "hello world " * 100, "m", expected_output_tokens=5_000, pricing=pricing
    )
    assert est.estimated_cost_usd > 0


def test_missing_transcription_pricing_raises():
    pricing = PricingTable(text={}, transcription={})
    with pytest.raises(MissingPricingError):
        estimate_transcription_cost(60.0, "no-such-model", pricing=pricing)


def test_missing_text_pricing_raises():
    pricing = PricingTable(text={}, transcription={})
    with pytest.raises(MissingPricingError):
        estimate_text_cost("hi", "no-such-model", pricing=pricing)


def test_rough_token_count_handles_empty():
    assert rough_token_count("") == 0
    assert rough_token_count("a") >= 1


def test_budget_guard_warns_above_selected_budget(monkeypatch):
    monkeypatch.setenv("DEFAULT_BUDGET_USD", "10")
    monkeypatch.setenv("HARD_CAP_USD", "60")
    monkeypatch.setenv("COST_SAFETY_MARGIN", "0.20")
    reset_settings_cache()
    g = BudgetGuard.for_run(10.0)
    res = g.check(20.0)  # 20 * 1.20 = 24 > 10 budget but < 60 cap
    assert res.allowed
    assert res.warning
    assert not res.blocked


def test_budget_guard_hard_blocks_above_cap(monkeypatch):
    monkeypatch.setenv("DEFAULT_BUDGET_USD", "10")
    monkeypatch.setenv("HARD_CAP_USD", "60")
    monkeypatch.setenv("COST_SAFETY_MARGIN", "0.20")
    reset_settings_cache()
    g = BudgetGuard.for_run(10.0)
    res = g.check(80.0)  # 80 * 1.20 = 96 > 60
    assert res.blocked
    with pytest.raises(BudgetViolationError):
        g.must_check(80.0)


def test_budget_guard_safety_margin_is_applied(monkeypatch):
    monkeypatch.setenv("COST_SAFETY_MARGIN", "0.20")
    reset_settings_cache()
    g = BudgetGuard.for_run(50.0)
    res = g.check(40.0)
    # projected with margin = 40 * 1.20 = 48 (< 50 budget)
    assert res.projected_total_with_margin_usd == pytest.approx(48.0)
    assert res.allowed and not res.warning


def test_budget_guard_accumulates():
    g = BudgetGuard.for_run(60.0)
    g.record_actual(10.0)
    g.record_actual(15.0)
    assert g.accumulated_cost_usd == 25.0


def test_default_pricing_table_has_basics():
    pt = get_pricing_table()
    assert pt.text_price("fake-text") is not None
    assert pt.transcription_price("fake-transcribe") is not None
