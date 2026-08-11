"""决策回放（§27）：DecisionReplayService + POST /api/v1/decision/{id}/replay。

落库产物（候选顺序 / portfolio_advice / benchmark_route）在 seed 时通过运行
与回放相同的服务构造，因此 match=True 是对确定性的真实验证，而非自我印证。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from engines.decision.decision_service import DecisionService
from engines.decision.replay import DecisionReplayService
from engines.opportunity.service import OpportunityRankingService
from engines.portfolio.pipeline import run_portfolio_pipeline
from engines.versioning import get_version
from storage.repositories.research_repository import DecisionRepository

AS_OF = datetime(2026, 8, 7, 9, 30)  # naive：与 sqlite DateTime 往返一致

# 原始候选（输入形状）；分数拉开使排序结果与录入顺序不同。
RAW_CANDIDATES = [
    {"symbol": "600000.SH", "theme": "创新药", "sector": "医药", "theme_score": 70.0, "technical_score": 65.0, "risk_score": 20.0, "liquidity_score": 80.0, "confidence": 0.6},
    {"symbol": "600001.SH", "theme": "创新药", "sector": "医药", "theme_score": 92.0, "technical_score": 85.0, "risk_score": 10.0, "liquidity_score": 85.0, "confidence": 0.8},
    {"symbol": "600002.SH", "theme": "创新药", "sector": "医药", "theme_score": 55.0, "technical_score": 50.0, "risk_score": 30.0, "liquidity_score": 75.0, "confidence": 0.5},
]

MARKET_FEATURES = {"market_code": "CN_A", "index_trend": "up", "volatility": 0.18}


def _chain_artifacts():
    """运行与回放相同的确定性链，产出落库用的 candidates 顺序与 portfolio_advice。"""
    as_of = AS_OF.isoformat()
    ranking = OpportunityRankingService().rank([dict(item) for item in RAW_CANDIDATES], {"as_of": as_of})
    order = [item["symbol"] for item in ranking["ranked"]]
    by_symbol = {item["symbol"]: dict(item) for item in RAW_CANDIDATES}
    score_map = {item["symbol"]: item["opportunity_score"] for item in ranking["ranked"]}
    ordered_candidates = []
    for symbol in order:
        candidate = by_symbol[symbol]
        pipeline_candidate = dict(candidate)
        pipeline_candidate["opportunity_score"] = score_map[symbol]
        ordered_candidates.append((candidate, pipeline_candidate))
    portfolio = run_portfolio_pipeline(
        [item[1] for item in ordered_candidates],
        [],
        context={"regime": "rotation_market", "as_of": as_of},
    )
    return [item[0] for item in ordered_candidates], portfolio


def _seed_decision(**overrides) -> str:
    candidates, portfolio = _chain_artifacts()
    payload = {
        "query": "创新药主题机会",
        "candidates": candidates,
        "portfolio_advice": portfolio,
        "market_regime": "rotation_market",
        "market_features": dict(MARKET_FEATURES),
        "market_feature_version": get_version("market_feature_version"),
        "themes": ["创新药"],
        "sector": "医药",
        "decision_as_of": AS_OF,
        "data_as_of": AS_OF,
    }
    payload.update(overrides)
    return str(DecisionService().save_decision(**payload)["decision_id"])


def test_original_replay_matches_stored_artifacts(isolated_database):
    decision_id = _seed_decision()
    result = DecisionReplayService().replay(decision_id, mode="original")

    assert result["decision_id"] == decision_id
    assert result["mode"] == "original"
    assert result["match"] is True
    assert result["diffs"] == []
    assert result["version_mismatch"] is False
    # original 模式：版本锚定到决策记录版本
    assert result["replay_versions"]["market_feature_version"] == get_version("market_feature_version")
    assert result["replay_versions"]["portfolio_rule_version"] == "portfolio_rules_v2"
    assert result["replay_uses_current_code"] is True
    assert result["market_feature_source"] == "decision"
    # 重放确实跑了排序与组合流水线
    assert [item["symbol"] for item in result["replay_output"]["ranked"]["ranked"]] == [
        item["symbol"] for item in result["original_output"]["candidates"]
    ]
    assert result["replay_output"]["portfolio"]["actions"]
    assert result["replay_output"]["benchmark_route"]["primary_benchmark"] == "000991.SH"


def test_tampered_stored_portfolio_action_breaks_match(isolated_database):
    decision_id = _seed_decision()
    decision = DecisionRepository().get(decision_id)
    tampered = dict(decision.portfolio_advice)
    actions = [dict(item) for item in tampered["actions"]]
    actions[0]["target_weight"] = round(actions[0]["target_weight"] + 0.01, 4)
    tampered["actions"] = actions
    DecisionRepository().update(decision_id, portfolio_advice=tampered)

    result = DecisionReplayService().replay(decision_id, mode="original")
    assert result["match"] is False
    fields = {diff["field"] for diff in result["diffs"]}
    assert f"portfolio_action:{actions[0]['symbol']}" in fields
    diff = next(item for item in result["diffs"] if item["field"].startswith("portfolio_action:"))
    assert diff["stored"]["target_weight"] != diff["replayed"]["target_weight"]


def test_tampered_candidate_order_breaks_match(isolated_database):
    decision_id = _seed_decision()
    decision = DecisionRepository().get(decision_id)
    reordered = list(reversed(decision.candidates))
    DecisionRepository().update(decision_id, candidates=reordered)

    result = DecisionReplayService().replay(decision_id, mode="original")
    assert result["match"] is False
    assert any(diff["field"] == "candidate_order" for diff in result["diffs"])


def test_current_mode_reports_current_versions_and_mismatch(isolated_database):
    # 记录版本与当前代码版本不一致 → version_mismatch=True 并给出差异明细
    decision_id = _seed_decision(market_feature_version="market_feature_v1_legacy")
    result = DecisionReplayService().replay(decision_id, mode="current")

    assert result["mode"] == "current"
    assert result["replay_versions"]["market_feature_version"] == get_version("market_feature_version")
    assert result["replay_versions"]["portfolio_rule_version"] == "portfolio_rules_v2"
    assert result["version_mismatch"] is True
    detail = result["version_mismatch_details"]["market_feature_version"]
    assert detail == {"recorded": "market_feature_v1_legacy", "current": get_version("market_feature_version")}
    # 同一批落库输入 + 相同算法 → 输出仍应与落库产物一致
    assert result["match"] is True


def test_original_mode_flags_version_mismatch(isolated_database):
    decision_id = _seed_decision(market_feature_version="market_feature_v1_legacy")
    result = DecisionReplayService().replay(decision_id, mode="original")
    assert result["version_mismatch"] is True
    assert result["replay_versions"]["market_feature_version"] == "market_feature_v1_legacy"


def test_replay_unknown_decision_returns_not_found(isolated_database):
    result = DecisionReplayService().replay("no-such-decision")
    assert result == {"error": "DECISION_NOT_FOUND", "decision_id": "no-such-decision"}


def test_replay_rejects_unknown_mode(isolated_database):
    decision_id = _seed_decision()
    result = DecisionReplayService().replay(decision_id, mode="sideways")
    assert result["error"] == "INVALID_REPLAY_MODE"


def test_replay_is_deterministic(isolated_database):
    decision_id = _seed_decision()
    service = DecisionReplayService()
    assert service.replay(decision_id) == service.replay(decision_id)


def test_api_replay_round_trip_and_default_mode(isolated_database):
    from app.api import app

    decision_id = _seed_decision()
    client = TestClient(app)

    response = client.post(f"/api/v1/decision/{decision_id}/replay", json={"mode": "current"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "current"
    assert body["match"] is True

    # 不带 body → 默认 original
    response = client.post(f"/api/v1/decision/{decision_id}/replay")
    assert response.status_code == 200
    assert response.json()["mode"] == "original"

    # 非法 mode → 422
    assert client.post(f"/api/v1/decision/{decision_id}/replay", json={"mode": "sideways"}).status_code == 422


def test_api_replay_unknown_decision_returns_404(isolated_database):
    from app.api import app

    client = TestClient(app)
    response = client.post("/api/v1/decision/no-such-decision/replay", json={"mode": "original"})
    assert response.status_code == 404
