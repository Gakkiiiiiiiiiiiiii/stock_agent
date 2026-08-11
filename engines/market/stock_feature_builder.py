"""个股特征构建：对 K 线记录序列（dict 或对象）计算纯函数特征。

记录可为 dict（date/open/high/low/close/volume/amount 键）或具备同名属性的对象
（如 financial_agent.models.KlineRecord）。收益类特征单位为百分比（5.0 表示 5%）。
"""
from __future__ import annotations

from typing import Any, Sequence


def _field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _numeric_series(records: Sequence[Any], name: str) -> list[float]:
    series: list[float] = []
    for record in records:
        value = _field(record, name)
        if value is None:
            continue
        series.append(float(value))
    return series


def _return_pct(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    base = closes[-lookback - 1]
    if base <= 0:
        return None
    return (closes[-1] - base) / base * 100


def _ma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    tail = closes[-window:]
    return sum(tail) / len(tail)


def _volatility_pct(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    window = closes[-lookback - 1 :]
    returns = [cur / prev - 1 for prev, cur in zip(window, window[1:], strict=False) if prev > 0 and cur > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return (variance ** 0.5) * 100


def _max_drawdown_pct(closes: list[float], lookback: int) -> float | None:
    """最近 lookback 个交易日内的最大回撤（负百分比）。"""
    if len(closes) <= lookback:
        return None
    window = [value for value in closes[-lookback - 1 :] if value > 0]
    if len(window) < 2:
        return None
    peak = window[0]
    worst = 0.0
    for value in window[1:]:
        peak = max(peak, value)
        worst = min(worst, (value - peak) / peak)
    return worst * 100


def _amount_percentile(amounts: list[float], lookback: int = 120, min_records: int = 30) -> float | None:
    if len(amounts) < min_records:
        return None
    window = amounts[-lookback:]
    last = window[-1]
    return sum(1 for value in window if value <= last) / len(window) * 100


def compute_stock_features(records: Sequence[Any]) -> dict[str, Any]:
    """计算单只股票的特征字典；数据不足时对应字段为 None。"""
    ordered = list(records or [])
    closes = _numeric_series(ordered, "close")
    highs = _numeric_series(ordered, "high")
    amounts = _numeric_series(ordered, "amount")
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    last_close = closes[-1] if closes else None
    new_high_20d: bool | None = None
    if len(highs) >= 20 and last_close is not None:
        new_high_20d = bool(last_close >= max(highs[-20:]))
    as_of = _field(ordered[-1], "date") if ordered else None
    return {
        "as_of": as_of,
        "record_count": len(ordered),
        "close": last_close,
        "return_1d": _return_pct(closes, 1),
        "return_5d": _return_pct(closes, 5),
        "return_20d": _return_pct(closes, 20),
        "above_ma20": None if ma20 is None or last_close is None else bool(last_close > ma20),
        "above_ma60": None if ma60 is None or last_close is None else bool(last_close > ma60),
        "new_high_20d": new_high_20d,
        "amount": amounts[-1] if amounts else None,
        "amount_ma5": _ma(amounts, 5),
        "amount_percentile_120d": _amount_percentile(amounts),
        "volatility_20d": _volatility_pct(closes, 20),
        "max_drawdown_20d": _max_drawdown_pct(closes, 20),
    }
