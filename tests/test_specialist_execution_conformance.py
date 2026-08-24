from agent.contracts import AgentTask, SpecialistArtifact
from agent.specialists.factor import FactorSpecialist
from agent.specialists.market import MarketSpecialist
from agent.specialists.portfolio import PortfolioSpecialist
from agent.specialists.research import ResearchSpecialist
from agent.specialists.risk import RiskSpecialist
from agent.specialists.technical import TechnicalSpecialist


class Registry:
    def execute(self, name, payload):
        return {
            "get_market_features": {"evidence_id": "ev-market-features"},
            "get_market_regime": {"evidence_id": "ev-market-regime", "regime": "risk_on"},
            "get_sector_strength": {"evidence_id": "ev-sector"},
            "get_technical_evidence": {"evidence_id": "ev-technical", "signal": "breakout"},
            "retrieve_relevant_context": {"evidence_refs": ["ev-research"], "items": ["claim"]},
            "construct_portfolio_v2": {"evidence_refs": ["ev-portfolio"], "targets": [{"symbol": "600000.SH", "weight": 0.1}]},
            "get_portfolio_risk_inputs": {"evidence_refs": ["ev-risk"], "risk_level": "low", "veto": False},
        }[name]


class Shared:
    def dependency_artifacts(self, task_id):
        return {}


class FactorClient:
    def score_alpha(self, request):
        return {"evidence_refs": ["ev-factor"], "factor_set_version": "f1", "scores": [{"symbol": request.symbols[0], "score": 0.8, "evidence_id": "ev-factor-score"}]}


def test_all_six_specialists_execute_to_v2_success_with_actual_refs():
    task = AgentTask(task_type="daily_market_decision", objective="test")
    registry = Registry()
    shared = Shared()
    specialists = [
        MarketSpecialist(registry, {}),
        ResearchSpecialist(registry, {}),
        TechnicalSpecialist(registry, {"candidate_symbols": ["600000.SH"]}),
        FactorSpecialist(registry, {"universe": ["600000.SH"]}, factor_client=FactorClient()),
        PortfolioSpecialist(registry, {"candidates": ["600000.SH"]}),
        RiskSpecialist(registry, {"positions": [{"symbol": "600000.SH"}]}),
    ]
    artifacts = [specialist(task, shared) for specialist in specialists]
    assert all(isinstance(item, SpecialistArtifact) for item in artifacts)
    assert all(item.status.value == "SUCCESS" for item in artifacts)
    assert all(item.evidence_refs and item.artifact_hash and item.tool_usage.calls >= 0 for item in artifacts)


def test_missing_refs_are_explicitly_degraded_and_risk_failure_is_not_safe():
    task = AgentTask(task_type="daily_market_decision", objective="test")
    class EmptyRegistry:
        def execute(self, name, payload):
            return {}

    empty = EmptyRegistry()
    technical = TechnicalSpecialist(empty, {"candidate_symbols": ["600000.SH"]})(task, Shared())
    assert technical.status.value == "DEGRADED"
    assert "EVIDENCE_REFS_MISSING" in technical.unknowns
    risk = RiskSpecialist(empty, {})(task, Shared())
    assert risk.status.value == "FAILED"
    assert risk.conclusion["veto"] is True
    assert "RISK_FAILED" in risk.unknowns
