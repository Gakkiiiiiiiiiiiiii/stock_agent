"""A股交易执行规则：板块判定、涨跌停、T+1、停牌与成本模型。

供组合回测（engines/backtest/portfolio_backtest.py）使用，
假设所有交易以当日开盘价成交。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from engines.market.price_limit_rules import board_of, resolve_price_limit_rule, round_to_tick

COMMISSION_RATE = 0.00025  # 佣金万2.5
COMMISSION_MIN = 5.0  # 佣金最低5元
STAMP_TAX_RATE = 0.0005  # 印花税0.05%（仅卖出收取）
SLIPPAGE_RATE = 0.001  # 滑点，双边各千一


def price_limit_pct(symbol: str, is_st: bool = False, trade_date=None, quote: dict | None = None) -> float:
    """按交易日解析涨跌幅限制比例。历史回放必须显式传入 trade_date。"""
    rule = resolve_price_limit_rule(symbol, trade_date, quote=quote, is_risk_warning=is_st)
    return rule.limit_up_pct


def limit_up_price(prev_close: float, symbol: str, is_st: bool = False, trade_date=None, quote: dict | None = None) -> float:
    """涨停价：前收价 ×(1+limit)，按交易 tick 半入取整。"""
    limit = price_limit_pct(symbol, is_st=is_st, trade_date=trade_date, quote=quote)
    return round_to_tick(prev_close * (1 + limit))


def limit_down_price(prev_close: float, symbol: str, is_st: bool = False, trade_date=None, quote: dict | None = None) -> float:
    """跌停价：前收价 ×(1-limit)，按交易 tick 半入取整。"""
    limit = price_limit_pct(symbol, is_st=is_st, trade_date=trade_date, quote=quote)
    return round_to_tick(prev_close * (1 - limit))


def can_buy(open_price: float, prev_close: float, symbol: str, is_st: bool = False, trade_date=None, quote: dict | None = None) -> bool:
    """以开盘价成交的假设下，open≥涨停价则当日不可买入。"""
    return open_price < limit_up_price(prev_close, symbol, is_st=is_st, trade_date=trade_date, quote=quote)


def can_sell(open_price: float, prev_close: float, symbol: str, is_st: bool = False, trade_date=None, quote: dict | None = None) -> bool:
    """以开盘价成交的假设下，open≤跌停价则当日不可卖出。"""
    return open_price > limit_down_price(prev_close, symbol, is_st=is_st, trade_date=trade_date, quote=quote)


def is_suspended(volume: float | None) -> bool:
    """当日无 K 线（NaN/None）或 volume=0 视为停牌。"""
    if volume is None:
        return True
    try:
        v = float(volume)
    except (TypeError, ValueError):
        return True
    return math.isnan(v) or v <= 0


def cost_of(trade_value: float, side: str) -> float:
    """交易成本：佣金万2.5（最低5元）+ 卖出印花税0.05% + 双边滑点千一。

    side 取 "buy" / "sell"。
    """
    if trade_value <= 0:
        return 0.0
    commission = max(trade_value * COMMISSION_RATE, COMMISSION_MIN)
    stamp = trade_value * STAMP_TAX_RATE if side == "sell" else 0.0
    slippage = trade_value * SLIPPAGE_RATE
    return commission + stamp + slippage


@dataclass
class PositionLot:
    """持仓批次，available_date 为可卖出的交易日索引（买入日 +1，实现 T+1）。"""

    shares: float
    available_date: int


@dataclass
class PositionBook:
    """单标的持仓台账，按批次记录份额以实现 T+1 约束。"""

    lots: list[PositionLot] = field(default_factory=list)

    def add(self, shares: float, buy_date: int) -> None:
        """买入记账，份额自次日起可卖。"""
        self.lots.append(PositionLot(shares=shares, available_date=buy_date + 1))

    @property
    def total_shares(self) -> float:
        return sum(lot.shares for lot in self.lots)

    def available_shares(self, date_idx: int) -> float:
        """当日可卖份额（available_date <= 当日）。"""
        return sum(lot.shares for lot in self.lots if lot.available_date <= date_idx)

    def pop_available(self, shares: float, date_idx: int) -> float:
        """先进先出取出可卖份额，返回实际取出数量（受 T+1 限制可能少于请求）。"""
        remaining = min(shares, self.available_shares(date_idx))
        taken = remaining
        for lot in self.lots:
            if remaining <= 1e-12:
                break
            if lot.available_date > date_idx:
                continue
            take = min(lot.shares, remaining)
            lot.shares -= take
            remaining -= take
        self.lots = [lot for lot in self.lots if lot.shares > 1e-9]
        return taken
