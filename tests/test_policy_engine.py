"""Deterministic Policy Engine（详细修改方案 §7）。

LLM 只产生 InvestmentProposal；批准/降权/拒绝必须由确定性 Policy Engine 决定。
"""
from __future__ import annotations

import pytest

from engines.policy import (
    ApprovedDecision,
    InvestmentProposal,
    PolicyContext,
    PolicyEngine,
    PolicyLimits,
    explain_decision,
)


def _proposal(**overrides) -> InvestmentProposal:
    defaults = {
        "symbol": "002415.SZ",
        "action": "BUY",
        "proposed_weight": 0.08,
        "confidence": 0.7,
        "thesis_refs": ["thesis-1"],
        "sector": "电子",
        "theme": "AI算力",
        "evidence_count": 3,
        "factor_coverage": 0.9,
    }
    defaults.update(overrides)
    return InvestmentProposal(**defaults)


def test_clean_proposal_approved():
    decision = PolicyEngine().evaluate(_proposal())
    assert decision.approved is True
    assert decision.approved_weight == 0.08
    assert decision.rejections == []
    assert decision.policy_version == "policy.v1"


def test_single_position_limit_downsizes_weight():
    decision = PolicyEngine().evaluate(_proposal(proposed_weight=0.15))
    assert decision.approved is True
    assert decision.approved_weight == pytest.approx(0.10)
    assert "SINGLE_POSITION_LIMIT" in decision.adjustments


def test_existing_position_consumes_headroom():
    context = PolicyContext(existing_weights={"002415.SZ": 0.07})
    decision = PolicyEngine().evaluate(_proposal(proposed_weight=0.08), context)
    assert decision.approved is True
    assert decision.approved_weight == pytest.approx(0.03)
    assert "SINGLE_POSITION_LIMIT" in decision.adjustments


def test_hard_rejections_block_execution():
    engine = PolicyEngine()
    for overrides, expected_rule in (
        ({"is_st": True}, "ST_SUSPENSION"),
        ({"is_suspended": True}, "ST_SUSPENSION"),
        ({"confidence": 0.1}, "MINIMUM_CONFIDENCE"),
        ({"evidence_count": 0}, "MINIMUM_EVIDENCE"),
        ({"factor_coverage": 0.2}, "FACTOR_COVERAGE"),
        ({"liquidity_ok": False}, "LIQUIDITY"),
    ):
        decision = engine.evaluate(_proposal(**overrides))
        assert decision.approved is False, overrides
        assert expected_rule in decision.rejections
        assert decision.approved_weight == 0.0


def test_restricted_universe_rejected():
    context = PolicyContext(restricted_universe=["002415.SZ"])
    decision = PolicyEngine().evaluate(_proposal(), context)
    assert decision.approved is False and "RESTRICTED_UNIVERSE" in decision.rejections


def test_drawdown_mode_halves_position():
    context = PolicyContext(portfolio_drawdown_mode=True)
    decision = PolicyEngine().evaluate(_proposal(proposed_weight=0.08), context)
    assert decision.approved_weight == pytest.approx(0.05)
    assert "DRAWDOWN_MODE" in decision.adjustments


def test_industry_and_theme_exposure_limits():
    context = PolicyContext(industry_weights={"电子": 0.28}, theme_weights={"AI算力": 0.24})
    decision = PolicyEngine().evaluate(_proposal(proposed_weight=0.08), context)
    assert "INDUSTRY_EXPOSURE" in decision.adjustments
    assert "THEME_EXPOSURE" in decision.adjustments
    assert decision.approved_weight <= 0.02


def test_sell_not_blocked_by_st_rule():
    decision = PolicyEngine().evaluate(_proposal(action="SELL", is_st=True))
    assert "ST_SUSPENSION" not in decision.rejections


def test_custom_limits_respected():
    engine = PolicyEngine(limits=PolicyLimits(single_position_limit=0.04, min_confidence=0.8))
    decision = engine.evaluate(_proposal(proposed_weight=0.08, confidence=0.7))
    assert decision.approved is False and "MINIMUM_CONFIDENCE" in decision.rejections


def test_explanation_covers_why_and_unsuitability():
    engine = PolicyEngine()
    proposal = _proposal(proposed_weight=0.15)
    decision = engine.evaluate(proposal)
    explanation = explain_decision(proposal, decision)
    assert explanation["approved"] is True
    assert explanation["why"]
    assert explanation["when_no_longer_suitable"]
    rejected = explain_decision(_proposal(confidence=0.1), engine.evaluate(_proposal(confidence=0.1)))
    assert "MINIMUM_CONFIDENCE" in rejected["rejections"]


def test_checks_are_fully_recorded():
    decision = PolicyEngine().evaluate(_proposal())
    assert isinstance(decision, ApprovedDecision)
    assert len(decision.checks) == 10
    assert all(check.rule for check in decision.checks)
    payload = decision.to_dict()
    assert payload["checks"] and payload["policy_version"]
