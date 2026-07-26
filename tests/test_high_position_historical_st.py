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


# ---------- 不可靠近期涨停证据降权（v2.2.3 第八轮 P2） ----------

from datetime import timedelta

from engines.market.high_position_feature_builder import HighPositionFeatureBuilder


def _make_bridge(special_idx=0, special_kind="uncertain"):
    """12 只主板股票 70 天历史（2026-04-24 ~ 2026-07-02，全部处于旧制度期）。

    idx 收益随编号微增 → ret 分位产生 2 个 return_leader（idx 10/11）作为基准池。
    special_idx 的最后一个交易日 +5%：
    - uncertain：无风险状态、无显式涨幅 → 不可靠涨停；
    - confirmed：显式 limit_up_rate=5 → 可靠涨停；
    - near_high_crowded：同时靠近 60 日高点且放量。
    """

    class Bridge:
        def get_history(self, symbols, period, start_time, end_time, dividend_type, fill_data=True, prefer_cache_first=True):
            rows = []
            for idx, symbol in enumerate(symbols):
                plateau = 10.0 + 40 * 0.01  # near_high_crowded 的高点平台
                for day in range(70):
                    trading_day = date(2026, 4, 24) + timedelta(days=day)
                    close = 10.0 + day * 0.0005 * idx
                    amount = 1_000_000.0
                    if day == 68:
                        # pool 末日（倒数第二天）放量倍数随 idx 递增 → 只有 idx 9/10/11 越过拥挤分位
                        amount = 1_000_000.0 * (1 + idx * 0.2)
                    extra = {}
                    if idx == special_idx:
                        if special_kind == "confirmed":
                            extra["is_risk_warning"] = True  # 每日状态明确 → 证据可靠
                        if special_kind == "near_high_crowded":
                            # 前 40 日走高后平台整理 → 收盘贴近 60 日高点；ret20≈0 不是 leader
                            close = min(10.0 + day * 0.01, plateau)
                        if day == 65:
                            # 窗口中段 +5% 后回落：不抬高 ret20
                            base = plateau if special_kind == "near_high_crowded" else 10.0 + 64 * 0.0005 * idx
                            close = base * 1.05
                            if special_kind == "confirmed":
                                extra["limit_up_rate"] = 5
                            else:
                                extra["name"] = "ST测试"  # 名称识别 ST → 5% 涨停但状态字段缺失 → 不可靠
                        elif special_kind == "near_high_crowded" and day > 65:
                            close = plateau * 1.002
                        elif special_kind != "near_high_crowded" and day > 65:
                            close = 9.9  # 回落 → 不接近 60 日高点
                        if special_kind == "near_high_crowded" and day == 68:
                            amount = 5_000_000.0  # 放量 → 拥挤
                    rows.append(
                        {
                            "symbol": symbol,
                            "trading_date": trading_day.isoformat(),
                            "open": close * 0.999,
                            "high": close * 1.001,
                            "close": close,
                            "amount": amount,
                            **extra,
                        }
                    )
            return rows

    return Bridge()


def _symbols():
    return [f"6000{i:02d}.SH" for i in range(12)]


def _quotes(symbols):
    return {symbol: {"last_price": 10.0, "last_close": 10.0, "open": 10.0} for symbol in symbols}


def test_unreliable_limit_hit_cannot_enter_pool_alone():
    symbols = _symbols()
    features = HighPositionFeatureBuilder(_make_bridge(special_kind="uncertain")).build(
        symbols, _quotes(symbols)
    ).as_dict()
    # 基准池只有 3 个拥挤/领涨股；不可靠涨停不能单独入池
    assert features["high_position_pool_size"] == 3
    assert features["high_position_uncertain_limit_count"] == 1
    assert "HIGH_POSITION_UNCERTAIN_LIMIT_EVIDENCE" in features["high_position_quality_flags"]
    assert "HISTORICAL_RISK_WARNING_STATUS_UNAVAILABLE" in features["high_position_quality_flags"]


def test_confirmed_limit_hit_can_enter_pool():
    symbols = _symbols()
    features = HighPositionFeatureBuilder(_make_bridge(special_kind="confirmed")).build(
        symbols, _quotes(symbols)
    ).as_dict()
    # 可靠涨停可独立入池：3 个基准 + 1 个可靠涨停
    assert features["high_position_pool_size"] == 4
    assert features["high_position_uncertain_limit_count"] == 0


def test_unreliable_limit_hit_can_strengthen_near_high_candidate():
    symbols = _symbols()
    features = HighPositionFeatureBuilder(_make_bridge(special_kind="near_high_crowded")).build(
        symbols, _quotes(symbols)
    ).as_dict()
    # 不可靠证据可增强已有高位（near_high）且拥挤的候选入池；
    # 基准池为 2 个领涨股（special 的放量抬高了拥挤分位）+ special 自身
    assert features["high_position_pool_size"] == 3
    assert features["high_position_uncertain_limit_count"] == 1
