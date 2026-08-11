"""engines.versioning 测试：读取 config/versions.yaml。"""
from __future__ import annotations

from engines.versioning import all_versions, get_version

EXPECTED_KEYS = {
    "market_feature_version",
    "sector_strength_version",
    "regime_model_version",
    "portfolio_rule_version",
    "factor_version",
    "retrieval_policy_version",
    "benchmark_router_version",
}


def test_get_version_reads_versions_yaml():
    assert get_version("market_feature_version") == "market_feature_v2"
    assert get_version("sector_strength_version") == "sector_strength_v2"
    assert get_version("regime_model_version") == "regime_preclassifier_v1"


def test_get_version_default_for_unknown_name():
    assert get_version("nonexistent_version") is None
    assert get_version("nonexistent_version", default="fallback_v0") == "fallback_v0"


def test_all_versions_contains_expected_keys():
    versions = all_versions()
    assert EXPECTED_KEYS <= set(versions)
    assert all(isinstance(value, str) and value for value in versions.values())
