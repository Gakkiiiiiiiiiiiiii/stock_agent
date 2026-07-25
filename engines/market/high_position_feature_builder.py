from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from engines.backtest.execution import price_limit_pct


@dataclass(frozen=True)
class HighPositionFeatures:
    high_position_loss_ratio: float | None
    high_position_limit_down_ratio: float | None
    high_position_breakdown_ratio: float | None
    high_position_big_negative_count: int | None
    high_position_pool_size: int

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class HighPositionFeatureBuilder:
    def __init__(self, bridge, max_symbols: int | None = None) -> None:
        self.bridge = bridge
        self.max_symbols = max_symbols

    def build(self, symbols: list[str], quotes: dict[str, Any], as_of: date | None = None) -> HighPositionFeatures:
        if self.max_symbols is not None:
            symbols = symbols[: self.max_symbols]
        if not symbols:
            return HighPositionFeatures(None, None, None, None, 0)
        end_day = as_of or date.today()
        start_day = end_day - timedelta(days=100)
        rows: list[dict[str, Any]] = []
        for chunk in _batched(symbols, 200):
            rows.extend(
                self.bridge.get_history(
                    symbols=chunk,
                    period="1d",
                    start_time=start_day.strftime("%Y%m%d"),
                    end_time=end_day.strftime("%Y%m%d"),
                    dividend_type="front",
                    fill_data=True,
                    prefer_cache_first=True,
                )
            )
        grouped = _group_rows(rows)
        stats = []
        for symbol in symbols:
            records = grouped.get(symbol) or []
            if len(records) < 25:
                continue
            closes = np.array([item["close"] for item in records if item["close"] > 0], dtype=float)
            amounts = np.array([item["amount"] for item in records if item["amount"] > 0], dtype=float)
            if len(closes) < 25:
                continue
            ret20 = closes[-1] / closes[-21] - 1 if len(closes) > 20 and closes[-21] > 0 else 0.0
            ret60 = closes[-1] / closes[-61] - 1 if len(closes) > 60 and closes[-61] > 0 else ret20
            high60 = float(np.max(closes[-60:])) if len(closes) >= 60 else float(np.max(closes))
            near_high = closes[-1] >= high60 * 0.95 if high60 > 0 else False
            recent_limit = _recent_limit_up(symbol, records[-10:])
            amount_ratio = float(amounts[-1] / np.nanmean(amounts[-20:])) if len(amounts) >= 20 and np.nanmean(amounts[-20:]) > 0 else 0.0
            stats.append(
                {
                    "symbol": symbol,
                    "ret20": ret20,
                    "ret60": ret60,
                    "near_high": near_high,
                    "recent_limit": recent_limit,
                    "amount_ratio": amount_ratio,
                    "close": closes[-1],
                    "ma20": float(np.nanmean(closes[-20:])),
                }
            )
        if not stats:
            return HighPositionFeatures(None, None, None, None, 0)
        ret20_cut = _quantile([item["ret20"] for item in stats], 0.9)
        ret60_cut = _quantile([item["ret60"] for item in stats], 0.9)
        amount_cut = _quantile([item["amount_ratio"] for item in stats], 0.8)
        pool = [
            item
            for item in stats
            if item["ret20"] >= ret20_cut
            or item["ret60"] >= ret60_cut
            or item["near_high"]
            or item["recent_limit"]
            or item["amount_ratio"] >= amount_cut
        ]
        if not pool:
            return HighPositionFeatures(None, None, None, None, 0)
        loss = limit_down = breakdown = big_negative = valid = 0
        for item in pool:
            symbol = item["symbol"]
            quote = quotes.get(symbol) or {}
            last_price = _float(quote.get("last_price") or quote.get("price"))
            last_close = _float(quote.get("last_close") or quote.get("pre_close"))
            open_price = _float(quote.get("open"))
            if last_price <= 0 or last_close <= 0:
                continue
            valid += 1
            pct = last_price / last_close - 1
            if pct < 0:
                loss += 1
            if pct <= -price_limit_pct(symbol) + 0.002:
                limit_down += 1
            if last_price < item["ma20"]:
                breakdown += 1
            if pct <= -0.07 or (open_price > 0 and last_price / open_price - 1 <= -0.05):
                big_negative += 1
        if valid == 0:
            return HighPositionFeatures(None, None, None, None, len(pool))
        return HighPositionFeatures(
            high_position_loss_ratio=round(loss / valid, 6),
            high_position_limit_down_ratio=round(limit_down / valid, 6),
            high_position_breakdown_ratio=round(breakdown / valid, 6),
            high_position_big_negative_count=big_negative,
            high_position_pool_size=len(pool),
        )


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, float]]]:
    grouped: dict[str, list[dict[str, float]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(
            {
                "date": _date_key(row),
                "open": _float(row.get("open")),
                "close": _float(row.get("close")),
                "amount": _float(row.get("amount")),
            }
        )
    for values in grouped.values():
        values.sort(key=lambda item: item["date"])
    return grouped


def _recent_limit_up(symbol: str, records: list[dict[str, Any]]) -> bool:
    for prev, cur in zip(records, records[1:], strict=False):
        prev_close = _float(prev.get("close"))
        cur_close = _float(cur.get("close"))
        if prev_close > 0 and cur_close / prev_close - 1 >= price_limit_pct(symbol) - 0.002:
            return True
    return False


def _quantile(values: list[float], q: float) -> float:
    return float(np.nanquantile(np.array(values, dtype=float), q))


def _date_key(row: dict[str, Any]) -> str:
    return str(row.get("trading_date") or row.get("date") or row.get("time") or "")


def _float(value) -> float:
    if value is None or value == "":
        return 0.0
    return float(str(value).replace(",", ""))


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


__all__ = ["HighPositionFeatureBuilder", "HighPositionFeatures"]
