"""Production paper-trading evidence collected from persisted execution data."""
from __future__ import annotations

from statistics import mean

from storage.repositories.p2_repository import P2Repository
from storage.repositories.research_repository import DecisionRepository


class StrategyPaperEvidenceProvider:
    """Collect auditable strategy paper evidence; missing observations never pass."""

    def __init__(self, p2_repository: P2Repository | None = None, decision_repository: DecisionRepository | None = None, minimum_days: int = 20, max_error_rate: float = .05) -> None:
        self.p2_repository = p2_repository or P2Repository()
        self.decision_repository = decision_repository or DecisionRepository()
        self.minimum_days = minimum_days
        self.max_error_rate = max_error_rate

    def collect(self, strategy_id: str) -> dict:
        execution = self.p2_repository.list_strategy_execution_evidence(strategy_id)
        decision_ids = [item.decision_id for item in execution["intents"]]
        outcomes = self.decision_repository.list_outcomes_for_decisions(decision_ids)
        days = {
            item.created_at.date() for item in execution["intents"] if item.created_at
        } | {
            item.filled_at.date() for item in execution["fills"] if item.filled_at
        } | {
            item.evaluation_date for item in outcomes if item.evaluation_date
        }
        terminal_errors = {"REJECTED", "CANCELED", "EXPIRED"}
        error_count = sum(item.status in terminal_errors for item in execution["orders"])
        order_count = len(execution["orders"])
        error_rate = error_count / order_count if order_count else 1.0
        drawdowns = [float(item.max_drawdown) for item in outcomes if item.max_drawdown is not None]
        excess = [float(item.excess_return) for item in outcomes if item.excess_return is not None]
        observed_trading_days = len(days)
        passed = (
            observed_trading_days >= self.minimum_days
            and bool(decision_ids)
            and bool(execution["orders"])
            and bool(execution["fills"])
            and bool(outcomes)
            and error_rate <= self.max_error_rate
        )
        return {
            "strategy_id": strategy_id,
            "observed_trading_days": observed_trading_days,
            "decision_count": len(set(decision_ids)),
            "order_count": order_count,
            "fill_count": len(execution["fills"]),
            "outcome_count": len(outcomes),
            "max_drawdown": max(drawdowns) if drawdowns else None,
            "excess_return": mean(excess) if excess else None,
            "error_rate": error_rate,
            "passed": passed,
        }
