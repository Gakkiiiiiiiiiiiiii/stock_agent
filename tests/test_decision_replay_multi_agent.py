from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from engines.decision.replay import DecisionReplayService
from storage.repositories.p2_repository import P2Repository
from storage.repositories.research_repository import DecisionRepository


def _seed_agent_run(decision_id: str) -> str:
    repo = P2Repository()
    run = repo.create_agent_run(id=str(uuid4()), task_type="daily_market_decision", objective="replay", status="COMPLETED", participating_agents=["MarketAgent", "TechnicalAgent", "RiskAgent"], usage={})
    repo.add_subtask(id=str(uuid4()), agent_run_id=run.id, agent="MarketAgent", status="COMPLETED", conclusion={"get_market_regime": {"regime": {"primary_regime": "artifact_regime"}}}, evidence_refs=[], confidence=1, usage={})
    repo.add_subtask(id=str(uuid4()), agent_run_id=run.id, agent="TechnicalAgent", status="COMPLETED", conclusion={"technical": {"candidates": [{"symbol": "600000.SH", "theme_score": 90, "technical_score": 80, "risk_score": 10, "liquidity_score": 80, "confidence": .8}]}}, evidence_refs=[], confidence=1, usage={})
    repo.add_subtask(id=str(uuid4()), agent_run_id=run.id, agent="RiskAgent", status="COMPLETED", conclusion={"evaluate_portfolio_risk": {"veto": True}}, evidence_refs=[], confidence=1, usage={})
    repo.add_conflict(agent_run_id=run.id, dimension="position", opinions=[], resolution_policy="owner", resolved_value={"value": "reduce"}, resolved_by="RiskAgent")
    return run.id


def test_multi_agent_replay_uses_nested_regime_conflicts_and_detects_risk_diff(isolated_database):
    decisions = DecisionRepository()
    decision = decisions.create(
        decision_as_of=datetime(2026, 8, 10, 9, 30), market_regime="stored_regime", thesis={"risk_veto": False},
        candidates=[{"symbol": "600000.SH", "theme_score": 90, "technical_score": 80, "risk_score": 10, "liquidity_score": 80, "confidence": .8}], portfolio_advice={}, benchmark_route={},
    )
    run_id = _seed_agent_run(decision.id)
    decisions.attach_agent_run(decision.id, run_id)
    result = DecisionReplayService().replay(decision.id, mode="multi_agent")
    assert result["multi_agent"]["available"] is True
    assert result["replay_output"]["multi_agent_context"]["market_regime"] == "artifact_regime"
    assert result["replay_output"]["multi_agent_context"]["resolved_conflicts"] == {"position": "reduce"}
    assert result["replay_output"]["risk_veto"] is True
    assert {item["field"] for item in result["diffs"]} >= {"market_regime", "risk_veto"}


def test_multi_agent_replay_without_agent_run_reports_unavailable(isolated_database):
    decision = DecisionRepository().create(candidates=[], portfolio_advice={}, benchmark_route={})
    result = DecisionReplayService().replay(decision.id, mode="multi_agent")
    assert result["multi_agent"] == {"available": False, "reason": "AGENT_RUN_NOT_ATTACHED"}
