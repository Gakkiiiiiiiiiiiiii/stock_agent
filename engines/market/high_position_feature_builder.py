from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from engines.backtest.execution import price_limit_pct


MIN_HIGH_POSITION_POOL_SIZE = 10
MIN_HIGH_POSITION_VALID_COUNT = 10
MIN_HIGH_POSITION_COVERAGE = 0.8


@dataclass(frozen=True)
class HighPositionFeatures:
    high_position_loss_ratio: float | None
    high_position_limit_down_ratio: float | None
    high_position_breakdown_ratio: float | None
    high_position_big_negative_count: int | None
    high_position_pool_size: int
    high_position_valid_count: int
    high_position_quote_coverage: float | None
    high_position_quality_flags: list[str]

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class HighPositionFeatureBuilder:
    """Build formal high-position retreat features from a frozen T-1 pool.

    Pool membership is decided only from data available at ``pool_as_of``. The
    observed loss/limit-down/breakdown metrics use ``outcome_as_of`` quotes or
    history, so T-day outcomes cannot change the high-position universe.
    """

    def __init__(self, bridge, max_symbols: int | None = None) -> None:
        self.bridge = bridge
        self.max_symbols = max_symbols

    def build(
        self,
        symbols: list[str],
        quotes: dict[str, Any],
        pool_as_of: date | None = None,
        outcome_as_of: date | None = None,
        as_of: date | None = None,
    ) -> HighPositionFeatures:
        if as_of is not None and outcome_as_of is None:
            outcome_as_of = as_of
        if outcome_as_of is not None and pool_as_of is None:
            pool_as_of = outcome_as_of - timedelta(days=1)
        if self.max_symbols is not None:
            symbols = symbols[: self.max_symbols]
        if not symbols:
            return _empty(["HIGH_POSITION_UNIVERSE_EMPTY"])

        end_day = outcome_as_of or date.today()
        start_day = end_day - timedelta(days=120)
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
            pool_records, outcome_record = _split_pool_and_outcome(records, pool_as_of, outcome_as_of)
            if len(pool_records) < 25:
                continue
            closes = np.array([item["close"] for item in pool_records if item["close"] > 0], dtype=float)
            amounts = np.array([item["amount"] for item in pool_records if item["amount"] > 0], dtype=float)
            highs = np.array([item["high"] for item in pool_records if item["high"] > 0], dtype=float)
            if len(closes) < 25:
                continue
            ret20 = closes[-1] / closes[-21] - 1 if len(closes) > 20 and closes[-21] > 0 else 0.0
            ret60 = closes[-1] / closes[-61] - 1 if len(closes) > 60 and closes[-61] > 0 else ret20
            high60 = float(np.max(highs[-60:])) if len(highs) >= 60 else float(np.max(closes))
            near_high = closes[-1] >= high60 * 0.95 if high60 > 0 else False
            recent_limit = _recent_limit_up(symbol, pool_records[-10:])
            amount_mean = float(np.nanmean(amounts[-20:])) if len(amounts) >= 20 else 0.0
            amount_ratio = float(amounts[-1] / amount_mean) if amount_mean > 0 else 0.0
            stats.append(
                {
                    "symbol": symbol,
                    "ret20": ret20,
                    "ret60": ret60,
                    "near_high": near_high,
                    "recent_limit": recent_limit,
                    "amount_ratio": amount_ratio,
                    "prev_close": float(closes[-1]),
                    "ma20": float(np.nanmean(closes[-20:])),
                    "outcome_record": outcome_record,
                }
            )
        if not stats:
            return _empty(["HIGH_POSITION_FEATURES_UNAVAILABLE"])

        ret20_cut = _quantile([item["ret20"] for item in stats], 0.9)
        ret60_cut = _quantile([item["ret60"] for item in stats], 0.9)
        amount_cut = _quantile([item["amount_ratio"] for item in stats], 0.8)
        pool = []
        for item in stats:
            return_leader = item["ret20"] >= ret20_cut or item["ret60"] >= ret60_cut
            is_high_position = return_leader or item["near_high"] or item["recent_limit"]
            is_crowded = item["amount_ratio"] >= amount_cut
            if is_high_position and (is_crowded or item["recent_limit"] or return_leader):
                pool.append(item)

        flags: list[str] = []
        if len(pool) < MIN_HIGH_POSITION_POOL_SIZE:
            flags.append("HIGH_POSITION_POOL_TOO_SMALL")
        if not pool:
            return _empty(flags or ["HIGH_POSITION_FEATURES_UNAVAILABLE"])

        loss = limit_down = breakdown = big_negative = valid = 0
        for item in pool:
            symbol = item["symbol"]
            quote = quotes.get(symbol) or {}
            outcome_record = item.get("outcome_record") or {}
            prev_close = float(item["prev_close"])
            last_price = _float(quote.get("last_price") or quote.get("price")) or _float(outcome_record.get("close"))
            quote_prev_close = _float(quote.get("last_close") or quote.get("pre_close"))
            open_price = _float(quote.get("open")) or _float(outcome_record.get("open"))
            if quote_prev_close > 0 and prev_close > 0 and abs(quote_prev_close / prev_close - 1) > 0.01:
                flags.append("HIGH_POSITION_PREV_CLOSE_MISMATCH")
            if last_price <= 0 or prev_close <= 0:
                continue
            valid += 1
            pct = last_price / prev_close - 1
            if pct < 0:
                loss += 1
            if pct <= -price_limit_pct(symbol) + 0.002:
                limit_down += 1
            if last_price < item["ma20"]:
                breakdown += 1
            if pct <= -0.07 or (open_price > 0 and last_price / open_price - 1 <= -0.05):
                big_negative += 1

        coverage = valid / len(pool) if pool else None
        if valid < MIN_HIGH_POSITION_VALID_COUNT:
            flags.append("HIGH_POSITION_VALID_COUNT_LOW")
        if coverage is not None and coverage < MIN_HIGH_POSITION_COVERAGE:
            flags.append("HIGH_POSITION_QUOTE_COVERAGE_LOW")
        flags = sorted(set(flags))
        if (
            len(pool) < MIN_HIGH_POSITION_POOL_SIZE
            or valid < MIN_HIGH_POSITION_VALID_COUNT
            or (coverage is not None and coverage < MIN_HIGH_POSITION_COVERAGE)
        ):
            return HighPositionFeatures(None, None, None, None, len(pool), valid, _round_or_none(coverage), flags)

        return HighPositionFeatures(
            high_position_loss_ratio=round(loss / valid, 6),
            high_position_limit_down_ratio=round(limit_down / valid, 6),
            high_position_breakdown_ratio=round(breakdown / valid, 6),
            high_position_big_negative_count=big_negative,
            high_position_pool_size=len(pool),
            high_position_valid_count=valid,
            high_position_quote_coverage=_round_or_none(coverage),
            high_position_quality_flags=flags,
        )


def _empty(flags: list[str]) -> HighPositionFeatures:
    return HighPositionFeatures(None, None, None, None, 0, 0, None, sorted(set(flags)))


def _split_pool_and_outcome(
    records: list[dict[str, Any]],
    pool_as_of: date | None,
    outcome_as_of: date | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    valid_records = [row for row in records if row["date"] is not None]
    if pool_as_of is None and outcome_as_of is None:
        if len(valid_records) <= 1:
            return valid_records, None
        return valid_records[:-1], valid_records[-1]
    pool_records = [row for row in valid_records if pool_as_of is None or row["date"] <= pool_as_of]
    outcome_candidates = [
        row
        for row in valid_records
        if (outcome_as_of is None or row["date"] <= outcome_as_of)
        and (pool_as_of is None or row["date"] > pool_as_of)
    ]
    outcome_record = outcome_candidates[-1] if outcome_candidates else None
    return pool_records, outcome_record


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(
            {
                "date": _date_value(row),
                "open": _float(row.get("open")),
                "high": _float(row.get("high")),
                "close": _float(row.get("close")),
                "amount": _float(row.get("amount")),
            }
        )
    for values in grouped.values():
        values.sort(key=lambda item: item["date"] or date.min)
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


def _date_value(row: dict[str, Any]) -> date | None:
    raw = row.get("trading_date") or row.get("date") or row.get("time")
    if raw is None:
        return None
    text = str(raw).split(" ")[0].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _float(value) -> float:
    if value is None or value == "":
        return 0.0
    return float(str(value).replace(",", ""))


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


__all__ = ["HighPositionFeatureBuilder", "HighPositionFeatures"]
