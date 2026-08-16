"""DecisionSnapshot 落库与 Replay 关联（设计文档 §26/§27/§82/§109）。"""
from __future__ import annotations

from datetime import datetime

from engines.decision.decision_service import DecisionService
from engines.decision.replay import DecisionReplayService
from storage.repositories.research_repository import DecisionSnapshotRepository

AS_OF = datetime(2026, 8, 7, 9, 30)

SNAPSHOT_INPUT = {
    "market": {"snapshot_id": "mds-xyz", "data_version": "sha256:abc"},
    "content": {"snapshot_id": "content-1", "contract_version": "content-factor-signal.v2"},
    "factor": {"factor_set_version": "factor-set-abc", "factor_model_version": "alpha-v3"},
    "strategy": {"strategy_version": "daily-market-decision@v12"},
    "agent": {"agent_version": "v1", "prompt_version": "prompt-7", "model_name": "test-model", "model_version": "m-1"},
    "portfolio": {"portfolio_policy_version": "portfolio_rules_v2"},
    "risk": {"risk_policy_version": "risk-policy-v1"},
    "lineage": [
        {"type": "MARKET_SNAPSHOT", "id": "mds-xyz", "version": "sha256:abc"},
        {"type": "FACTOR_SET", "id": "factor-set-abc"},
    ],
}


def _save_decision(**overrides):
    payload = {
        "query": "DecisionSnapshot 测试",
        "candidates": [
            {"symbol": "600000.SH", "theme": "创新药", "sector": "医药", "theme_score": 70.0, "technical_score": 65.0, "risk_score": 20.0, "liquidity_score": 80.0, "confidence": 0.6},
        ],
        "market_regime": "rotation_market",
        "themes": ["创新药"],
        "sector": "医药",
        "decision_as_of": AS_OF,
        "data_as_of": AS_OF,
        "decision_snapshot": dict(SNAPSHOT_INPUT),
        "decision_quality": "HIGH",
    }
    payload.update(overrides)
    return DecisionService().save_decision(**payload)


def test_save_decision_persists_decision_snapshot(isolated_database):
    result = _save_decision()
    assert result.get("decision_snapshot_id")
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot is not None
    assert snapshot.market["snapshot_id"] == "mds-xyz"
    assert snapshot.content["contract_version"] == "content-factor-signal.v2"
    assert snapshot.factor["factor_set_version"] == "factor-set-abc"
    assert snapshot.strategy["strategy_version"] == "daily-market-decision@v12"
    assert snapshot.agent["model_version"] == "m-1"
    assert snapshot.portfolio["portfolio_policy_version"] == "portfolio_rules_v2"
    assert snapshot.risk["risk_policy_version"] == "risk-policy-v1"
    assert snapshot.lineage[0]["type"] == "MARKET_SNAPSHOT"
    assert snapshot.decision_quality == "HIGH"
    # 详细修改方案 §4/§5：v2 Schema 与显式 runtime 段
    assert snapshot.schema_version == "decision.snapshot.v2"
    assert snapshot.runtime["runtime_mode"] == "DETERMINISTIC_FALLBACK"  # 无 agent_run_id
    assert snapshot.runtime["fallback_used"] is True
    assert snapshot.inputs["market_snapshot_ids"] == ["mds-xyz"]
    assert snapshot.inputs["factor_set_ids"] == ["factor-set-abc"]
    assert snapshot.proposal["candidates"] == ["600000.SH"]
    assert snapshot.policy["policy_version"] == "risk-policy-v1"
    assert "final_decision" in snapshot.output


def test_save_decision_defaults_version_anchors_without_input(isolated_database):
    result = _save_decision(decision_snapshot=None, decision_quality=None)
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot is not None
    # 未提供时从决策版本字段退化重建（§82）
    assert snapshot.portfolio["portfolio_policy_version"]
    assert snapshot.strategy.get("skill_contract_hash") is None or isinstance(snapshot.strategy.get("skill_contract_hash"), (str, type(None)))


def test_replay_returns_decision_snapshot(isolated_database):
    result = _save_decision()
    replay = DecisionReplayService().replay(result["decision_id"], mode="original")
    assert replay.get("decision_snapshot") is not None
    assert replay["decision_snapshot"]["market"]["snapshot_id"] == "mds-xyz"
    assert replay["decision_snapshot"]["factor"]["factor_set_version"] == "factor-set-abc"
    assert replay["decision_snapshot"]["decision_quality"] == "HIGH"


def test_save_decision_records_primary_agent_runtime(isolated_database):
    # 详细修改方案 §4：有 agent_run_id 时 runtime_mode = PRIMARY_AGENT，fallback_used=False
    result = _save_decision(agent_run_id="run-1")
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot.runtime["runtime_mode"] == "PRIMARY_AGENT"
    assert snapshot.runtime["fallback_used"] is False
    assert snapshot.runtime["fallback_reason"] is None


def test_save_decision_accepts_explicit_runtime(isolated_database):
    runtime = {"runtime_mode": "DETERMINISTIC_FALLBACK", "fallback_used": True, "fallback_reason": "MODEL_UNAVAILABLE"}
    result = _save_decision(decision_snapshot={**SNAPSHOT_INPUT, "runtime": runtime})
    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot.runtime["fallback_reason"] == "MODEL_UNAVAILABLE"
