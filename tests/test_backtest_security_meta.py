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
    with pytest.raises(ValueError, match="BACKTEST_BUY_RULE_AMBIGUOUS"):
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


# ---------- Security Meta 实际限价与统计口径（v2.2.4 第九轮） ----------


def test_security_meta_upper_limit_price_is_used():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    opens[0, 5] = 10.4
    meta = _meta_grid(4, 11, {(0, 5): {"upper_limit_price": 10.3, "lower_limit_price": 9.7}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] not in result["holdings_log"][5]


def test_security_meta_lower_limit_price_is_used():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    scores[0, :] = 100.0
    scores[1, :] = 90.0
    scores[0, 5:] = 1.0
    scores[1, 5:] = 1.0
    scores[2, 5:] = 100.0
    scores[3, 5:] = 90.0
    opens[0, 5] = 9.4
    meta = _meta_grid(4, 11, {(0, 5): {"lower_limit_price": 9.5}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] in result["holdings_log"][5]


def test_explicit_limit_panel_overrides_meta_limit_price():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    opens[0, 5] = 10.4
    meta = _meta_grid(4, 11, {(0, 5): {"upper_limit_price": 10.3}})
    upper = np.full((4, 11), np.nan)
    upper[0, 5] = 11.5  # 独立面板优先于 Meta（10.3）：10.4 < 11.5 可买
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True,
        security_meta=meta, upper_limit_prices=upper,
    )
    assert symbols[0] in result["holdings_log"][5]
    assert "UPPER_LIMIT_PRICE_CONFLICT" in result["price_limit_quality_flags"]


def test_daily_security_meta_dataclass_is_supported():
    from engines.backtest.execution import DailySecurityMeta

    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    scores[1, 5:] = 90.0
    opens[0, 5] = 10.4
    meta = _meta_grid(4, 11, {(0, 5): DailySecurityMeta(upper_limit_price=10.3, source="qmt")})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] not in result["holdings_log"][5]


def test_price_limit_coverage_counts_unique_cells():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    scores[0, :] = 100.0
    scores[1, :] = 90.0
    scores[0, 5:] = 1.0
    scores[1, 5:] = 1.0
    scores[2, 5:] = 100.0
    scores[3, 5:] = 90.0
    # 同一标的同一调仓日会触发多次 buy/sell 检查，但 Coverage 只按唯一 Cell 统计一次
    meta = _meta_grid(4, 11, {(0, 5): {"limit_up_rate": 15, "limit_down_rate": 12}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    # 实际评估的唯一 Cell 为 6（t=5 全部 4 只 + t=10 调仓涉及 2 只），其中 1 个有 meta；
    # 若按调用次数统计，分母会显著大于 6
    assert result["price_limit_fallback_count"] == 5
    assert result["price_limit_meta_coverage"] == pytest.approx(1 / 6, abs=1e-5)


def test_lower_limit_price_makes_sell_rule_precise(monkeypatch):
    from financial_agent.research_config import BacktestConfig, ResearchConfig

    monkeypatch.setattr(
        "engines.backtest.portfolio_backtest.get_research_config",
        lambda: ResearchConfig(backtest=BacktestConfig(fail_on_ambiguous_price_limit=True)),
    )
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-06-29")
    scores = np.full((4, 11), 1.0)
    scores[0, :5] = 100.0
    scores[1, :5] = 90.0
    scores[:, 5:] = np.nan  # t=5 全部调出：只触发卖出路径
    # 旧制度主板风险状态缺失，但有实际跌停价：卖出规则精确，不被阻断
    meta = _meta_grid(4, 11, {(i, 5): {"lower_limit_price": 9.0} for i in range(4)})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] not in result["holdings_log"][5]  # 成功卖出
    assert result["price_limit_sell_ambiguous_count"] == 0


def test_upper_limit_missing_does_not_block_precise_sell(monkeypatch):
    from financial_agent.research_config import BacktestConfig, ResearchConfig

    monkeypatch.setattr(
        "engines.backtest.portfolio_backtest.get_research_config",
        lambda: ResearchConfig(backtest=BacktestConfig(fail_on_ambiguous_price_limit=True)),
    )
    symbols, dates, opens, highs, lows, closes, volume = _panels(start="2026-06-29")
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    # 旧制度主板、状态缺失、无涨停信息：买入规则模糊 → 严格模式阻断
    meta = _meta_grid(4, 11, {(0, 5): {"lower_limit_price": 9.0}})
    with pytest.raises(ValueError, match="BACKTEST_BUY_RULE_AMBIGUOUS"):
        run_topk_backtest(
            scores, opens, highs, lows, closes, volume, symbols, dates,
            rebalance_interval=5, top_k=2, initial_cash=100_000.0,
            allow_unsafe_without_metadata=True, security_meta=meta,
        )


def test_source_only_meta_does_not_count_as_limit_meta():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    meta = _meta_grid(4, 11, {(0, 5): {"source": "qmt", "data_version": "dv-1"}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert result["price_limit_meta_coverage"] == 0.0
    assert result["price_limit_buy_meta_coverage"] == 0.0
    assert result["price_limit_sell_meta_coverage"] == 0.0


def test_name_only_meta_counts_as_limit_meta():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    meta = _meta_grid(4, 11, {(0, 5): {"name": "ST测试"}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert result["price_limit_meta_coverage"] > 0
    assert result["price_limit_buy_meta_coverage"] > 0
    assert result["price_limit_sell_meta_coverage"] > 0


def test_invalid_upper_limit_price_sets_quality_flag(monkeypatch):
    from financial_agent.research_config import BacktestConfig, ResearchConfig

    monkeypatch.setattr(
        "engines.backtest.portfolio_backtest.get_research_config",
        lambda: ResearchConfig(backtest=BacktestConfig(fail_on_invalid_price_limit_meta=False)),
    )
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    meta = _meta_grid(4, 11, {(0, 5): {"upper_limit_price": "nan"}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert "INVALID_UPPER_LIMIT_PRICE" in result["price_limit_quality_flags"]
    assert result["invalid_upper_limit_price_count"] == 1


def test_strict_mode_rejects_invalid_actual_limit_price(monkeypatch):
    from financial_agent.research_config import BacktestConfig, ResearchConfig

    monkeypatch.setattr(
        "engines.backtest.portfolio_backtest.get_research_config",
        lambda: ResearchConfig(backtest=BacktestConfig(fail_on_invalid_price_limit_meta=True)),
    )
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    meta = _meta_grid(4, 11, {(0, 5): {"lower_limit_price": -1}})
    with pytest.raises(ValueError, match="PRICE_LIMIT_PRICE_INVALID:lower_limit_price"):
        run_topk_backtest(
            scores, opens, highs, lows, closes, volume, symbols, dates,
            rebalance_interval=5, top_k=2, initial_cash=100_000.0,
            allow_unsafe_without_metadata=True, security_meta=meta,
        )


@pytest.mark.parametrize("key", ["limit_up_rate", "LimitUpRate", "up_limit_rate", "涨停幅度"])
def test_all_up_rate_aliases_match_execution_and_coverage(key):
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    opens[0, 5] = 10.6
    meta = _meta_grid(4, 11, {(0, 5): {key: 5}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=1, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] not in result["holdings_log"][5]
    assert result["price_limit_buy_meta_coverage"] > 0


@pytest.mark.parametrize("key", ["lower_limit_price", "LowerLimitPrice", "down_limit_price", "跌停价"])
def test_all_lower_price_aliases_match_execution_and_coverage(key):
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), 1.0)
    scores[0, :5] = 100.0
    scores[:, 5:] = np.nan
    opens[0, 5] = 9.4
    meta = _meta_grid(4, 11, {(0, 5): {key: 9.5}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=1, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert symbols[0] in result["holdings_log"][5]
    assert result["price_limit_sell_meta_coverage"] > 0


def test_upper_only_meta_has_sell_fallback():
    symbols, dates, opens, highs, lows, closes, volume = _panels()
    scores = np.full((4, 11), np.nan)
    scores[0, 5:] = 100.0
    meta = _meta_grid(4, 11, {(0, 5): {"upper_limit_price": 11.0}})
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=1, initial_cash=100_000.0,
        allow_unsafe_without_metadata=True, security_meta=meta,
    )
    assert result["price_limit_buy_fallback_count"] < result["price_limit_sell_fallback_count"]
    assert result["price_limit_any_side_fallback_count"] >= result["price_limit_both_sides_fallback_count"]
