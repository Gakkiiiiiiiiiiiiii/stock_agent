from __future__ import annotations

from datetime import date, datetime

from scripts.validate_data_quality import validate as validate_quality
from scripts.validate_historical_data import validate as validate_history
from storage.repositories.market_feature_repository import MarketFeatureRepository


def test_persisted_historical_validator_checks_coverage_and_future_data(isolated_database):
    repo = MarketFeatureRepository()
    day = date(2026, 8, 10)
    repo.save_market_snapshot("CN_A", datetime(2026, 8, 10, 15), day, "v1", {}, quality_flags=[])
    repo.save_sector_snapshot("银行", day, datetime(2026, 8, 10, 15), {}, .5, "v1", coverage=1.0)
    repo.upsert_membership("600000.SH", "BK", "银行", day, "HISTORICAL")
    report = validate_history(day, day, {day})
    assert report["passed"] is True
    assert report["future_data_violation_count"] == 0
    assert validate_quality(day, day)["passed"] is True

    repo.save_market_snapshot("CN_A", datetime(2026, 8, 11, 9), day, "v1", {}, quality_flags=["DATA_UNAVAILABLE"])
    report = validate_history(day, day, {day})
    assert report["passed"] is False
    assert report["future_data_violation_count"] == 1
    assert validate_quality(day, day)["passed"] is False
