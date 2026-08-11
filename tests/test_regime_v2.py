"""Regime v2：概率向量分类器与 market_regime_server v2 输出。"""
from __future__ import annotations

import pytest

from engines.regime.regime_preclassifier import (
    REGIMES,
    blend_probabilities,
    classify_regime_probabilities,
    preclassify_regime,
)
from mcp_servers.market_regime_server import get_market_regime

REPRESENTATIVE_FEATURES = [
    # high_position_retreat
    {"retreat_score": 0.85, "breadth": 0.55, "crowding_score": 0.5, "drawdown_risk": 0.3, "rotation_score": 0.6, "range_score": 0.4},
    # downtrend_market
    {"retreat_score": 0.2, "breadth": 0.2, "crowding_score": 0.3, "drawdown_risk": 0.85, "rotation_score": 0.5, "range_score": 0.3},
    # crowding_market
    {"retreat_score": 0.1, "breadth": 0.65, "crowding_score": 0.9, "drawdown_risk": 0.2, "rotation_score": 0.5, "range_score": 0.3},
    # range_market
    {"retreat_score": 0.2, "breadth": 0.48, "crowding_score": 0.4, "drawdown_risk": 0.3, "rotation_score": 0.6, "range_score": 0.85},
    # rotation_market
    {"retreat_score": 0.1, "breadth": 0.7, "crowding_score": 0.5, "drawdown_risk": 0.2, "rotation_score": 0.9, "range_score": 0.3},
]

FULL_SNAPSHOT = {
    "as_of": "2026-07-25T00:00:00+00:00",
    "up_count": 3000,
    "down_count": 1000,
    "limit_up_count": 60,
    "limit_down_count": 2,
    "index_return_5d": 0.02,
    "index_return_20d": 0.05,
    "index_volatility_20d": 0.012,
    "high_position_loss_ratio": 0.1,
    "high_position_limit_down_ratio": 0.02,
    "high_position_breakdown_ratio": 0.03,
    "high_position_big_negative_count": 1,
    "quality_score": 1.0,
}


def _service_payload(quality_status="OK", coverage=0.98):
    data = dict(FULL_SNAPSHOT)
    data["top_theme_strength"] = 85.0
    data["index_drawdown_20d"] = -0.01
    return {
        "data": data,
        "meta": {
            "calculation_version": "market_feature_v2_test",
            "coverage": coverage,
            "quality_status": quality_status,
            "quality_flags": [],
        },
    }


def test_probability_vector_sums_to_one():
    for features in REPRESENTATIVE_FEATURES:
        result = classify_regime_probabilities(features)
        total = sum(result["probabilities"].values()) + result["unknown_probability"]
        assert total == pytest.approx(1.0, abs=0.01)
        assert set(result["probabilities"]) == set(REGIMES)


def test_probability_argmax_consistent_with_legacy_preclassifier():
    for features in REPRESENTATIVE_FEATURES:
        legacy = preclassify_regime(features)
        result = classify_regime_probabilities(features)
        assert result["primary_regime"] == legacy["primary_regime"]
        top = max(result["probabilities"].values())
        assert result["probabilities"][result["primary_regime"]] == top
        assert result["confidence"] == top


def test_missing_features_produce_unknown_mass_and_low_confidence():
    result = classify_regime_probabilities({})
    assert result["unknown_probability"] > 0.8
    assert result["primary_regime"] == "UNKNOWN"
    assert result["confidence"] == 0.0

    partial = classify_regime_probabilities({"breadth": 0.5})
    assert 0 < partial["unknown_probability"] < 1
    assert partial["confidence"] <= max(partial["probabilities"].values())
    assert "crowding_score" in partial["missing_fields"]


def test_classifier_is_deterministic():
    features = REPRESENTATIVE_FEATURES[4]
    assert classify_regime_probabilities(features) == classify_regime_probabilities(dict(features))


def test_blend_probabilities_without_hint_keeps_deterministic():
    det = {"rotation_market": 0.6, "range_market": 0.4}
    assert blend_probabilities(det, None) == {"high_position_retreat": 0.0, "downtrend_market": 0.0, "crowding_market": 0.0, "range_market": 0.4, "rotation_market": 0.6}
    assert blend_probabilities(det, {}) == blend_probabilities(det, None)


def test_blend_probabilities_with_hint_renormalizes():
    det = {regime: 0.0 for regime in REGIMES}
    det["rotation_market"] = 0.8
    det["range_market"] = 0.2
    hint = {regime: 0.0 for regime in REGIMES}
    hint["crowding_market"] = 1.0
    blended = blend_probabilities(det, hint, weight=0.25)
    assert sum(blended.values()) == pytest.approx(1.0, abs=1e-9)
    assert blended["crowding_market"] == pytest.approx(0.25, abs=1e-4)
    assert blended["rotation_market"] == pytest.approx(0.6, abs=1e-4)


def test_server_output_contains_v2_keys():
    result = get_market_regime(
        snapshot=FULL_SNAPSHOT,
        top_theme_strength=85.0,
        index_drawdown_20d=-0.01,
        state_mode="stateless",
    )
    for key in ("primary_regime", "probabilities", "confidence", "transition_status", "feature_version", "data_quality", "regime_model_version"):
        assert key in result
    assert result["primary_regime"] in set(REGIMES) | {"UNKNOWN"}
    assert sum(result["probabilities"].values()) == pytest.approx(1.0, abs=0.01)
    assert result["confidence"] == result["probabilities"][result["primary_regime"]]
    assert result["transition_status"] == "stable"
    assert result["regime_model_version"]
    # 旧版键保持向后兼容
    assert result["regime"]["primary_regime"] in set(REGIMES)
    assert "state_machine" in result and "features" in result


def test_server_transition_status_confirming_on_candidate_switch():
    result = get_market_regime(
        snapshot=FULL_SNAPSHOT,
        top_theme_strength=85.0,
        index_drawdown_20d=-0.01,
        previous_regime="range_market",
        state_mode="stateless",
    )
    assert result["state_machine"]["switch_status"] == "watch_switch"
    assert result["transition_status"] == "confirming"


def test_server_downgrades_confidence_on_insufficient_data_quality(monkeypatch):
    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def get_market_features(self, as_of=None):
            return _service_payload(quality_status="INSUFFICIENT", coverage=0.5)

    monkeypatch.setattr("mcp_servers.market_regime_server.MarketFeatureService", FakeService)
    degraded = get_market_regime(state_mode="stateless")
    assert degraded["data_quality"] == "INSUFFICIENT"
    assert "REGIME_LOW_DATA_CONFIDENCE" in degraded["quality_flags"]
    assert degraded["feature_version"] == "market_feature_v2_test"

    class HealthyService(FakeService):
        def get_market_features(self, as_of=None):
            return _service_payload(quality_status="OK", coverage=0.99)

    monkeypatch.setattr("mcp_servers.market_regime_server.MarketFeatureService", HealthyService)
    healthy = get_market_regime(state_mode="stateless")
    assert "REGIME_LOW_DATA_CONFIDENCE" not in healthy["quality_flags"]
    # 降级路径不拒绝回答：主 regime 一致，仅置信度按比例下调
    assert degraded["primary_regime"] == healthy["primary_regime"]
    assert degraded["confidence"] == round(healthy["confidence"] * 0.5, 4)


def test_server_falls_back_to_builder_when_service_fails(monkeypatch):
    class BrokenService:
        def __init__(self, *args, **kwargs):
            pass

        def get_market_features(self, as_of=None):
            raise RuntimeError("provider down")

    class Provider:
        def get_market_snapshot(self, as_of=None, force_refresh=False):
            return {
                "source": "fake",
                "up_count": 3000,
                "down_count": 1000,
                "limit_up_count": 60,
                "limit_down_count": 2,
                "indices": {"return_5d_pct": 2.0, "return_20d_pct": 5.0, "volatility_20d_pct": 1.2, "drawdown_20d_pct": -1.0},
                "high_position_loss_ratio": 0.1,
                "high_position_limit_down_ratio": 0.02,
                "high_position_breakdown_ratio": 0.03,
                "high_position_big_negative_count": 1,
            }

        def get_sector_strength(self, as_of=None):
            return [{"sector": "TMT", "strength_score": 85, "change_pct": 3.0}]

    monkeypatch.setattr("mcp_servers.market_regime_server.MarketFeatureService", BrokenService)
    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    result = get_market_regime(state_mode="stateless")
    assert "REGIME_FEATURE_SERVICE_FALLBACK" in result["quality_flags"]
    assert result["regime"]["primary_regime"] != "UNKNOWN"
    assert result["primary_regime"] in set(REGIMES)
    assert result["data_quality"] == "UNKNOWN"


def test_server_blocked_path_still_answers_with_downgrade():
    snapshot = dict(FULL_SNAPSHOT, quality_score=0.0, quote_coverage=0.5, quality_flags=["MARKET_QUOTE_COVERAGE_LOW"])
    result = get_market_regime(
        snapshot=snapshot,
        top_theme_strength=85.0,
        index_drawdown_20d=-0.01,
        state_mode="stateless",
    )
    # 旧版拒绝语义保留
    assert result["regime"]["primary_regime"] == "UNKNOWN"
    # v2 输出层不拒绝：给出降级后的概率答案
    assert result["primary_regime"] in set(REGIMES) | {"UNKNOWN"}
    assert "REGIME_LOW_DATA_CONFIDENCE" in result["quality_flags"]
    assert result["data_quality"] == "INSUFFICIENT"
    assert result["confidence"] <= 0.5
