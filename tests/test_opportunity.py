"""engines/opportunity 测试：资格过滤机器码、评分权重、排序确定性与 §11.2 形状、service meta。"""
from __future__ import annotations

from engines.opportunity.candidate import OpportunityCandidate
from engines.opportunity.eligibility import evaluate_eligibility
from engines.opportunity.ranker import rank_opportunities
from engines.opportunity.scorer import score_candidate
from engines.opportunity.service import OpportunityRankingService

TEST_CONFIG = {
    "version": "opportunity_ranking_v1",
    "neutral_component_score": 50.0,
    "weights": {"theme": 0.25, "technical": 0.20, "alpha": 0.20, "regime_fit": 0.15, "knowledge": 0.10, "risk": 0.10},
    "eligibility": {"min_liquidity_score": 20.0, "min_data_coverage": 0.60},
}


def _candidate(**overrides) -> OpportunityCandidate:
    base = {
        "symbol": "AAA",
        "theme": "AI",
        "sector": "TMT",
        "technical_score": 80.0,
        "factor_score": 70.0,
        "theme_score": 90.0,
        "regime_fit_score": 60.0,
        "liquidity_score": 80.0,
        "risk_score": 20.0,
        "knowledge_score": 50.0,
        "confidence": 0.7,
        "evidence_ids": ["ev-1", "ev-2"],
        "trigger_conditions": ["breakout"],
        "invalidation_conditions": ["thesis_broken"],
    }
    base.update(overrides)
    return OpportunityCandidate.model_validate(base)


# ---- eligibility -----------------------------------------------------------


def test_eligibility_passes_clean_candidate():
    verdict = evaluate_eligibility(_candidate(), {"quote_available": True, "data_coverage": 0.9})
    assert verdict == {"symbol": "AAA", "eligible": True, "reject_reasons": []}


def test_eligibility_each_reject_code():
    assert "QUOTE_MISSING" in evaluate_eligibility(_candidate(), {"quote_available": False})["reject_reasons"]
    assert "SUSPENDED" in evaluate_eligibility(_candidate(), {"suspended": True})["reject_reasons"]
    assert "LIQUIDITY_TOO_LOW" in evaluate_eligibility(_candidate(liquidity_score=10.0), {})["reject_reasons"]
    assert "DATA_COVERAGE_LOW" in evaluate_eligibility(_candidate(), {"data_coverage": 0.3})["reject_reasons"]
    assert "THEME_INVALIDATED" in evaluate_eligibility(_candidate(), {"theme_invalidated": True})["reject_reasons"]
    assert "TECHNICAL_INVALIDATED" in evaluate_eligibility(_candidate(), {"technical_invalidated": True})["reject_reasons"]
    assert "RISK_LIMIT_EXCEEDED" in evaluate_eligibility(
        _candidate(), {"requested_weight": 0.2, "risk_cap": 0.1}
    )["reject_reasons"]


def test_eligibility_collects_multiple_reasons():
    verdict = evaluate_eligibility(_candidate(), {"quote_available": False, "suspended": True})
    assert verdict["eligible"] is False
    assert verdict["reject_reasons"] == ["QUOTE_MISSING", "SUSPENDED"]


# ---- scorer -----------------------------------------------------------------


def test_scorer_applies_config_weights():
    result = score_candidate(_candidate(), TEST_CONFIG)
    expected = 0.25 * 90 + 0.20 * 80 + 0.20 * 70 + 0.15 * 60 + 0.10 * 50 - 0.10 * 20
    assert result["opportunity_score"] == round(expected, 4)
    assert result["notes"] == []
    assert list(result["components"]) == ["theme", "technical", "alpha", "regime_fit", "knowledge", "risk"]


def test_scorer_missing_components_fall_back_to_neutral_with_note():
    candidate = _candidate(technical_score=None, knowledge_score=None)
    result = score_candidate(candidate, TEST_CONFIG)
    assert result["components"]["technical"] == 50.0
    assert result["components"]["knowledge"] == 50.0
    assert "MISSING_COMPONENT_TECHNICAL" in result["notes"]
    assert "MISSING_COMPONENT_KNOWLEDGE" in result["notes"]


def test_scorer_clamps_to_zero():
    candidate = _candidate(theme_score=0, technical_score=0, factor_score=0, regime_fit_score=0, knowledge_score=0, risk_score=100)
    assert score_candidate(candidate, TEST_CONFIG)["opportunity_score"] == 0.0


# ---- ranker -----------------------------------------------------------------


def test_ranker_sorts_desc_with_symbol_tiebreak_and_doc_shape():
    scored = [
        (_candidate(symbol="BBB", theme_score=80), score_candidate(_candidate(symbol="BBB", theme_score=80), TEST_CONFIG)),
        (_candidate(symbol="AAA", theme_score=80), score_candidate(_candidate(symbol="AAA", theme_score=80), TEST_CONFIG)),
        (_candidate(symbol="CCC", theme_score=95), score_candidate(_candidate(symbol="CCC", theme_score=95), TEST_CONFIG)),
    ]
    ranked = rank_opportunities(scored)
    assert [item["symbol"] for item in ranked] == ["CCC", "AAA", "BBB"]
    assert [item["rank"] for item in ranked] == [1, 2, 3]
    first = ranked[0]
    assert set(first) == {
        "rank",
        "symbol",
        "opportunity_score",
        "confidence",
        "components",
        "evidence_refs",
        "trigger_conditions",
        "invalidation_conditions",
    }
    assert set(first["components"]) == {"theme", "technical", "alpha", "regime_fit", "knowledge", "risk"}
    assert first["evidence_refs"][0].startswith("component:")
    assert "evidence:ev-1" in first["evidence_refs"]
    assert first["trigger_conditions"] == ["breakout"]
    assert first["invalidation_conditions"] == ["thesis_broken"]


def test_ranker_determinism():
    candidates = [_candidate(symbol=s) for s in ("A", "B", "C")]
    scored = [(c, score_candidate(c, TEST_CONFIG)) for c in candidates]
    assert rank_opportunities(scored) == rank_opportunities(list(reversed(scored)))


# ---- service ----------------------------------------------------------------


def test_service_rank_returns_ranked_rejected_and_meta():
    service = OpportunityRankingService(config=TEST_CONFIG)
    result = service.rank(
        candidates=[
            {"symbol": "GOOD", "theme_score": 90, "technical_score": 80, "liquidity_score": 90},
            {"symbol": "BAD", "theme_score": 95},
        ],
        context={"as_of": "2026-08-10", "symbols": {"BAD": {"suspended": True}}},
    )
    assert [item["symbol"] for item in result["ranked"]] == ["GOOD"]
    assert result["rejected"] == [{"symbol": "BAD", "eligible": False, "reject_reasons": ["SUSPENDED"]}]
    meta = result["meta"]
    assert meta["as_of"] == "2026-08-10"
    assert meta["calculation_version"] == "opportunity_ranking_v1"
    assert meta["candidate_count"] == 2
    assert meta["ranked_count"] == 1
    assert meta["rejected_count"] == 1
    assert meta["weights"]["theme"] == 0.25


def test_service_determinism_same_input_twice():
    service = OpportunityRankingService(config=TEST_CONFIG)
    payload = {
        "candidates": [
            {"symbol": "A", "theme_score": 90},
            {"symbol": "B", "theme_score": 90},
            {"symbol": "C", "theme_score": 70},
        ],
        "context": {"as_of": "2026-08-10"},
    }
    assert service.rank(**payload) == service.rank(**payload)
