"""Advisory Suitability 测试（详细修改方案 §8）。"""
from __future__ import annotations

from engines.advisory.models import InvestorProfile, Recommendation
from engines.advisory.suitability import evaluate_suitability


def test_suitable_when_within_limits():
    profile = InvestorProfile(risk_level="AGGRESSIVE")
    rec = Recommendation(symbol="600519.SH", action="BUY", weight=0.10)
    result = evaluate_suitability(profile, rec)
    assert result["suitable"] is True
    assert result["status"] == "SUITABLE"
    assert result["approved_weight"] == 0.10
    assert result["rejections"] == []


def test_conditional_when_weight_capped_by_risk_level():
    profile = InvestorProfile(risk_level="CONSERVATIVE")
    rec = Recommendation(symbol="600519.SH", action="BUY", weight=0.20, risk_rating="CONSERVATIVE")
    result = evaluate_suitability(profile, rec)
    assert result["suitable"] is True
    assert result["status"] == "CONDITIONAL"
    assert result["approved_weight"] == 0.05
    assert "WEIGHT_CAPPED_BY_RISK_LEVEL" in result["conditions"]
    assert result["explanation"]


def test_rejected_on_risk_mismatch():
    profile = InvestorProfile(risk_level="CONSERVATIVE")
    rec = Recommendation(symbol="600519.SH", action="BUY", weight=0.10, risk_rating="AGGRESSIVE")
    result = evaluate_suitability(profile, rec)
    assert result["suitable"] is False
    assert result["status"] == "REJECTED"
    assert result["approved_weight"] == 0.0
    assert any(reason.startswith("RISK_MISMATCH") for reason in result["rejections"])


def test_rejected_on_market_and_drawdown():
    profile = InvestorProfile(allowed_markets=("CN_A",), max_drawdown_tolerance=0.10)
    rec = Recommendation(
        symbol="AAPL", action="BUY", weight=0.10, market="US", expected_max_drawdown=0.30
    )
    result = evaluate_suitability(profile, rec)
    assert result["status"] == "REJECTED"
    assert "MARKET_NOT_ALLOWED:US" in result["rejections"]
    assert any(reason.startswith("DRAWDOWN_EXCEEDS_TOLERANCE") for reason in result["rejections"])


def test_horizon_mismatch_caps_weight_and_explains_exit_conditions():
    profile = InvestorProfile(risk_level="AGGRESSIVE", investment_horizon_years=0.5)
    rec = Recommendation(symbol="600519.SH", action="BUY", weight=0.20, holding_horizon_years=3.0)
    result = evaluate_suitability(profile, rec)
    assert result["status"] == "CONDITIONAL"
    assert "HORIZON_MISMATCH" in result["conditions"]
    assert result["approved_weight"] == 0.05
    assert result["when_no_longer_suitable"]
