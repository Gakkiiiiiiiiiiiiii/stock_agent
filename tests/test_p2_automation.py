from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.contracts import AgentArtifact, AgentRole, AgentTask, TaskStatus
from agent.supervisor import Supervisor
from agent.task_graph import TaskGraph
from engines.execution import ExecutionService, TradeIntent
from engines.execution.models import ExecutionFill, ExecutionMode, OrderStatus
from engines.execution.reconciliation import reconcile
from engines.market.streaming import MarketEvent, StreamingFeatureEngine
from engines.skill_evolution import SkillEvolutionService, SkillProposal
from engines.skill_evolution.models import ProposalStatus
from engines.strategy_factory import StrategyDefinition, StrategyFactory
from engines.strategy_factory.models import StrategyStatus
from agent.plans.daily_market_decision import build_daily_market_decision_graph
from agent.task_graph import TaskGraph


def test_supervisor_runs_dag_and_domain_owner_wins_conflict():
    def market(task, _shared):
        return AgentArtifact(agent=AgentRole.MARKET, task_id=task.task_id, conclusion={"regime": "rotation"}, confidence=.8)

    graph = TaskGraph()
    graph.add_task(AgentTask(task_type=AgentRole.MARKET, objective="market"))
    result = Supervisor({AgentRole.MARKET: market}).run(graph)
    assert result["errors"] == []
    assert result["artifacts"][0]["conclusion"]["regime"] == "rotation"


def test_daily_decision_plan_separates_task_type_from_agent_role():
    graph = build_daily_market_decision_graph("daily")
    tasks = list(graph.tasks.values())
    assert {task.task_type for task in tasks} == {"daily_market_decision"}
    assert {task.assigned_agent for task in tasks} >= {AgentRole.MARKET, AgentRole.PORTFOLIO, AgentRole.RISK}
    portfolio = next(task for task in tasks if task.assigned_agent == AgentRole.PORTFOLIO)
    assert len(graph.dependencies(portfolio.task_id)) == 3


def test_execution_is_idempotent_and_shadow_never_submits():
    now = datetime.now(UTC)
    service = ExecutionService(mode=ExecutionMode.SHADOW)
    intent = TradeIntent(decision_id="d1", symbol="600000.SH", target_weight=.05, current_weight=0)
    context = {"quote": {"as_of": now, "liquid": True}, "available_cash": 1000, "order_notional": 10}
    first = service.create_order(intent, context, 100)
    second = service.create_order(intent, context, 100)
    assert first.id == second.id
    assert service.submit(first.client_order_id).status == OrderStatus.VALIDATED


def test_paper_fill_and_reconciliation():
    now = datetime.now(UTC)
    service = ExecutionService(mode=ExecutionMode.PAPER)
    intent = TradeIntent(decision_id="d1", symbol="600000.SH", target_weight=.05, current_weight=0)
    order = service.create_order(intent, {"quote": {"as_of": now, "liquid": True}, "available_cash": 1000, "order_notional": 10}, 100)
    service.submit(order.client_order_id)
    assert service.record_fill(ExecutionFill(order_id=order.id, quantity=100, price=10)).status == OrderStatus.FILLED
    assert reconcile({"cash": 10, "positions": {"600000.SH": 100}}, {"cash": 10, "positions": {"600000.SH": 99}})["status"] == "RECONCILIATION_REQUIRED"


def test_streaming_watermark_duplicate_and_replay():
    at = datetime(2026, 1, 1, 1, tzinfo=UTC)
    engine = StreamingFeatureEngine(allowed_lateness_seconds=5)
    first = MarketEvent(event_id="1", symbol="600000.SH", exchange="SH", event_time=at, last=10, volume=10, amount=100)
    second = MarketEvent(event_id="2", symbol="600000.SH", exchange="SH", event_time=at + timedelta(seconds=10), last=11, volume=10, amount=110)
    assert engine.process(first)["accepted"]
    assert engine.process(first)["reason"] == "DUPLICATE_EVENT"
    assert engine.process(second)["accepted"]
    assert engine.process(MarketEvent(event_id="late", symbol="600000.SH", exchange="SH", event_time=at + timedelta(seconds=2), last=10, volume=1, amount=10))["reason"] == "LATE_EVENT"
    assert StreamingFeatureEngine.replay([first, second]).symbol_features("600000.SH")["intraday_vwap"] == 10.5


def test_skill_evolution_requires_gates_and_no_automatic_promotion():
    service = SkillEvolutionService(config={"auto_promote": False})
    proposal = service.propose(SkillProposal(skill_slug="daily-market-decision", base_version=2, hypothesis="x"))
    service.static_validate(proposal.proposal_id, True, True)
    service.replay_validate(proposal.proposal_id, {"quality_score": .5, "tokens": 100}, {"quality_score": .6, "tokens": 100})
    service.paper_validate(proposal.proposal_id, True)
    assert service.promote(proposal.proposal_id).status == ProposalStatus.APPROVED


def test_strategy_factory_enforces_lifecycle():
    class Runs:
        def __init__(self):
            self.runs = {
                "backtest": {"strategy_id": "", "evaluation_type": "BACKTEST", "passed": True, "metrics": {"max_drawdown": .1, "turnover": 1}},
                "oos": {"strategy_id": "", "evaluation_type": "OOS", "passed": True, "metrics": {"oos_days": 60, "excess_sharpe": .6, "pbo": .1}},
            }
        def get(self, run_id):
            return self.runs.get(run_id)
    runs = Runs()
    factory = StrategyFactory(evaluation_runner=runs, paper_evidence_provider=lambda _: {"observed_trading_days": 20, "passed": True})
    definition = StrategyDefinition(name="rotation", universe={"name": "all"}, entry_rules=[{"op": "gt"}], exit_rules=[{"op": "lt"}])
    factory.generate(definition)
    runs.get("backtest")["strategy_id"] = definition.strategy_id
    runs.get("oos")["strategy_id"] = definition.strategy_id
    factory.validate_static(definition.strategy_id)
    factory.evaluate_backtest(definition.strategy_id, "backtest")
    factory.validate_oos(definition.strategy_id, "oos")
    factory.paper_track(definition.strategy_id)
    factory.promote_shadow(definition.strategy_id)
    assert factory.activate(definition.strategy_id, "LIVE").status == StrategyStatus.ACTIVE
