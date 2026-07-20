import numpy as np
import pytest

from engines.backtest.execution import (
    PositionBook,
    board_of,
    can_buy,
    can_sell,
    cost_of,
    is_suspended,
    limit_down_price,
    limit_up_price,
    price_limit_pct,
)
from engines.backtest.metrics import calc_portfolio_metrics
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.backtest.reports import render_portfolio_report

# ---------- 板块与涨跌停判定 ----------


@pytest.mark.parametrize(
    "symbol,board,pct",
    [
        ("600000.SH", "主板", 0.10),
        ("601318", "主板", 0.10),
        ("000001.SZ", "主板", 0.10),
        ("002594.SZ", "主板", 0.10),
        ("688981.SH", "科创板", 0.20),
        ("300750.SZ", "创业板", 0.20),
        ("301001", "创业板", 0.20),
        ("830799.BJ", "北交所", 0.30),
        ("430047", "北交所", 0.30),
        ("920001", "北交所", 0.30),
    ],
)
def test_board_and_limit_table(symbol, board, pct):
    assert board_of(symbol) == board
    assert price_limit_pct(symbol) == pytest.approx(pct)


def test_st_limit_always_5pct():
    assert price_limit_pct("600000.SH", is_st=True) == pytest.approx(0.05)
    assert price_limit_pct("300750.SZ", is_st=True) == pytest.approx(0.05)


def test_limit_prices_round_to_2dp():
    # 主板 10%：10.00 × 1.1 = 11.00
    assert limit_up_price(10.0, "600000.SH") == pytest.approx(11.00)
    assert limit_down_price(10.0, "600000.SH") == pytest.approx(9.00)
    # 创业板 20%
    assert limit_up_price(10.0, "300750.SZ") == pytest.approx(12.00)
    # ST 5%
    assert limit_up_price(10.0, "600000.SH", is_st=True) == pytest.approx(10.50)
    # 保留 2 位小数：10.01 × 1.1 = 11.011 -> 11.01
    assert limit_up_price(10.01, "600000.SH") == pytest.approx(11.01)


def test_limit_up_cannot_buy_limit_down_cannot_sell():
    symbol = "600000.SH"  # 主板 10%，前收 10.00，涨停 11.00 / 跌停 9.00
    assert can_buy(11.00, 10.0, symbol) is False
    assert can_buy(10.99, 10.0, symbol) is True
    assert can_sell(9.00, 10.0, symbol) is False
    assert can_sell(9.01, 10.0, symbol) is True


# ---------- T+1 ----------


def test_t1_same_day_not_sellable():
    book = PositionBook()
    book.add(100, buy_date=0)
    assert book.total_shares == pytest.approx(100)
    assert book.available_shares(0) == pytest.approx(0)  # 当日不可卖
    assert book.available_shares(1) == pytest.approx(100)  # 次日可卖
    assert book.pop_available(60, date_idx=0) == pytest.approx(0)
    assert book.pop_available(60, date_idx=1) == pytest.approx(60)
    assert book.total_shares == pytest.approx(40)


def test_t1_fifo_across_lots():
    book = PositionBook()
    book.add(100, buy_date=0)
    book.add(50, buy_date=1)
    assert book.available_shares(1) == pytest.approx(100)
    assert book.available_shares(2) == pytest.approx(150)
    # 先进先出：先取第一批 100，再取第二批 20
    assert book.pop_available(120, date_idx=2) == pytest.approx(120)
    assert book.total_shares == pytest.approx(30)
    assert book.lots[0].available_date == 2


# ---------- 成本模型 ----------


def test_cost_of_hand_computed():
    assert cost_of(0, "buy") == 0.0
    # 小额买入：佣金 10000×0.00025=2.5 < 5，取最低 5 元；无印花税；滑点 10
    assert cost_of(10_000, "buy") == pytest.approx(5 + 10)
    # 小额卖出：佣金 5 + 印花税 5 + 滑点 10
    assert cost_of(10_000, "sell") == pytest.approx(5 + 5 + 10)
    # 大额买入：佣金 250（超过最低 5 元）+ 滑点 1000
    assert cost_of(1_000_000, "buy") == pytest.approx(250 + 1000)
    # 大额卖出：佣金 250 + 印花税 500 + 滑点 1000
    assert cost_of(1_000_000, "sell") == pytest.approx(250 + 500 + 1000)


# ---------- 停牌 ----------


def test_is_suspended():
    assert is_suspended(0) is True
    assert is_suspended(float("nan")) is True
    assert is_suspended(None) is True
    assert is_suspended(12345) is False


# ---------- 组合回测（合成面板） ----------


def _make_panels(n_symbols=6, n_days=11, price=10.0):
    symbols = [f"60000{i}.SH" for i in range(n_symbols)]
    dates = [f"2024-01-{d + 1:02d}" for d in range(n_days)]
    base = np.full((n_symbols, n_days), price)
    volume = np.full((n_symbols, n_days), 1000.0)
    return symbols, dates, base.copy(), base.copy(), base.copy(), base.copy(), volume


def test_topk_equal_weight_and_cost_direction():
    symbols, dates, opens, highs, lows, closes, volume = _make_panels()
    # 固定选 0、1 号标的
    scores = np.full((6, 11), 1.0)
    scores[0] = 10.0
    scores[1] = 9.0
    initial = 100_000.0
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=initial,
    )
    # 首日建仓，等权持有 0、1 号
    day0 = result["holdings_log"][0]
    assert set(day0) == {symbols[0], symbols[1]}
    v0 = day0[symbols[0]] * 10.0
    v1 = day0[symbols[1]] * 10.0
    assert abs(v0 - v1) / (initial / 2) < 0.02
    # 成本扣减方向正确：建仓后净值略低于初始资金
    assert result["equity_curve"][0] < initial
    assert result["equity_curve"][0] > initial * 0.99
    # 首日 2 笔买入，非调仓日无换手
    day0_trades = [t for t in result["trades"] if t["date"] == dates[0]]
    assert len(day0_trades) == 2
    assert all(t["side"] == "buy" for t in day0_trades)
    assert result["daily_turnover"][0] > 0
    assert all(tv == 0 for tv in result["daily_turnover"][1:5])
    # 价格恒定，净值应基本平稳
    assert result["equity_curve"][-1] == pytest.approx(result["equity_curve"][0], rel=0.01)


def test_topk_rotation_sells_after_t1():
    symbols, dates, opens, highs, lows, closes, volume = _make_panels()
    scores = np.full((6, 11), 1.0)
    scores[0, :5] = 10.0
    scores[1, :5] = 9.0
    # 第二个调仓日（t=5）切换为 2、3 号标的
    scores[2, 5:] = 10.0
    scores[3, 5:] = 9.0
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
    )
    # t=5 调仓后持有 2、3 号（T+1：t=0 买入的份额此时早已可卖）
    assert set(result["holdings_log"][5]) == {symbols[2], symbols[3]}
    day5_trades = [t for t in result["trades"] if t["date"] == dates[5]]
    sells = {t["symbol"] for t in day5_trades if t["side"] == "sell"}
    buys = {t["symbol"] for t in day5_trades if t["side"] == "buy"}
    assert {symbols[0], symbols[1]} <= sells
    assert {symbols[2], symbols[3]} <= buys


def test_limit_up_and_suspended_excluded_from_buy():
    symbols, dates, opens, highs, lows, closes, volume = _make_panels()
    # 首日全部不入选，等 t=5 有前收价后再调仓
    scores = np.full((6, 11), np.nan)
    scores[0, 5:] = 100.0  # 得分最高，但 t=5 开盘涨停（前收 10，主板涨停 11.00）
    scores[1, 5:] = 90.0   # 得分第二，但 t=5 停牌（volume=0）
    scores[2, 5:] = 80.0
    scores[3, 5:] = 70.0
    opens[0, 5] = 11.0
    volume[1, 5] = 0.0
    result = run_topk_backtest(
        scores, opens, highs, lows, closes, volume, symbols, dates,
        rebalance_interval=5, top_k=2, initial_cash=100_000.0,
    )
    # 涨停的 0 号买不进、停牌的 1 号跳过，t=5 调仓后持有 2、3 号
    assert set(result["holdings_log"][5]) == {symbols[2], symbols[3]}
    day5_symbols = {t["symbol"] for t in result["trades"] if t["date"] == dates[5]}
    assert symbols[0] not in day5_symbols
    assert symbols[1] not in day5_symbols
    # 首日无可入选标的，不产生任何交易
    assert all(t["date"] != dates[0] for t in result["trades"])


# ---------- 指标与报告 ----------


def test_calc_portfolio_metrics_hand_computed():
    equity = [100.0, 110.0, 99.0, 121.0]
    dates = ["2024-01-01", "2024-01-02", "2024-02-01", "2024-02-02"]
    benchmark = [100.0, 100.0, 100.0, 100.0]
    trades = [
        {"symbol": "A", "side": "buy", "shares": 100, "value": 1000.0, "cost": 5.0},
        {"symbol": "A", "side": "sell", "shares": 100, "value": 1100.0, "cost": 6.0},
    ]
    metrics = calc_portfolio_metrics(
        equity, benchmark_curve=benchmark, trades=trades,
        daily_turnover=[0.5, 0.0, 0.0, 0.5], dates=dates,
    )
    assert metrics["total_return"] == pytest.approx(0.21, abs=1e-4)
    # 月度：1 月 110/100-1=0.1，2 月 121/110-1=0.1
    assert metrics["monthly_returns"] == {"2024-01": pytest.approx(0.1), "2024-02": pytest.approx(0.1)}
    # 基准收益为 0，超额年化 = 组合年化
    assert metrics["excess_annual_return"] == pytest.approx(metrics["annual_return"], abs=1e-4)
    assert metrics["avg_daily_turnover"] == pytest.approx(0.25)
    assert metrics["trade_count"] == 2
    # 一买一卖盈利（1100-6 vs 1000+5），胜率 1.0
    assert metrics["win_rate"] == pytest.approx(1.0)


def test_render_portfolio_report_markdown():
    result = {
        "equity_curve": [100.0, 110.0, 121.0],
        "benchmark_curve": [100.0, 101.0, 102.0],
        "trades": [],
        "daily_turnover": [0.1, 0.0, 0.0],
        "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
    }
    report = render_portfolio_report(result)
    assert "# 组合回测报告" in report
    assert "超额年化收益" in report
    assert "| 2024-01 |" in report
