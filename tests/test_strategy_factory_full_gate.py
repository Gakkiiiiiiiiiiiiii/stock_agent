from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from engines.strategy_factory import StrategyDefinition, StrategyFactory, StrategyPaperEvidenceProvider
from engines.strategy_factory.models import StrategyStatus
from storage.repositories.p2_repository import P2Repository
from storage.repositories.research_repository import DecisionRepository


class Runs:
    def __init__(self, strategy_id: str, oos_passed: bool = True):
        self.runs = {
            "backtest": {"strategy_id": strategy_id, "evaluation_type": "BACKTEST", "passed": True, "metrics": {"max_drawdown": .1, "turnover": 1}},
            "oos": {"strategy_id": strategy_id, "evaluation_type": "OOS", "passed": oos_passed, "metrics": {"oos_days": 60, "excess_sharpe": .6, "pbo": .1}},
        }
    def get(self, run_id):
        return self.runs.get(run_id)


def _definition() -> StrategyDefinition:
    return StrategyDefinition(name="paper evidence", universe={"name": "all"}, entry_rules=[{"op": "gt"}], exit_rules=[{"op": "lt"}])


def test_evaluation_run_mismatch_and_failed_oos_are_rejected():
    definition = _definition()
    factory = StrategyFactory(evaluation_runner=Runs(definition.strategy_id), paper_evidence_provider=lambda _: {"passed": True, "observed_trading_days": 20})
    factory.generate(definition)
    factory.validate_static(definition.strategy_id)
    with pytest.raises(ValueError, match="missing or incompatible"):
        factory.evaluate_backtest(definition.strategy_id, "oos")

    definition = _definition()
    factory = StrategyFactory(evaluation_runner=Runs(definition.strategy_id, oos_passed=False), paper_evidence_provider=lambda _: {"passed": True, "observed_trading_days": 20})
    factory.generate(definition)
    factory.validate_static(definition.strategy_id)
    factory.evaluate_backtest(definition.strategy_id, "backtest")
    assert factory.validate_oos(definition.strategy_id, "oos").status == StrategyStatus.REJECTED


def test_default_paper_provider_collects_persisted_execution_and_outcome(isolated_database):
    strategy_id = str(uuid4())
    decisions = DecisionRepository()
    decision = decisions.create(skill_slug="daily-market-decision", market_regime="rotation", candidates=[{"symbol": "600000.SH"}], portfolio_advice={"actions": []})
    decisions.add_outcome(decision_id=decision.id, evaluation_date=date(2026, 8, 10), horizon_days=1, excess_return=.03, max_drawdown=.02)
    p2 = P2Repository()
    intent = p2.create_trade_intent(client_order_id="strategy-paper-order", decision_id=decision.id, strategy_id=strategy_id, symbol="600000.SH", target_version="v1", payload={}, status="CREATED", created_at=datetime(2026, 8, 9, tzinfo=UTC))
    order = p2.add_order(id=str(uuid4()), trade_intent_id=intent.id, mode="PAPER", status="FILLED", quantity=100, limit_price=None, broker_order_id=None, rejection_reasons=[], created_at=datetime(2026, 8, 9, tzinfo=UTC))
    p2.add_fill(execution_order_id=order.id, quantity=100, price=10, broker_fill_id="fill-1", filled_at=datetime(2026, 8, 9, tzinfo=UTC))
    evidence = StrategyPaperEvidenceProvider(p2, decisions, minimum_days=1).collect(strategy_id)
    assert evidence["passed"] is True
    assert evidence["decision_count"] == evidence["order_count"] == evidence["fill_count"] == 1


def test_paper_evidence_shortage_cannot_advance_lifecycle():
    definition = _definition()
    factory = StrategyFactory(evaluation_runner=Runs(definition.strategy_id), paper_evidence_provider=lambda _: {"passed": False, "observed_trading_days": 0})
    factory.generate(definition)
    factory.validate_static(definition.strategy_id)
    factory.evaluate_backtest(definition.strategy_id, "backtest")
    factory.validate_oos(definition.strategy_id, "oos")
    assert factory.paper_track(definition.strategy_id).status == StrategyStatus.REJECTED
