import pandas as pd
import pytest
from pathlib import Path

from app.tool_registry import ClaudeToolRegistry
from engines.market.data_provider import sample_kline
from engines.technical.models import TriState
from engines.technical.profile_loader import load_technical_profile
from engines.technical.registry import default_indicator_registry
from engines.technical.rule_engine import RuleEngine
from engines.technical.rule_validator import RulePackValidationError, RulePackValidator, stable_rule_pack_hash
from financial_agent.models import KlineResponse
from mcp_servers import technical_factor_server


def test_technical_profile_registry_fingerprint():
    profile = load_technical_profile("core_daily_v1")
    registry = default_indicator_registry()
    assert registry.validate_profile(profile)["valid"] is True
    assert len(registry.fingerprint(profile)) == 64


def test_rule_engine_three_valued_logic():
    frame = pd.DataFrame({"ma5": [3], "ma10": [2], "ma20": [1]})
    rule = {"id": "x", "score": 10, "condition": {"all": [{"gt": ["ma5", "ma10"]}, {"gt": ["missing", "ma20"]}]}}
    evaluation = RuleEngine().evaluate_rule(rule, frame)
    assert evaluation.status == TriState.INDETERMINATE


def test_profile_minimum_bars_enforced(monkeypatch):
    class _Provider:
        def get_kline(self, symbol, **kwargs):
            return KlineResponse(symbol=symbol, records=sample_kline(symbol, days=259), source="sample")

    monkeypatch.setattr(technical_factor_server, "get_market_data_provider", lambda: _Provider())
    result = technical_factor_server.calc_profile_indicators("SAMPLE", end_date="2026-07-25")
    assert result["error"]["code"] == "INSUFFICIENT_BARS"
    assert result["error"]["required"] == 260
    assert result["error"]["actual"] == 259


def test_invalid_indicator_params_rejected():
    profile = load_technical_profile("core_daily_v1")
    broken = profile.__class__(
        **{**profile.__dict__, "indicators": [profile.indicators[0].__class__("sma", "bad_ma", {"window": -20, "field": "close"})]}
    )
    validation = default_indicator_registry().validate_profile(broken)
    assert validation["valid"] is False
    assert "bad_ma" in ";".join(validation["errors"])


def test_invalid_indicator_field_rejected():
    profile = load_technical_profile("core_daily_v1")
    broken = profile.__class__(
        **{**profile.__dict__, "indicators": [profile.indicators[0].__class__("sma", "bad_ma", {"window": 20, "field": "unknown_field"})]}
    )
    validation = default_indicator_registry().validate_profile(broken)
    assert validation["valid"] is False
    assert "unknown_field" in ";".join(validation["errors"])


def test_invalid_rule_reference_rejected():
    profile = load_technical_profile("core_daily_v1")
    pack = {"version": "1.0.0", "profile": profile.name, "rules": [{"id": "bad", "score": 5, "condition": {"gt": ["missing_alias", "ma20"]}}]}
    with pytest.raises(RulePackValidationError) as exc:
        RulePackValidator(default_indicator_registry()).validate("bad_pack", pack, profile)
    assert exc.value.errors[0].rule_id == "bad"


def test_rule_pack_hash_stable():
    pack = {"version": "1.0.0", "rules": [{"id": "x", "score": 1, "condition": {"gt": ["a", 1]}}]}
    assert stable_rule_pack_hash("p", pack) == stable_rule_pack_hash("p", dict(pack))


def test_legacy_patterns_not_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_TECHNICAL_PATTERNS", raising=False)
    tools = {item["name"] for item in ClaudeToolRegistry().anthropic_tools()}
    assert "detect_pattern_signal" not in tools
    assert "scan_stock_signals" not in tools
    assert "calc_profile_indicators" in tools
    assert "evaluate_technical_rules" in tools
    assert "scan_technical_rules" in tools


def test_skill_uses_profile_tools():
    content = (Path("skills/a-share-technical-analysis/SKILL.md")).read_text(encoding="utf-8")
    assert "calc_profile_indicators" in content
    assert "evaluate_technical_rules" in content
    assert "detect_pattern_signal" not in content
