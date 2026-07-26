"""回测/模拟盘每日涨跌停元数据测试（v2.2.3 第八轮 P0）。"""
from datetime import date

import numpy as np
import pytest

from engines.backtest.portfolio_backtest import run_topk_backtest


def _panels(n_symbols=4, n_days=11, price=10.0, start="2026-07-01"):
    from datetime import timedelta

    symbols = [f"60000{i}.SH" for i in range(n_symbols)]
    base = date.fromisoformat(start)
    dates = [(base + timedelta(days=d)).isoformat() for d in range(n_days)]
    base_arr = np.full((n_symbols, n_days), price)
    volume = np.full((n_symbols, n_days), 1000.0)
    return symbols, dates, base_arr.copy(), base_arr.copy(), base_arr.copy(), base_arr.copy(), volume


def _meta_grid(n_symbols, n_days, fill=None):
    grid = [[None for _ in range(n_days)] for _ in range(n_symbols)]
    if fill:
        for (i, t), meta in fill.items():
            grid[i][t] = meta
    return grid


def test_backtest_pre_20260706_st_limit_up_uses_daily_meta():
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-06-29")
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    scores[2, 5:] = 80.0
    scores[3, 5:] = 70.0
    opens[0, 5] = 10.5  # 主板前收 10：普通 10% 可买（涨停 11.0），ST 5% 涨停 10.5 不可买
    kwargs = dict(rebalance_interval=5, top_k=2, initial_cash=100_000.0, allow_unsafe_without_metadata=True)

    control = run_topk_backtest(scores, opens, highs, lows, closes, volume, symbols, dates, **kwargs)
    assert symbols[0] in control["holdings_log"][5]  # 无元数据：按 10% 买入

    meta = _meta_grid(4, 11, {(0, 5): {"is_risk_warning": True}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        security_meta=meta, **kwargs,
    )
    assert symbols[0] not in result["holdings_log"][5]  # 每日 ST 状态生效：5% 涨停买不进
    assert symbols[1] in result["holdings_log"][5]


def test_backtest_uses_actual_upper_limit_price_before_rule_rate():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    opens[0, 5] = 10.4  # 规则 10% 允许（涨停 11.0），但实际涨停价 10.3 不允许
    upper = np.full((4, 11), np.nan)
    upper[0, 5] = 10.3
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
        upper_limit_prices=upper,
    )
    assert symbols[0] not in result["holdings_log"][5]
    assert symbols[1] in result["holdings_log"][5]


def test_backtest_uses_actual_lower_limit_price_before_rule_rate():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    scores[0, :] = 100.0
    scores[1, :] = 90.0
    # t=5 调出 0、1 号，换入 2、3 号
    scores[0, 5:] = 1.0
    scores[1, 5:] = 1.0
    scores[2, 5:] = 100.0
    scores[3, 5:] = 90.0
    opens[0, 5] = 9.4  # 规则 10% 可卖（跌停 9.0），但实际跌停价 9.5 不可卖
    lower = np.full((4, 11), np.nan)
    lower[0, 5] = 9.5
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
        lower_limit_prices=lower,
    )
    assert symbols[0] in result["holdings_log"][5]  # 跌停价 9.5：开盘 9.4 卖不出，保留
    assert symbols[2] in result["holdings_log"][5]


def test_backtest_no_limit_listing_stage_can_trade():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    opens[0, 5] = 20.0  # 较前收翻倍：IPO 首日无涨跌幅，可买
    meta = _meta_grid(4, 11, {(0, 5): {"listing_stage": "IPO_FIRST_DAY"}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
        security_meta=meta,
    )
    assert symbols[0] in result["holdings_log"][5]


def test_backtest_asymmetric_daily_limit_rates():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    scores[0, :] = 100.0
    scores[1, :] = 90.0
    scores[0, 5:] = 1.0
    scores[1, 5:] = 1.0
    scores[2, 5:] = 100.0
    scores[3, 5:] = 90.0
    # 非对称 15%/12%：开盘 8.7 低于跌停 8.80 → 不可卖（对称 10% 跌停 9.0 也不可卖，
    # 但 15% 对称错误实现跌停 8.5 → 会错误卖出）
    opens[0, 5] = 8.7
    meta = _meta_grid(4, 11, {(0, 5): {"limit_up_rate": 15, "limit_down_rate": 12}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
        security_meta=meta,
    )
    assert symbols[0] in result["holdings_log"][5]  # 8.7 <= 8.80 跌停，保留


def test_backtest_missing_historical_st_status_sets_quality_flag():
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-06-29")
    scores = np.full((4, 11), 1.0)
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
    )
    assert "BACKTEST_HISTORICAL_RISK_STATUS_UNAVAILABLE" in result["price_limit_quality_flags"]
    assert result["price_limit_fallback_count"] > 0
    assert result["price_limit_meta_coverage"] == 0.0


def test_backtest_post_effective_date_no_quality_flag():
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-07-20")
    scores = np.full((4, 11), 1.0)
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
    )
    assert result["price_limit_quality_flags"] == []


def test_backtest_fail_on_ambiguous_price_limit_strict_mode(monkeypatch):
    from financial_agent.research_config import BacktestConfig, ResearchConfig

    monkeypatch.setattr(
        "engines.backtest.portfolio_backtest.get_research_config",
        lambda: ResearchConfig(backtest=BacktestConfig(fail_on_ambiguous_price_limit=True)),
    )
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-06-29")
    scores = np.full((4, 11), 1.0)
    with pytest.raises(ValueError, match="BACKTEST_HISTORICAL_RISK_STATUS_UNAVAILABLE"):
        run_topk_backtest(
            scores, opens, highs, lows, closes, volume, symbols, dates,
            rebalance_interval=5, top_k=2, initial_cash=100_000.0,
            allow_unsafe_without_metadata=True,
        )


def test_backtest_security_meta_shape_validation():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    with pytest.raises(ValueError, match="security_meta shape mismatch"):
        run_topk_backtest(
            scores, opens, highs, lows, closes, volume, symbols, dates,
            rebalance_interval=5, top_k=2, initial_cash=100_000.0,
            allow_unsafe_without_metadata=True,
            security_meta=_meta_grid(3, 11),
        )


def test_paper_worker_passes_quote_meta_to_can_buy_sell(tmp_path):
    """模拟盘执行：T 日 Quote 中的实际涨停价优先于规则比例。"""
    from workers import factor_paper_worker as fpw

    symbols = ["600000.SH", "600001.SH"]
    dates = ["2026-07-27", "2026-07-28"]
    n = (2, 2)
    panel = {
        "open": np.full(n, 10.0),
        "close": np.full(n, 10.0),
        "volume": np.full(n, 1000.0),
    }
    panel["open"][0, 1] = 10.4  # 规则 10% 可买（涨停 11.0），Quote 实际涨停价 10.3 不可买
    state_dir = tmp_path / "state"

    control = fpw._advance_portfolio(panel, dates, symbols, ["600000.SH"], state_dir, "2026-07-28")
    assert control["advanced"] is True
    state = fpw._load_json(state_dir / "portfolio_state.json", {})
    assert "600000.SH" in state["positions"]  # 无 Quote：按规则买入

    state_dir2 = tmp_path / "state2"
    quotes = {"600000.SH": {"upper_limit_price": 10.3, "lower_limit_price": 9.7, "name": "浦发银行"}}
    result = fpw._advance_portfolio(panel, dates, symbols, ["600000.SH"], state_dir2, "2026-07-28", quotes=quotes)
    assert result["advanced"] is True
    state2 = fpw._load_json(state_dir2 / "portfolio_state.json", {})
    assert "600000.SH" not in state2["positions"]  # 实际涨停价 10.3：开盘 10.4 买不进
