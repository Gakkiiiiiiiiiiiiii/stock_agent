"""A股交易执行规则：板块判定、涨跌停、T+1、停牌与成本模型。

供组合回测（engines/backtest/portfolio_backtest.py）使用，
假设所有交易以当日开盘价成交。
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

from engines.market.price_limit_rules import board_of, resolve_price_limit_rule, round_to_tick

COMMISSION_RATE = 0.00025  # 佣金万2.5
COMMISSION_MIN = 5.0  # 佣金最低5元
STAMP_TAX_RATE = 0.0005  # 印花税0.05%（仅卖出收取）
SLIPPAGE_RATE = 0.001  # 滑点，双边各千一

DEFAULT_TICK_SIZE = 0.01  # 默认最小价格变动单位（元）


def _resolve_tick_size(quote: dict | None, default: float = DEFAULT_TICK_SIZE) -> float:
    """行情元数据可覆盖默认 tick；缺失、非有限或非正时回退 0.01 元。"""
    payload = quote or {}
    for key in ("tick_size", "price_tick", "min_price_change"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            tick = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(tick) and tick > 0:
            return tick
    return default


@dataclass(frozen=True)
class DailySecurityMeta:
    """单标的单交易日的交易规则元数据（历史回放按日对齐）。"""

    is_risk_warning: bool | None = None
    listing_stage: str | None = None
    limit_up_rate: float | None = None
    limit_down_rate: float | None = None
    upper_limit_price: float | None = None
    lower_limit_price: float | None = None
    tick_size: float | None = None
    source: str | None = None
    data_version: str | None = None


@dataclass(frozen=True)
class TradeRuleContext:
    """单笔交易的可执行性判断上下文：实际 Limit Price 优先于比例规则。"""

    symbol: str
    trade_date: object
    prev_close: float
    open_price: float
    quote: dict
    upper_limit_price: float | None = None
    lower_limit_price: float | None = None


def _meta_risk_warning(meta: dict | None) -> bool | None:
    """三态风险状态：缺失返回 None，不得当作 False 覆盖名称识别。"""
    value = (meta or {}).get("is_risk_warning")
    if value is None or value == "":
        return None
    return bool(value)


def _checked_limit_price(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"PRICE_LIMIT_PRICE_INVALID:{field_name}")
    return price


def can_buy_with_context(context: TradeRuleContext) -> bool:
    """实际涨停价优先；缺失时回退到比例规则（quote/is_st/trade_date）。"""
    upper = _checked_limit_price(context.upper_limit_price, "upper_limit_price")
    if upper is not None:
        return context.open_price < upper
    return can_buy(
        context.open_price,
        context.prev_close,
        context.symbol,
        is_st=_meta_risk_warning(context.quote),
        trade_date=context.trade_date,
        quote=context.quote,
    )


def can_sell_with_context(context: TradeRuleContext) -> bool:
    """实际跌停价优先；缺失时回退到比例规则（quote/is_st/trade_date）。"""
    lower = _checked_limit_price(context.lower_limit_price, "lower_limit_price")
    if lower is not None:
        return context.open_price > lower
    return can_sell(
        context.open_price,
        context.prev_close,
        context.symbol,
        is_st=_meta_risk_warning(context.quote),
        trade_date=context.trade_date,
        quote=context.quote,
    )


def price_limit_up_pct(symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> float:
    """涨停比例。历史回放必须显式传入 trade_date。无涨跌幅阶段返回 +inf。"""
    rule = resolve_price_limit_rule(symbol, trade_date, quote=quote, is_risk_warning=is_st)
    if not rule.has_price_limit:
        return float("inf")
    if rule.limit_up_pct is None:
        raise ValueError("PRICE_LIMIT_RULE_INCOMPLETE:limit_up_pct")
    return rule.limit_up_pct


def price_limit_down_pct(symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> float:
    """跌停比例。历史回放必须显式传入 trade_date。无涨跌幅阶段返回 +inf。"""
    rule = resolve_price_limit_rule(symbol, trade_date, quote=quote, is_risk_warning=is_st)
    if not rule.has_price_limit:
        return float("inf")
    if rule.limit_down_pct is None:
        raise ValueError("PRICE_LIMIT_RULE_INCOMPLETE:limit_down_pct")
    return rule.limit_down_pct


def price_limit_pct(symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> float:
    """Deprecated: 仅返回涨停比例，请改用 price_limit_up_pct()/price_limit_down_pct()。"""
    warnings.warn(
        "price_limit_pct() returns the upper limit only; use "
        "price_limit_up_pct() or price_limit_down_pct()",
        DeprecationWarning,
        stacklevel=2,
    )
    return price_limit_up_pct(symbol, is_st=is_st, trade_date=trade_date, quote=quote)


def limit_up_price(prev_close: float, symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> float:
    """涨停价：前收价 ×(1+limit_up)，按交易 tick 半入取整。无涨跌幅阶段返回 +inf。"""
    rule = resolve_price_limit_rule(symbol, trade_date, quote=quote, is_risk_warning=is_st)
    if not rule.has_price_limit:
        return float("inf")
    if rule.limit_up_pct is None:
        raise ValueError("PRICE_LIMIT_RULE_INCOMPLETE:limit_up_pct")
    return round_to_tick(prev_close * (1 + rule.limit_up_pct), tick_size=_resolve_tick_size(quote))


def limit_down_price(prev_close: float, symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> float:
    """跌停价：前收价 ×(1-limit_down)，按交易 tick 半入取整。无涨跌幅阶段返回 -inf。"""
    rule = resolve_price_limit_rule(symbol, trade_date, quote=quote, is_risk_warning=is_st)
    if not rule.has_price_limit:
        return float("-inf")
    if rule.limit_down_pct is None:
        raise ValueError("PRICE_LIMIT_RULE_INCOMPLETE:limit_down_pct")
    return round_to_tick(prev_close * (1 - rule.limit_down_pct), tick_size=_resolve_tick_size(quote))


def can_buy(open_price: float, prev_close: float, symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> bool:
    """以开盘价成交的假设下，open≥涨停价则当日不可买入。"""
    return open_price < limit_up_price(prev_close, symbol, is_st=is_st, trade_date=trade_date, quote=quote)


def can_sell(open_price: float, prev_close: float, symbol: str, is_st: bool | None = None, trade_date=None, quote: dict | None = None) -> bool:
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
