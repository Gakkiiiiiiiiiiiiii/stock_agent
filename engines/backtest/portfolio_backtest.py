"""TopK 等权组合回测。

每个调仓日按 scores 面板选取得分最高的 top_k 只股票等权配置，
以当日开盘价为目标执行调仓（受涨跌停/停牌/T+1 约束），非调仓日持有不动。
面板形状均为 (n_symbols, n_days)，NaN 表示缺失；scores 为 NaN 表示当日不入选。
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from engines.backtest.execution import (
    PositionBook,
    can_buy,
    can_sell,
    cost_of,
    is_suspended,
)

# 调仓时忽略的价值偏差阈值（元），避免无意义的碎单
_MIN_TRADE_VALUE = 1.0


def _valid_price(value: float) -> bool:
    return not (value is None or np.isnan(value) or value <= 0)


class _PortfolioState:
    """回测过程中的组合状态：现金 + 各标的持仓台账（T+1 批次）。"""

    def __init__(self, initial_cash: float) -> None:
        self.cash = float(initial_cash)
        self.books: dict[int, PositionBook] = {}

    def shares_of(self, idx: int) -> float:
        book = self.books.get(idx)
        return book.total_shares if book else 0.0

    def sell(self, idx: int, date_idx: int, shares: float, price: float,
             symbols: Sequence[str], dates: Sequence, trades: list) -> float:
        """卖出指定份额（受 T+1 可卖数量限制），返回成交金额。"""
        book = self.books.get(idx)
        if book is None:
            return 0.0
        sold = book.pop_available(shares, date_idx)
        if sold <= 0:
            return 0.0
        value = sold * price
        cost = cost_of(value, "sell")
        self.cash += value - cost
        trades.append({
            "date": dates[date_idx], "symbol": symbols[idx], "side": "sell",
            "shares": sold, "price": price, "value": value, "cost": cost,
        })
        return value

    def buy(self, idx: int, date_idx: int, value: float, price: float,
            symbols: Sequence[str], dates: Sequence, trades: list) -> float:
        """按金额买入（受现金约束），返回实际成交金额。"""
        value = min(value, self.cash)
        if value <= _MIN_TRADE_VALUE:
            return 0.0
        # 成本随金额变化（佣金有最低5元），迭代两次逼近“金额+成本≤现金”
        for _ in range(2):
            cost = cost_of(value, "buy")
            if value + cost <= self.cash + 1e-9:
                break
            value = max(self.cash - cost, 0.0)
        if value <= _MIN_TRADE_VALUE:
            return 0.0
        cost = cost_of(value, "buy")
        shares = value / price
        self.cash -= value + cost
        book = self.books.setdefault(idx, PositionBook())
        book.add(shares, date_idx)
        trades.append({
            "date": dates[date_idx], "symbol": symbols[idx], "side": "buy",
            "shares": shares, "price": price, "value": value, "cost": cost,
        })
        return value


def run_topk_backtest(
    scores: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    symbols: Sequence[str],
    dates: Sequence,
    rebalance_interval: int = 5,
    top_k: int | None = None,
    initial_cash: float = 1_000_000.0,
) -> dict:
    """运行 TopK 等权组合回测，返回净值/基准/交易/换手/持仓日志。"""
    scores = np.asarray(scores, dtype=float)
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    n_symbols, n_days = scores.shape
    if top_k is None:
        # 默认池子的 1%，下限 5 只
        top_k = max(5, int(n_symbols * 0.01))
    top_k = max(1, min(top_k, n_symbols))
    rebalance_interval = max(1, int(rebalance_interval))

    state = _PortfolioState(initial_cash)
    last_close = np.full(n_symbols, np.nan)  # 各标的最近有效收盘价（停牌股估值用）

    equity_curve: list[float] = []
    benchmark_curve: list[float] = []
    trades: list[dict] = []
    daily_turnover: list[float] = []
    holdings_log: list[dict] = []

    def mark_price(idx: int) -> float:
        """估值价：当日收盘价，缺失时用最近有效收盘价。"""
        price = closes[idx, t]
        return price if _valid_price(price) else last_close[idx]

    for t in range(n_days):
        # 更新最近有效收盘价
        for i in range(n_symbols):
            if _valid_price(closes[i, t]):
                last_close[i] = closes[i, t]

        # 基准：全样本等权日收益净值（与组合同起点）
        if t == 0:
            benchmark_curve.append(float(initial_cash))
        else:
            rets = []
            for i in range(n_symbols):
                prev, cur = closes[i, t - 1], closes[i, t]
                if _valid_price(prev) and _valid_price(cur):
                    rets.append(cur / prev - 1)
            benchmark_curve.append(benchmark_curve[-1] * (1 + float(np.mean(rets)) if rets else 1.0))

        traded_value = 0.0
        if t % rebalance_interval == 0:
            traded_value = _rebalance_day(
                t, state, scores, opens, closes, volumes, last_close,
                symbols, dates, trades, top_k, n_symbols,
            )

        equity = state.cash + sum(
            state.shares_of(i) * mark_price(i)
            for i in state.books
            if _valid_price(mark_price(i))
        )
        equity_curve.append(equity)
        daily_turnover.append(traded_value / equity if equity > 0 else 0.0)
        holdings_log.append({
            symbols[i]: state.shares_of(i)
            for i in state.books
            if state.shares_of(i) > 1e-9
        })

    return {
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
        "trades": trades,
        "daily_turnover": daily_turnover,
        "holdings_log": holdings_log,
        "dates": list(dates),
    }


def _rebalance_day(
    t: int,
    state: _PortfolioState,
    scores: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    last_close: np.ndarray,
    symbols: Sequence[str],
    dates: Sequence,
    trades: list,
    top_k: int,
    n_symbols: int,
) -> float:
    """在调仓日 t 以开盘价执行调仓，返回当日成交总额（双边合计）。"""
    traded = 0.0

    def tradable(idx: int) -> bool:
        """当日可交易：未停牌且开盘价有效。"""
        return not is_suspended(volumes[idx, t]) and _valid_price(opens[idx, t])

    # 候选池：分数非 NaN 且当日可交易，取得分最高的 top_k 只
    eligible = [
        i for i in range(n_symbols)
        if not np.isnan(scores[i, t]) and tradable(i)
    ]
    ranked = sorted(eligible, key=lambda i: scores[i, t], reverse=True)[:top_k]
    target = set(ranked)

    prev_closes = closes[:, t - 1] if t > 0 else None

    def sell_allowed(idx: int) -> bool:
        # 首日无前收价，不做涨跌停约束
        if prev_closes is None or not _valid_price(prev_closes[idx]):
            return True
        return can_sell(opens[idx, t], prev_closes[idx], symbols[idx])

    def buy_allowed(idx: int) -> bool:
        if prev_closes is None or not _valid_price(prev_closes[idx]):
            return True
        return can_buy(opens[idx, t], prev_closes[idx], symbols[idx])

    # 未持有且开盘涨停买不进的股票不占目标名额，名额顺延给下一只
    if any(not buy_allowed(i) and state.shares_of(i) <= 1e-9 for i in ranked):
        ranked = [
            i for i in sorted(eligible, key=lambda i: scores[i, t], reverse=True)
            if buy_allowed(i) or state.shares_of(i) > 1e-9
        ][:top_k]
        target = set(ranked)

    # 第一步：卖出已调出目标池的持仓（跌停/停牌则保留）
    for idx in list(state.books):
        if idx in target or state.shares_of(idx) <= 1e-9:
            continue
        if not tradable(idx) or not sell_allowed(idx):
            continue
        traded += state.sell(idx, t, state.shares_of(idx), opens[idx, t], symbols, dates, trades)

    if not target:
        return traded

    # 第二步：按开盘价估算组合总市值，目标池内等权
    equity_open = state.cash
    for idx in state.books:
        shares = state.shares_of(idx)
        if shares <= 1e-9:
            continue
        price = opens[idx, t] if _valid_price(opens[idx, t]) else last_close[idx]
        if _valid_price(price):
            equity_open += shares * price
    target_value = equity_open / len(target)

    def current_value(idx: int) -> float:
        price = opens[idx, t] if _valid_price(opens[idx, t]) else last_close[idx]
        return state.shares_of(idx) * price if _valid_price(price) else 0.0

    # 第三步：先减持超配的目标股（受 T+1 与跌停约束）
    for idx in ranked:
        excess = current_value(idx) - target_value
        if excess <= _MIN_TRADE_VALUE:
            continue
        if not tradable(idx) or not sell_allowed(idx):
            continue
        traded += state.sell(idx, t, excess / opens[idx, t], opens[idx, t], symbols, dates, trades)

    # 第四步：买入/加仓低配的目标股（涨停不可买）
    for idx in ranked:
        gap = target_value - current_value(idx)
        if gap <= _MIN_TRADE_VALUE:
            continue
        if not buy_allowed(idx):
            continue
        traded += state.buy(idx, t, gap, opens[idx, t], symbols, dates, trades)

    return traded
