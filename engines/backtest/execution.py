"""A股交易执行规则：板块判定、涨跌停、T+1、停牌与成本模型。

供组合回测（engines/backtest/portfolio_backtest.py）使用，
假设所有交易以当日开盘价成交。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

COMMISSION_RATE = 0.00025  # 佣金万2.5
COMMISSION_MIN = 5.0  # 佣金最低5元
STAMP_TAX_RATE = 0.0005  # 印花税0.05%（仅卖出收取）
SLIPPAGE_RATE = 0.001  # 滑点，双边各千一

_LIMIT_BY_BOARD = {
    "主板": 0.10,
    "创业板": 0.20,
    "科创板": 0.20,
    "北交所": 0.30,
}
_ST_LIMIT = 0.05


def _code(symbol: str) -> str:
    """去掉 .SH/.SZ/.BJ 等交易所后缀，取纯数字代码。"""
    return str(symbol).split(".")[0].strip()


def board_of(symbol: str) -> str:
    """按代码前缀判定板块：60/68 主板，300/301 创业板，688 科创板，8xx/4xx/920 北交所。"""
    code = _code(symbol)
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith("920") or code.startswith(("8", "4")):
        return "北交所"
    if code.startswith(("60", "68")):
        return "主板"
    # 00 开头深市主板等其余情况按主板处理
    return "主板"


def price_limit_pct(symbol: str, is_st: bool = False) -> float:
    """涨跌幅限制比例，ST 一律 5%。"""
    if is_st:
        return _ST_LIMIT
    return _LIMIT_BY_BOARD[board_of(symbol)]


def limit_up_price(prev_close: float, symbol: str, is_st: bool = False) -> float:
    """涨停价：前收价 ×(1+limit)，保留 2 位小数。"""
    return round(prev_close * (1 + price_limit_pct(symbol, is_st)), 2)


def limit_down_price(prev_close: float, symbol: str, is_st: bool = False) -> float:
    """跌停价：前收价 ×(1-limit)，保留 2 位小数。"""
    return round(prev_close * (1 - price_limit_pct(symbol, is_st)), 2)


def can_buy(open_price: float, prev_close: float, symbol: str, is_st: bool = False) -> bool:
    """以开盘价成交的假设下，open≥涨停价则当日不可买入。"""
    return open_price < limit_up_price(prev_close, symbol, is_st)


def can_sell(open_price: float, prev_close: float, symbol: str, is_st: bool = False) -> bool:
    """以开盘价成交的假设下，open≤跌停价则当日不可卖出。"""
    return open_price > limit_down_price(prev_close, symbol, is_st)


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
