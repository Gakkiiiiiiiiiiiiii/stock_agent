"""历史 ST 状态三态化与质量标记测试（v2.2.2 第七轮 P1）。"""
from datetime import date

import pytest

from engines.market.high_position_feature_builder import (
    _group_rows,
    _optional_bool,
    _recent_limit_up,
)

MAINBOARD = "600000.SH"


def _record(day: date, close: float, **extra):
    return {"date": day, "open": close, "high": close, "close": close, "amount": 1e6, **extra}


def test_pre_20260706_mainboard_st_uses_five_percent():
    records = [
        _record(date(2026, 7, 1), 10.0),
        _record(date(2026, 7, 2), 10.5, is_risk_warning=True),  # +5%，ST 即涨停
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is True
    assert result.reliable is True
    assert result.quality_flags == []


def test_pre_20260706_mainboard_non_st_uses_ten_percent():
    records = [
        _record(date(2026, 7, 1), 10.0),
        _record(date(2026, 7, 2), 10.5, is_risk_warning=False),  # +5%，非 ST 未涨停
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is False
    assert result.reliable is True

    records[1]["close"] = 11.0  # +10% 涨停
    assert _recent_limit_up(MAINBOARD, records).hit is True


def test_pre_20260706_missing_risk_status_sets_quality_flag():
    records = [
        _record(date(2026, 7, 1), 10.0),
        _record(date(2026, 7, 2), 10.5),  # 数据源未提供 ST 状态
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is False  # 按 10% 判断未涨停，但结论不可信
    assert result.reliable is False
    assert "HISTORICAL_RISK_WARNING_STATUS_UNAVAILABLE" in result.quality_flags


def test_explicit_historical_limit_rate_avoids_missing_status_flag():
    records = [
        _record(date(2026, 7, 1), 10.0),
        _record(date(2026, 7, 2), 10.5, limit_up_rate=5),  # 实际涨停幅度优先
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is True  # 5% 涨停
    assert result.reliable is True
    assert result.quality_flags == []


def test_post_20260706_missing_st_status_does_not_change_mainboard_limit():
    records = [
        _record(date(2026, 7, 7), 10.0),
        _record(date(2026, 7, 8), 10.5),  # 新制度后主板统一 10%，状态缺失不影响比例
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is False
    assert result.reliable is True
    assert result.quality_flags == []

    records[1]["close"] = 11.0
    assert _recent_limit_up(MAINBOARD, records).hit is True


def test_group_rows_preserves_risk_status_fields():
    rows = [
        {
            "symbol": MAINBOARD,
            "trading_date": "20260702",
            "open": 10, "high": 10.5, "close": 10.5, "amount": 1e6,
            "name": "ST示例",
            "is_risk_warning": "1",
            "limit_up_rate": 5,
            "limit_down_rate": 5,
            "upper_limit_price": 10.5,
            "lower_limit_price": 9.5,
        }
    ]
    grouped = _group_rows(rows)
    record = grouped[MAINBOARD][0]
    assert record["name"] == "ST示例"
    assert record["is_risk_warning"] is True
    assert record["limit_up_rate"] == 5.0
    assert record["limit_down_rate"] == 5.0
    assert record["upper_limit_price"] == 10.5
    assert record["lower_limit_price"] == 9.5


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, True),
        (False, False),
        ("1", True),
        ("true", True),
        ("0", False),
        ("false", False),
        ("", None),
        (None, None),
        ("unknown", None),
    ],
)
def test_optional_bool_three_states(raw, expected):
    assert _optional_bool(raw) is expected


def test_st_name_detected_when_status_missing():
    # 状态字段缺失但名称含 ST：规则层仍可识别 5%，但质量标记必须保留
    records = [
        _record(date(2026, 7, 1), 10.0),
        _record(date(2026, 7, 2), 10.5, name="ST测试"),
    ]
    result = _recent_limit_up(MAINBOARD, records)
    assert result.hit is True
    assert result.reliable is False
    assert "HISTORICAL_RISK_WARNING_STATUS_UNAVAILABLE" in result.quality_flags
