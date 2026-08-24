"""Conflict Resolver v2 测试（详细修改方案 §11）：领域权威 + Risk VETO。"""
from __future__ import annotations

from engines.decision.conflict_resolver import (
    ConflictOpinion,
    CONFLICT_TYPES,
    DOMAIN_AUTHORITY,
    resolve_conflicts_v2,
)
import pytest


def test_domain_authority_resolves_conflict():
    conflicts = [
        {
            "type": "SIGNAL_CONFLICT",
            "dimension": "factor_direction",
            "options": [
                {"agent": "MARKET_SPECIALIST", "value": "bullish"},
                {"agent": "FACTOR_SPECIALIST", "value": "bearish"},
            ],
        }
    ]
    result = resolve_conflicts_v2(conflicts)
    assert result["final_action"] == "proceed"
    assert result["vetoed"] is False
    resolution = result["resolutions"][0]
    assert resolution["resolved_by"] == "FACTOR_SPECIALIST"
    assert resolution["resolved_value"] == "bearish"


def test_risk_veto_flag_blocks_decision():
    conflicts = [
        {
            "type": "RISK_CONFLICT",
            "dimension": "drawdown",
            "risk_veto": True,
            "options": [
                {"agent": "RISK_SPECIALIST", "value": "reduce"},
                {"agent": "FACTOR_SPECIALIST", "value": "hold"},
            ],
        }
    ]
    result = resolve_conflicts_v2(conflicts)
    assert result["final_action"] == "veto"
    assert result["vetoed"] is True
    assert result["veto_reasons"] == ["drawdown"]


def test_risk_specialist_owner_veto_blocks_decision():
    conflicts = [
        {
            "type": "RISK_CONFLICT",
            "dimension": "liquidity",
            "options": [{"agent": "RISK_SPECIALIST", "value": "block", "veto": True}],
        }
    ]
    result = resolve_conflicts_v2(conflicts)
    assert result["final_action"] == "veto"
    assert result["vetoed"] is True


def test_non_risk_conflict_without_veto_proceeds():
    conflicts = [
        {
            "type": "REGIME_CONFLICT",
            "dimension": "regime",
            "options": [
                {"agent": "MARKET_SPECIALIST", "value": "risk_off"},
                {"agent": "PORTFOLIO_SPECIALIST", "value": "risk_on"},
            ],
        }
    ]
    result = resolve_conflicts_v2(conflicts)
    assert result["final_action"] == "proceed"
    assert result["resolutions"][0]["resolved_value"] == "risk_off"


def test_conflict_types_and_authority_cover_all_domains():
    assert set(CONFLICT_TYPES) == set(DOMAIN_AUTHORITY.keys())
    assert DOMAIN_AUTHORITY["RISK_CONFLICT"] == "RISK_SPECIALIST"
    assert DOMAIN_AUTHORITY["FACT_CONFLICT"] == "EVIDENCE_AUTHORITY"


def test_empty_conflicts_proceed():
    result = resolve_conflicts_v2([])
    assert result["final_action"] == "proceed"
    assert result["resolutions"] == []


def test_real_agent_alias_domain_owner_beats_higher_confidence_fallback():
    result = resolve_conflicts_v2([{
        "type": "SIGNAL_CONFLICT",
        "dimension": "factor_direction",
        "options": [
            {"agent": "FactorAgent", "value": "bearish", "confidence": 0.2},
            {"agent": "MarketAgent", "value": "bullish", "confidence": 0.99},
        ],
    }])
    assert result["resolutions"][0]["resolved_by"] == "FactorAgent"
    assert result["resolutions"][0]["resolved_value"] == "bearish"


def test_risk_agent_veto_beats_higher_confidence_buy():
    result = resolve_conflicts_v2([{
        "type": "SIGNAL_CONFLICT",
        "dimension": "risk_overlay",
        "options": [
            {"agent": "FactorAgent", "value": "BUY", "confidence": 0.99},
            {"agent": "RiskAgent", "value": "VETO", "confidence": 0.2, "veto": True},
        ],
    }])
    assert result["vetoed"] is True
    assert result["final_action"] == "veto"
    assert result["resolutions"][0]["resolved_by"] == "RiskAgent"


def test_conflict_values_are_deep_immutable():
    opinion = ConflictOpinion(agent="FactorAgent", value={"nested": [1]})
    with pytest.raises(TypeError):
        opinion.value["nested"].append(2)
