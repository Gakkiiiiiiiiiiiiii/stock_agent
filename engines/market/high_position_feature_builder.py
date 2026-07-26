from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from engines.market.price_limit_rules import (
    MAIN_BOARD_ST_10_EFFECTIVE_DATE,
    board_of,
    resolve_price_limit_rule,
)
from financial_agent.research_config import get_research_config


@dataclass(frozen=True)
class RecentLimitResult:
    """近期涨停识别结果：hit 为结论，reliable 表示证据完整性，quality_flags 记录降级原因。"""

    hit: bool
    reliable: bool
    quality_flags: list[str]


@dataclass(frozen=True)
class HighPositionFeatures:
    high_position_loss_ratio: float | None
    high_position_limit_down_ratio: float | None
    high_position_breakdown_ratio: float | None
    high_position_big_negative_count: int | None
    high_position_pool_size: int
    high_position_valid_count: int
    high_position_quote_coverage: float | None
    high_position_prev_close_mismatch_count: int
    high_position_prev_close_mismatch_ratio: float | None
    high_position_quality_flags: list[str]
    # 命中涨停但历史风险状态缺失（证据不可靠）的标的数，仅作诊断，不单独触发入池
    high_position_uncertain_limit_count: int = 0

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
                    dividend_type="none",
                    fill_data=False,
                    prefer_cache_first=True,
                )
            )
        grouped = _group_rows(rows)
        stats = []
        global_flags: list[str] = []
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
            config = get_research_config().high_position
            near_high = closes[-1] >= high60 * config.near_high_ratio if high60 > 0 else False
            recent_limit_result = _recent_limit_up(symbol, pool_records[-10:])
            global_flags.extend(recent_limit_result.quality_flags)
            amount_mean = float(np.nanmean(amounts[-20:])) if len(amounts) >= 20 else 0.0
            amount_ratio = float(amounts[-1] / amount_mean) if amount_mean > 0 else 0.0
            stats.append(
                {
                    "symbol": symbol,
                    "ret20": ret20,
                    "ret60": ret60,
                    "near_high": near_high,
                    # 可靠涨停可独立入池；不可靠涨停只能增强已有高位证据
                    "recent_limit_confirmed": recent_limit_result.hit and recent_limit_result.reliable,
                    "recent_limit_uncertain": recent_limit_result.hit and not recent_limit_result.reliable,
                    "amount_ratio": amount_ratio,
                    "prev_close": float(closes[-1]),
                    "ma20": float(np.nanmean(closes[-20:])),
                    "outcome_record": outcome_record,
                }
            )
        if not stats:
            return _empty(["HIGH_POSITION_FEATURES_UNAVAILABLE"])

        config = get_research_config().high_position
        ret20_cut = _quantile([item["ret20"] for item in stats], config.ret20_quantile)
        ret60_cut = _quantile([item["ret60"] for item in stats], config.ret60_quantile)
        amount_cut = _quantile([item["amount_ratio"] for item in stats], config.amount_ratio_quantile)
        pool = []
        uncertain_limit_count = 0
        for item in stats:
            return_leader = item["ret20"] >= ret20_cut or item["ret60"] >= ret60_cut
            is_high_position = (
                return_leader
                or item["near_high"]
                or item["recent_limit_confirmed"]
                or (item["recent_limit_uncertain"] and (return_leader or item["near_high"]))
            )
            is_crowded = item["amount_ratio"] >= amount_cut
            if is_high_position and (is_crowded or item["recent_limit_confirmed"] or return_leader):
                pool.append(item)
            if item["recent_limit_uncertain"]:
                uncertain_limit_count += 1
        if uncertain_limit_count:
            global_flags.append("HIGH_POSITION_UNCERTAIN_LIMIT_EVIDENCE")

        flags: list[str] = list(global_flags)
        if len(pool) < config.min_pool_size:
            flags.append("HIGH_POSITION_POOL_TOO_SMALL")
        if not pool:
            return _empty(flags or ["HIGH_POSITION_FEATURES_UNAVAILABLE"], uncertain_limit_count)

        loss = limit_down = breakdown = big_negative = valid = mismatch = 0
        for item in pool:
            symbol = item["symbol"]
            quote = quotes.get(symbol) or {}
            outcome_record = item.get("outcome_record") or {}
            prev_close = float(item["prev_close"])
            last_price = _float(quote.get("last_price") or quote.get("price")) or _float(outcome_record.get("close"))
            quote_prev_close = _float(quote.get("last_close") or quote.get("pre_close"))
            open_price = _float(quote.get("open")) or _float(outcome_record.get("open"))
            if quote_prev_close > 0 and prev_close > 0 and abs(quote_prev_close / prev_close - 1) > config.prev_close_mismatch_threshold:
                mismatch += 1
            if last_price <= 0 or prev_close <= 0:
                continue
            valid += 1
            denominator = quote_prev_close if quote_prev_close > 0 else prev_close
            pct = last_price / denominator - 1
            if pct < 0:
                loss += 1
            rule = resolve_price_limit_rule(
                symbol,
                trade_date=end_day,
                quote=quote,
                is_risk_warning=_is_st_quote(quote),
            )
            if rule.has_price_limit and rule.limit_down_pct is not None and pct <= -rule.limit_down_pct + 0.002:
                limit_down += 1
            if last_price < item["ma20"]:
                breakdown += 1
            if pct <= -0.07 or (open_price > 0 and last_price / open_price - 1 <= -0.05):
                big_negative += 1

        coverage = valid / len(pool) if pool else None
        mismatch_ratio = mismatch / len(pool) if pool else None
        if valid < config.min_valid_count:
            flags.append("HIGH_POSITION_VALID_COUNT_LOW")
        if coverage is not None and coverage < config.min_quote_coverage:
            flags.append("HIGH_POSITION_QUOTE_COVERAGE_LOW")
        if mismatch_ratio is not None and mismatch_ratio > config.max_mismatch_ratio:
            flags.append("HIGH_POSITION_PREV_CLOSE_MISMATCH")
        flags = sorted(set(flags))
        if (
            len(pool) < config.min_pool_size
            or valid < config.min_valid_count
            or (coverage is not None and coverage < config.min_quote_coverage)
            or (mismatch_ratio is not None and mismatch_ratio > config.max_mismatch_ratio)
            # 配置要求时，旧制度 ST 状态缺失直接阻断正式指标，不允许伪装为精确结果
            or (
                config.block_on_historical_risk_status_missing
                and "HISTORICAL_RISK_WARNING_STATUS_UNAVAILABLE" in flags
            )
        ):
            return HighPositionFeatures(
                None, None, None, None, len(pool), valid, _round_or_none(coverage),
                mismatch, _round_or_none(mismatch_ratio), flags,
                high_position_uncertain_limit_count=uncertain_limit_count,
            )

        return HighPositionFeatures(
            high_position_loss_ratio=round(loss / valid, 6),
            high_position_limit_down_ratio=round(limit_down / valid, 6),
            high_position_breakdown_ratio=round(breakdown / valid, 6),
            high_position_big_negative_count=big_negative,
            high_position_pool_size=len(pool),
            high_position_valid_count=valid,
            high_position_quote_coverage=_round_or_none(coverage),
            high_position_prev_close_mismatch_count=mismatch,
            high_position_prev_close_mismatch_ratio=_round_or_none(mismatch_ratio),
            high_position_quality_flags=flags,
            high_position_uncertain_limit_count=uncertain_limit_count,
        )


def _empty(flags: list[str], uncertain_limit_count: int = 0) -> HighPositionFeatures:
    return HighPositionFeatures(
        None, None, None, None, 0, 0, None, 0, None, sorted(set(flags)),
        high_position_uncertain_limit_count=uncertain_limit_count,
    )


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
                # 保留名称与风险警示状态：2026-07-06 前主板 ST 适用 5%，
                # 缺失状态必须显式标记，不能静默按 10% 处理。
                "name": (
                    row.get("name")
                    or row.get("stock_name")
                    or row.get("instrument_name")
                ),
                "is_risk_warning": _optional_bool(
                    row.get("is_risk_warning")
                    if row.get("is_risk_warning") is not None
                    else row.get("risk_warning")
                    if row.get("risk_warning") is not None
                    else row.get("is_st")
                ),
                "limit_up_rate": _optional_float(
                    row.get("limit_up_rate")
                    if row.get("limit_up_rate") is not None
                    else row.get("LimitUpRate")
                ),
                "limit_down_rate": _optional_float(
                    row.get("limit_down_rate")
                    if row.get("limit_down_rate") is not None
                    else row.get("LimitDownRate")
                ),
                "upper_limit_price": _optional_float(row.get("upper_limit_price")),
                "lower_limit_price": _optional_float(row.get("lower_limit_price")),
            }
        )
    for values in grouped.values():
        values.sort(key=lambda item: item["date"] or date.min)
    return grouped


def _recent_limit_up(symbol: str, records: list[dict[str, Any]]) -> RecentLimitResult:
    flags: list[str] = []
    for prev, cur in zip(records, records[1:], strict=False):
        prev_close = _float(prev.get("close"))
        cur_close = _float(cur.get("close"))
        cur_date = cur.get("date")
        if cur_date is None:
            continue
        risk_warning = cur.get("is_risk_warning")
        explicit_rate = cur.get("limit_up_rate") is not None
        if (
            board_of(symbol) == "主板"
            and cur_date < MAIN_BOARD_ST_10_EFFECTIVE_DATE
            and risk_warning is None
            and not explicit_rate
        ):
            flags.append("HISTORICAL_RISK_WARNING_STATUS_UNAVAILABLE")
        rule = resolve_price_limit_rule(
            symbol,
            trade_date=cur_date,
            security_meta=cur,
            is_risk_warning=risk_warning,
        )
        if (
            prev_close > 0
            and rule.has_price_limit
            and rule.limit_up_pct is not None
            and cur_close / prev_close - 1 >= rule.limit_up_pct - 0.002
        ):
            return RecentLimitResult(hit=True, reliable=not flags, quality_flags=sorted(set(flags)))
    return RecentLimitResult(hit=False, reliable=not flags, quality_flags=sorted(set(flags)))


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


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _optional_bool(value) -> bool | None:
    """三态风险状态：True 明确风险警示，False 明确非风险警示，None 数据源未提供。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _is_st_quote(payload: dict[str, Any]) -> bool:
    text = str(payload.get("name") or payload.get("stock_name") or payload.get("instrument_name") or "")
    return "ST" in text.upper() or "＊ST" in text.upper() or "*ST" in text.upper()


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


__all__ = ["HighPositionFeatureBuilder", "HighPositionFeatures", "RecentLimitResult"]
