"""RuntimeMode 与 Replay 模式（详细修改方案 §4 / §6）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from engines.decision.decision_service import DecisionService
from engines.decision.replay import DecisionReplayService
from engines.decision.runtime_mode import RuntimeMode, build_runtime_segment

AS_OF = datetime(2026, 8, 7, 9, 30)


def test_build_runtime_segment_explicit_fallback():
    segment = build_runtime_segment(RuntimeMode.DETERMINISTIC_FALLBACK, fallback_reason="MODEL_UNAVAILABLE")
    assert segment == {
        "runtime_mode": "DETERMINISTIC_FALLBACK",
        "fallback_used": True,
        "fallback_reason": "MODEL_UNAVAILABLE",
        "supervisor_version": None,
    }


def test_build_runtime_segment_primary_agent_has_no_fallback():
    segment = build_runtime_segment("PRIMARY_AGENT", supervisor_version="v2")
    assert segment["fallback_used"] is False
    assert segment["fallback_reason"] is None
    assert segment["supervisor_version"] == "v2"


def test_build_runtime_segment_rejects_unknown_reason():
    with pytest.raises(ValueError):
        build_runtime_segment("DETERMINISTIC_FALLBACK", fallback_reason="NOT_A_REASON")


def _save_decision(**overrides) -> dict:
    payload = {
        "query": "replay 模式测试",
        "candidates": [
            {"symbol": "600000.SH", "theme": "创新药", "sector": "医药", "theme_score": 70.0, "technical_score": 65.0, "risk_score": 20.0, "liquidity_score": 80.0, "confidence": 0.6},
        ],
        "market_regime": "rotation_market",
        "themes": ["创新药"],
        "sector": "医药",
        "decision_as_of": AS_OF,
        "data_as_of": AS_OF,
    }
    payload.update(overrides)
    return DecisionService().save_decision(**payload)


def test_exact_replay_matches_original_semantics(isolated_database):
    result = _save_decision()
    service = DecisionReplayService()
    original = service.replay(result["decision_id"], mode="original")
    exact = service.replay(result["decision_id"], mode="EXACT_REPLAY")
    assert exact["mode"] == "EXACT_REPLAY"
    # §6：EXACT_REPLAY 固定所有输入，与 original 使用相同的版本锚定
    assert exact["replay_versions"] == original["replay_versions"]
    assert exact["match"] == original["match"]


def test_counterfactual_replay_requires_override(isolated_database):
    result = _save_decision()
    replay = DecisionReplayService().replay(result["decision_id"], mode="COUNTERFACTUAL_REPLAY")
    assert replay["error"] == "COUNTERFACTUAL_OVERRIDE_REQUIRED"


def test_counterfactual_replay_reports_override_and_diff(isolated_database):
    result = _save_decision()
    replay = DecisionReplayService().replay(
        result["decision_id"], mode="COUNTERFACTUAL_REPLAY", overrides={"policy_version": "risk-policy-v3"}
    )
    assert replay["mode"] == "COUNTERFACTUAL_REPLAY"
    assert replay["counterfactual"]["overrides"] == {"policy_version": "risk-policy-v3"}
    assert replay["counterfactual"]["applied_versions"]
    # 反事实使用当前版本重算（相当于 current baseline）
    assert replay["replay_versions"].get("portfolio_rule_version") is not None


def test_invalid_mode_rejected(isolated_database):
    result = _save_decision()
    replay = DecisionReplayService().replay(result["decision_id"], mode="NOT_A_MODE")
    assert replay["error"] == "INVALID_REPLAY_MODE"
