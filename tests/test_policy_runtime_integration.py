"""P0 A-07：Policy/Risk 治理集成测试（最终决策权归 Policy/Risk，不归 LLM）。"""
from __future__ import annotations

from datetime import UTC, datetime

from app.decision_runtime import DecisionRuntime
from storage.repositories.research_repository import DecisionSnapshotRepository


class _StubClaudeAgent:
    def configured(self) -> bool:
        return False

    def run(self, **kwargs):
        raise AssertionError("fallback 模式不得调用 LLM")


class _StubFallback:
    def analyze_stock(self, symbol, as_of=None, patterns=None):
        return {"symbol": symbol, "orchestration": "local-fallback"}


def _runtime() -> DecisionRuntime:
    return DecisionRuntime(claude_agent=_StubClaudeAgent(), fallback=_StubFallback())


def _buy_payload(**proposal_overrides) -> dict:
    proposal = {
        "symbol": "600000.SH",
        "action": "BUY",
        "proposed_weight": 0.05,
        "confidence": 0.8,
        "evidence_count": 3,
    }
    proposal.update(proposal_overrides)
    return {"symbol": "600000.SH", "proposal": proposal}


def test_hard_rule_reject():
    payload = _buy_payload()
    payload["restricted_universe"] = ["600000.SH"]

    governed = _runtime()._govern(payload)

    final = governed["final_decision"]
    assert final["action"] == "REJECT"
    assert final["approved"] is False
    assert "RESTRICTED_UNIVERSE" in final["rejections"]


def test_risk_veto_overrides_llm_buy():
    """A-03 必测：LLM BUY 100% + Risk VETO → Final VETO。"""
    payload = _buy_payload(proposed_weight=1.0)
    payload["risk"] = {"veto": True, "reason": "流动性风险"}

    governed = _runtime()._govern(payload)

    final = governed["final_decision"]
    assert governed["proposal"].action == "BUY"
    assert governed["proposal"].proposed_weight == 1.0
    assert final["action"] == "VETO"
    assert final["vetoed"] is True
    assert final["approved"] is False
    assert final["approved_weight"] == 0.0


def test_position_cap_resizes_oversized_buy():
    governed = _runtime()._govern(_buy_payload(proposed_weight=1.0))

    final = governed["final_decision"]
    assert final["action"] == "BUY"
    assert final["approved"] is True
    assert final["approved_weight"] <= 0.10, "PolicyEngine 必须 resize 超额仓位"
    assert "SINGLE_POSITION_LIMIT" in final["adjustments"]


def test_insufficient_evidence_hard_reject():
    governed = _runtime()._govern(_buy_payload(evidence_count=0))

    final = governed["final_decision"]
    assert final["action"] == "REJECT"
    assert "MINIMUM_EVIDENCE" in final["rejections"]


def test_suitability_fail_blocks_actionable_advice():
    payload = _buy_payload(proposed_weight=0.05)
    payload["investor_profile"] = {
        "risk_level": "CONSERVATIVE",
        "product_risk_rating": "AGGRESSIVE",
        "expected_max_drawdown": 0.30,
        "max_drawdown_tolerance": 0.10,
    }

    governed = _runtime()._govern(payload)

    final = governed["final_decision"]
    assert final["action"] == "REJECT", "Suitability FAIL 时不得输出不符合策略的 actionable advice"
    assert final["approved"] is False
    assert final["suitability"]["suitable"] is False


def test_final_decision_matches_persisted_snapshot(isolated_database):
    now = datetime(2026, 1, 2, 16, tzinfo=UTC)

    class _FrozenGateway:
        def collect(self, requests, *, decision_time, trace=None):
            del requests, decision_time, trace
            return [], []

    runtime = DecisionRuntime(
        claude_agent=_StubClaudeAgent(),
        evidence_gateway=_FrozenGateway(),
        trusted_fallback=lambda **_: {"proposal": {"symbol": "600000.SH", "action": "HOLD", "confidence": 0.8}},
        clock=lambda: now,
    )

    result = runtime.decide(
        task_type="daily-market-decision", objective="formal snapshot integration", subjects=["600000.SH"],
        context={"skill": "daily-market-decision"}, as_of=now,
    )

    snapshot = DecisionSnapshotRepository().get_v3_for_decision(result["decision_id"])
    assert snapshot is not None
    decision = result["decision"]
    assert snapshot.proposal["proposal_id"] == decision["proposal_id"]
    assert snapshot.proposal["payload"]["action"] == decision["investment_action"]
    assert snapshot.policy["policy_result_id"] == decision["policy_result_id"]
    assert snapshot.output["final_decision"]["decision_action"] == decision["decision_action"]
    assert snapshot.output["bundle_id"] == result["bundle_id"] == decision["bundle_id"]
    lineage = {(item["type"], item["id"]) for item in snapshot.lineage}
    assert ("BUNDLE", result["bundle_id"]) in lineage
    assert ("PROPOSAL", decision["proposal_id"]) in lineage
    assert ("POLICY", decision["policy_result_id"]) in lineage
    assert ("FINAL_DECISION", decision["decision_id"]) in lineage
