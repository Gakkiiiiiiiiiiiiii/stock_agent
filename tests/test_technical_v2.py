import pandas as pd

from engines.technical.models import TriState
from engines.technical.profile_loader import load_technical_profile
from engines.technical.registry import default_indicator_registry
from engines.technical.rule_engine import RuleEngine


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
