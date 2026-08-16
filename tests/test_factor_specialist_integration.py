"""FactorSpecialist 接 stock_factor /api/v1/alpha/score（设计文档 §14.2/§79/§62/§90）。"""
from __future__ import annotations

from agent.contracts import AgentTask
from agent.decision_quality import compute_decision_quality
from agent.specialists.factor import FactorSpecialist


class FakeFactorClient:
    def __init__(self, response: dict | None = None, raise_error: bool = False) -> None:
        self.response = response or {}
        self.raise_error = raise_error
        self.requests: list = []

    def score_alpha(self, request):
        self.requests.append(request)
        if self.raise_error:
            raise RuntimeError("factor service unreachable")
        return self.response


def _task() -> AgentTask:
    return AgentTask(task_type="daily_market_decision", objective="test")


def test_factor_specialist_calls_alpha_score_and_returns_evidence():
    response = {
        "factor_set_version": "factor-set-abc",
        "market_snapshot_id": "mds-xyz",
        "as_of": "2026-08-14",
        "scores": [
            {"symbol": "600519.SH", "score": 0.73, "rank": 1, "evidence": [{"factor_id": "f-1", "contribution": 0.31}]},
            {"symbol": "300750.SZ", "score": 0.42, "rank": 2, "evidence": [{"factor_id": "f-1", "contribution": 0.21}]},
        ],
    }
    client = FakeFactorClient(response)
    specialist = FactorSpecialist(registry=None, context={"universe": ["600519.SH", "300750.SZ"]}, factor_client=client)
    artifact = specialist(_task(), None)
    assert artifact.warnings == []
    assert artifact.tool_calls == 1
    assert client.requests and client.requests[0].symbols == ["600519.SH", "300750.SZ"]
    conclusion = artifact.conclusion
    # §79 验收：Decision Artifact 中必须出现真实 factor_set_version / scores / evidence
    assert conclusion["factor_set_version"] == "factor-set-abc"
    assert conclusion["market_snapshot_id"] == "mds-xyz"
    assert len(conclusion["factor_scores"]) == 2
    evidence = conclusion["factor_evidence"][0]
    assert evidence["score"] == 0.73
    assert evidence["rank"] == 1
    assert evidence["snapshot_id"] == "mds-xyz"
    assert evidence["evidence"][0]["factor_id"] == "f-1"


def test_factor_specialist_degrades_explicitly_when_service_unavailable():
    specialist = FactorSpecialist(registry=None, context={"universe": ["600519.SH"]}, factor_client=FakeFactorClient(raise_error=True))
    artifact = specialist(_task(), None)
    # §90：不允许伪造 factor evidence，必须显式 FACTOR_UNAVAILABLE
    assert artifact.warnings == ["FACTOR_UNAVAILABLE"]
    assert artifact.conclusion["factor_evidence"] == []
    assert artifact.conclusion["decision_quality"] == "DEGRADED"
    assert artifact.confidence < 0.7


def test_factor_specialist_requires_universe():
    specialist = FactorSpecialist(registry=None, context={}, factor_client=FakeFactorClient({}))
    artifact = specialist(_task(), None)
    assert artifact.warnings == ["FACTOR_UNIVERSE_NOT_PROVIDED"]
    assert artifact.conclusion["decision_quality"] == "DEGRADED"


def test_decision_quality_degraded_on_factor_unavailable():
    artifacts = [
        {"agent": "MarketAgent", "warnings": [], "confidence": 0.8},
        {"agent": "FactorAgent", "warnings": ["FACTOR_UNAVAILABLE"], "confidence": 0.4},
    ]
    assert compute_decision_quality(artifacts) == "DEGRADED"


def test_decision_quality_high_when_core_roles_have_evidence():
    artifacts = [
        {"agent": role, "warnings": [], "confidence": 0.8}
        for role in ("MarketAgent", "ResearchAgent", "TechnicalAgent", "FactorAgent", "RiskAgent")
    ]
    assert compute_decision_quality(artifacts) == "HIGH"


def test_decision_quality_low_on_errors():
    artifacts = [{"agent": "MarketAgent", "warnings": [], "confidence": 0.8}]
    assert compute_decision_quality(artifacts, errors=[{"code": "X"}]) == "LOW"
