"""feature_quality 数据质量门控测试：OK / DEGRADED / INSUFFICIENT 状态流转。"""
from __future__ import annotations

from engines.market.feature_quality import (
    DATA_QUALITY_INSUFFICIENT,
    evaluate_market_data_quality,
)

THRESHOLDS = {
    "min_quote_coverage": 0.9,
    "degraded_quote_coverage": 0.7,
    "min_sector_membership_coverage": 0.8,
    "degraded_sector_membership_coverage": 0.6,
    "require_index_data": True,
    "require_limit_metadata": True,
}


def _snapshot(**overrides):
    base = {
        "quote_coverage": 0.95,
        "indices": {"return_5d_pct": 1.2},
        "sector_membership_coverage": 0.9,
        "limit_up_count": 30,
        "limit_down_count": 5,
    }
    base.update(overrides)
    return base


def test_status_ok_when_all_checks_pass():
    result = evaluate_market_data_quality(_snapshot(), THRESHOLDS)
    assert result["status"] == "OK"
    assert result["quality_score"] == 1.0
    assert result["quality_flags"] == []


def test_status_degraded_on_medium_quote_coverage():
    result = evaluate_market_data_quality(_snapshot(quote_coverage=0.8), THRESHOLDS)
    assert result["status"] == "DEGRADED"
    assert "QUOTE_COVERAGE_DEGRADED" in result["quality_flags"]
    assert DATA_QUALITY_INSUFFICIENT not in result["quality_flags"]
    assert 0.0 < result["quality_score"] < 1.0


def test_status_degraded_on_missing_limit_metadata():
    result = evaluate_market_data_quality(_snapshot(limit_up_count=None, limit_down_count=None), THRESHOLDS)
    assert result["status"] == "DEGRADED"
    assert "LIMIT_METADATA_MISSING" in result["quality_flags"]


def test_status_degraded_on_medium_membership_coverage():
    result = evaluate_market_data_quality(_snapshot(sector_membership_coverage=0.7), THRESHOLDS)
    assert result["status"] == "DEGRADED"
    assert "SECTOR_MEMBERSHIP_COVERAGE_DEGRADED" in result["quality_flags"]


def test_status_insufficient_on_low_quote_coverage():
    result = evaluate_market_data_quality(_snapshot(quote_coverage=0.5), THRESHOLDS)
    assert result["status"] == "INSUFFICIENT"
    assert "QUOTE_COVERAGE_INSUFFICIENT" in result["quality_flags"]
    assert DATA_QUALITY_INSUFFICIENT in result["quality_flags"]


def test_status_insufficient_when_index_missing_and_required():
    result = evaluate_market_data_quality(_snapshot(indices={}), THRESHOLDS)
    assert result["status"] == "INSUFFICIENT"
    assert "INDEX_DATA_UNAVAILABLE" in result["quality_flags"]
    assert DATA_QUALITY_INSUFFICIENT in result["quality_flags"]


def test_index_check_skipped_when_not_required():
    thresholds = {**THRESHOLDS, "require_index_data": False}
    result = evaluate_market_data_quality(_snapshot(indices={}), thresholds)
    assert result["status"] == "OK"


def test_missing_optional_fields_skip_checks():
    result = evaluate_market_data_quality({"quote_coverage": 0.95, "indices": {"a": 1}, "limit_up_count": 1, "limit_down_count": 1}, THRESHOLDS)
    assert result["status"] == "OK"
    assert result["quality_score"] == 1.0
