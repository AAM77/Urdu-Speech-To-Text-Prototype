"""Budget guard.

Decides whether a paid API call may proceed given:
- the cost already accumulated this run,
- the estimated cost of the next stage,
- the user's selected per-run budget,
- the absolute hard cap (default $60).

Includes a configurable safety margin (default 20%).
"""

from __future__ import annotations

from dataclasses import dataclass

from urdu_pipeline.config.settings import Settings, get_settings


class BudgetViolationError(RuntimeError):
    """Raised when a projected total exceeds the hard cap (or the user budget
    if `allow_warning` is False)."""


@dataclass(frozen=True)
class BudgetCheckResult:
    allowed: bool
    blocked: bool
    warning: bool
    projected_total_usd: float
    projected_total_with_margin_usd: float
    selected_budget_usd: float
    hard_cap_usd: float
    safety_margin: float
    reason: str


@dataclass
class BudgetGuard:
    """Stateful budget tracker for a single run."""

    settings: Settings
    selected_budget_usd: float
    accumulated_cost_usd: float = 0.0

    @classmethod
    def for_run(
        cls,
        selected_budget_usd: float | None = None,
        *,
        settings: Settings | None = None,
    ) -> "BudgetGuard":
        s = settings or get_settings()
        budget = float(selected_budget_usd if selected_budget_usd is not None else s.default_budget_usd)
        return cls(settings=s, selected_budget_usd=budget, accumulated_cost_usd=0.0)

    # ------------------------------------------------------------------
    def check(self, next_stage_cost_usd: float) -> BudgetCheckResult:
        margin = self.settings.cost_safety_margin
        hard_cap = self.settings.hard_cap_usd
        projected = self.accumulated_cost_usd + max(0.0, float(next_stage_cost_usd))
        projected_with_margin = projected * (1.0 + margin)

        # Hard block: projected (with margin) exceeds the absolute cap.
        if projected_with_margin > hard_cap:
            return BudgetCheckResult(
                allowed=False,
                blocked=True,
                warning=False,
                projected_total_usd=projected,
                projected_total_with_margin_usd=projected_with_margin,
                selected_budget_usd=self.selected_budget_usd,
                hard_cap_usd=hard_cap,
                safety_margin=margin,
                reason=(
                    f"Hard cap exceeded: projected total with {int(margin*100)}% margin "
                    f"= ${projected_with_margin:.2f} > hard cap ${hard_cap:.2f}."
                ),
            )

        # Soft warn: projected (with margin) exceeds the per-run budget.
        if projected_with_margin > self.selected_budget_usd:
            return BudgetCheckResult(
                allowed=True,
                blocked=False,
                warning=True,
                projected_total_usd=projected,
                projected_total_with_margin_usd=projected_with_margin,
                selected_budget_usd=self.selected_budget_usd,
                hard_cap_usd=hard_cap,
                safety_margin=margin,
                reason=(
                    f"Budget warning: projected total with margin "
                    f"${projected_with_margin:.2f} > selected budget "
                    f"${self.selected_budget_usd:.2f}."
                ),
            )

        return BudgetCheckResult(
            allowed=True,
            blocked=False,
            warning=False,
            projected_total_usd=projected,
            projected_total_with_margin_usd=projected_with_margin,
            selected_budget_usd=self.selected_budget_usd,
            hard_cap_usd=hard_cap,
            safety_margin=margin,
            reason="OK",
        )

    def must_check(self, next_stage_cost_usd: float) -> BudgetCheckResult:
        """Like `check`, but raise on hard-cap violations."""
        result = self.check(next_stage_cost_usd)
        if result.blocked:
            raise BudgetViolationError(result.reason)
        return result

    def record_actual(self, cost_usd: float) -> None:
        self.accumulated_cost_usd += max(0.0, float(cost_usd))
