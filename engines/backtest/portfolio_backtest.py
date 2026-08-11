"""TopK 等权组合回测。

每个调仓日按 scores 面板选取得分最高的 top_k 只股票等权配置，
以当日开盘价为目标执行调仓（受涨跌停/停牌/T+1 约束），非调仓日持有不动。
面板形状均为 (n_symbols, n_days)，NaN 表示缺失；scores 为 NaN 表示当日不入选。
交易可执行性判断优先级：实际涨跌停价 > 每日 Limit Rate/风险状态/上市阶段 > 本地版本化规则。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime

import numpy as np
from pydantic import BaseModel

from engines.backtest.execution import (
    PositionBook,
    TradeRuleContext,
    can_buy_with_context,
    can_sell_with_context,
    cost_of,
    is_suspended,
)
from engines.backtest.events import SignalEvent
from engines.backtest.execution_model import ExecutionModel, resolve_fill, schedule_order
from engines.market.price_limit_rules import MAIN_BOARD_ST_10_EFFECTIVE_DATE, board_of
from engines.market.price_limit_metadata import (
    OptionalPriceStatus,
    extract_limit_down_rate,
    extract_limit_up_rate,
    extract_listing_stage,
    extract_lower_limit_price,
    extract_risk_warning,
    extract_upper_limit_price,
    inspect_price_limit_meta,
    parse_optional_price,
)
from engines.factor.versioning import is_known_version
from financial_agent.research_config import get_research_config

# 调仓时忽略的价值偏差阈值（元），避免无意义的碎单
_MIN_TRADE_VALUE = 1.0


class LookaheadViolation(ValueError):
    code = "LOOKAHEAD_VIOLATION"


class ScoreMetadata(BaseModel):
    feature_time: datetime
    available_at: datetime
    executable_from: datetime
    data_snapshot_id: str
    algorithm_version: str
    data_version: str | None = None


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
        shares = _round_buy_shares(symbols[idx], value / price)
        value = shares * price
        if shares <= 0 or value <= _MIN_TRADE_VALUE:
            return 0.0
        cost = cost_of(value, "buy")
        if value + cost > self.cash + 1e-9:
            shares = _round_buy_shares(symbols[idx], max(self.cash - cost, 0.0) / price)
            value = shares * price
            cost = cost_of(value, "buy")
            if shares <= 0 or value + cost > self.cash + 1e-9:
                return 0.0
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
    rebalance_mask: Sequence[bool] | None = None,
    top_k: int | None = None,
    initial_cash: float = 1_000_000.0,
    score_metadata: Sequence[dict] | None = None,
    allow_unsafe_without_metadata: bool = False,
    security_meta: Sequence[Sequence[dict | None]] | None = None,
    upper_limit_prices=None,
    lower_limit_prices=None,
    execution_model: ExecutionModel | str = ExecutionModel.NEXT_OPEN,
    limit_prices=None,
    amounts=None,
) -> dict:
    """运行 TopK 等权组合回测，返回净值/基准/交易/换手/持仓日志。

    security_meta / upper_limit_prices / lower_limit_prices 为可选的
    (n_symbols, n_days) 每日交易规则元数据面板：提供后历史 ST、IPO 首日、
    非对称 Limit Rate、实际涨跌停价与 Tick Size 才会真实进入执行。
    """
    scores = np.asarray(scores, dtype=float)
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    model = ExecutionModel(execution_model)
    n_symbols, n_days = scores.shape
    if rebalance_mask is not None and len(rebalance_mask) != n_days:
        raise ValueError("REBALANCE_MASK_LENGTH_MISMATCH")
    normalized_rebalance_mask = [bool(value) for value in rebalance_mask] if rebalance_mask is not None else None
    _validate_optional_panel_shape(security_meta, (n_symbols, n_days), "security_meta")
    _validate_optional_panel_shape(upper_limit_prices, (n_symbols, n_days), "upper_limit_prices")
    _validate_optional_panel_shape(lower_limit_prices, (n_symbols, n_days), "lower_limit_prices")
    _validate_optional_panel_shape(limit_prices, (n_symbols, n_days), "limit_prices")
    _validate_optional_panel_shape(amounts, (n_symbols, n_days), "amounts")
    meta_cells = np.asarray(security_meta, dtype=object) if security_meta is not None else None
    upper_cells = np.asarray(upper_limit_prices, dtype=object) if upper_limit_prices is not None else None
    lower_cells = np.asarray(lower_limit_prices, dtype=object) if lower_limit_prices is not None else None
    limit_cells = np.asarray(limit_prices, dtype=object) if limit_prices is not None else None
    amount_values = np.asarray(amounts, dtype=float) if amounts is not None else None
    execution_prices = _execution_price_panel(model, opens, highs, lows, closes, volumes, limit_cells, amount_values)
    if top_k is None:
        # 默认池子的 1%，下限 5 只
        top_k = max(5, int(n_symbols * 0.01))
    top_k = max(1, min(top_k, n_symbols))
    rebalance_interval = max(1, int(rebalance_interval))
    fail_on_ambiguous = get_research_config().backtest.fail_on_ambiguous_price_limit
    fail_on_invalid_meta = get_research_config().backtest.fail_on_invalid_price_limit_meta
    pl_stats = {
        "unique_cells": 0,
        "meta_covered_cells": 0,
        "buy_meta_covered_cells": 0,
        "sell_meta_covered_cells": 0,
        "actual_limit_price_cells": 0,
        "rate_based_cells": 0,
        "fallback_cells": 0,
        "buy_fallback_cells": 0,
        "sell_fallback_cells": 0,
        "any_side_fallback_cells": 0,
        "both_sides_fallback_cells": 0,
        "buy_ambiguous_cells": 0,
        "sell_ambiguous_cells": 0,
        "invalid_upper_limit_price_count": 0,
        "invalid_lower_limit_price_count": 0,
        "conflicts": set(),
    }

    state = _PortfolioState(initial_cash)
    last_close = np.full(n_symbols, np.nan)  # 各标的最近有效收盘价（停牌股估值用）

    equity_curve: list[float] = []
    benchmark_curve: list[float] = []
    trades: list[dict] = []
    daily_turnover: list[float] = []
    holdings_log: list[dict] = []
    price_limit_daily_stats: list[dict] = []
    execution_events: list[dict] = []

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
        should_rebalance = (
            normalized_rebalance_mask[t]
            if normalized_rebalance_mask is not None
            else t % rebalance_interval == 0
        )
        if should_rebalance:
            _check_score_time_contract(t, dates, score_metadata, allow_unsafe_without_metadata)
            before_stats = _snapshot_pl_stats(pl_stats)
            trade_start = len(trades)
            traded_value = _rebalance_day(
                t, state, scores, opens, execution_prices, highs, lows, closes, volumes, last_close,
                symbols, dates, trades, top_k, n_symbols,
                meta_cells=meta_cells,
                upper_cells=upper_cells,
                lower_cells=lower_cells,
                pl_stats=pl_stats,
                fail_on_ambiguous=fail_on_ambiguous,
                fail_on_invalid_meta=fail_on_invalid_meta,
                limit_order=model == ExecutionModel.LIMIT_PRICE,
            )
            _append_execution_events(execution_events, trades[trade_start:], dates, t, model)
            price_limit_daily_stats.append(_daily_pl_stats(dates[t], before_stats, pl_stats))

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

    quality_flags: list[str] = []
    if model == ExecutionModel.VWAP and amount_values is None:
        quality_flags.append("VWAP_APPROXIMATED")
    if pl_stats["buy_ambiguous_cells"] or pl_stats["sell_ambiguous_cells"]:
        quality_flags.append("BACKTEST_HISTORICAL_RISK_STATUS_UNAVAILABLE")
    quality_flags.extend(sorted(pl_stats["conflicts"]))
    return {
        "equity_curve": equity_curve,
        "benchmark_curve": benchmark_curve,
        "trades": trades,
        "daily_turnover": daily_turnover,
        "holdings_log": holdings_log,
        "dates": list(dates),
        "price_limit_meta_coverage": (
            round(pl_stats["meta_covered_cells"] / pl_stats["unique_cells"], 6)
            if pl_stats["unique_cells"] else None
        ),
        "price_limit_buy_meta_coverage": (
            round(pl_stats["buy_meta_covered_cells"] / pl_stats["unique_cells"], 6)
            if pl_stats["unique_cells"] else None
        ),
        "price_limit_sell_meta_coverage": (
            round(pl_stats["sell_meta_covered_cells"] / pl_stats["unique_cells"], 6)
            if pl_stats["unique_cells"] else None
        ),
        "actual_limit_price_coverage": (
            round(pl_stats["actual_limit_price_cells"] / pl_stats["unique_cells"], 6)
            if pl_stats["unique_cells"] else None
        ),
        "price_limit_fallback_count": pl_stats["fallback_cells"],
        "price_limit_buy_fallback_count": pl_stats["buy_fallback_cells"],
        "price_limit_sell_fallback_count": pl_stats["sell_fallback_cells"],
        "price_limit_any_side_fallback_count": pl_stats["any_side_fallback_cells"],
        "price_limit_both_sides_fallback_count": pl_stats["both_sides_fallback_cells"],
        "price_limit_rule_fallback_semantics": "both_sides_missing",
        "price_limit_buy_ambiguous_count": pl_stats["buy_ambiguous_cells"],
        "price_limit_sell_ambiguous_count": pl_stats["sell_ambiguous_cells"],
        "invalid_upper_limit_price_count": pl_stats["invalid_upper_limit_price_count"],
        "invalid_lower_limit_price_count": pl_stats["invalid_lower_limit_price_count"],
        "price_limit_quality_flags": quality_flags,
        "price_limit_daily_stats": price_limit_daily_stats,
        "execution_model": model.value,
        "execution_events": execution_events,
        "diagnostics": {
            "rebalance_mode": "explicit_mask" if normalized_rebalance_mask is not None else "interval",
            "rebalance_interval": None if normalized_rebalance_mask is not None else rebalance_interval,
        },
    }


def _execution_price_panel(
    model: ExecutionModel,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    limit_prices: np.ndarray | None,
    amounts: np.ndarray | None,
) -> np.ndarray:
    if model == ExecutionModel.NEXT_OPEN:
        return opens
    if model == ExecutionModel.NEXT_CLOSE:
        return closes
    if model == ExecutionModel.VWAP:
        # Intraday amount is optional in the existing panel contract.  Typical
        # price is an explicit, deterministic fallback rather than a mock.
        if amounts is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                vwap = amounts / volumes
            return np.where(np.isfinite(vwap) & (vwap > 0), vwap, (highs + lows + closes) / 3.0)
        return (highs + lows + closes) / 3.0
    if limit_prices is None:
        raise ValueError("LIMIT_PRICE requires limit_prices panel")
    return np.asarray(limit_prices, dtype=float)


def _event_datetime(value, *, close: bool = False) -> datetime:
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError(f"invalid backtest date for event: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.replace(hour=15 if close else 9, minute=30, second=0, microsecond=0)


def _append_execution_events(events: list[dict], trades: list[dict], dates: Sequence, t: int, model: ExecutionModel) -> None:
    """Persist a replayable event chain for every actual simulated fill."""
    signal_time = _event_datetime(dates[t - 1] if t else dates[t], close=True)
    fill_time = _event_datetime(dates[t], close=model == ExecutionModel.NEXT_CLOSE)
    if fill_time <= signal_time:
        # A first-bar execution has no prior available signal; it remains in the
        # trade ledger but intentionally has no executable event chain.
        return
    for trade in trades:
        signal = SignalEvent(symbol=str(trade["symbol"]), side=str(trade["side"]).upper(), signal_time=signal_time)
        order = schedule_order(signal, fill_time, model, float(trade["shares"]), limit_price=float(trade["price"]) if model == ExecutionModel.LIMIT_PRICE else None)
        fill = resolve_fill(order, signal, {
            "open": trade["price"], "close": trade["price"], "high": trade["price"], "low": trade["price"],
            "vwap": trade["price"], "volume": trade["shares"], "amount": float(trade["shares"]) * float(trade["price"]),
        }, fill_time)
        events.extend([signal.model_dump(mode="json"), order.model_dump(mode="json"), fill.model_dump(mode="json")])


def _snapshot_pl_stats(stats: dict) -> dict:
    return {
        key: (set(value) if isinstance(value, set) else value)
        for key, value in (stats or {}).items()
    }


def _daily_pl_stats(date_value, before: dict, after: dict) -> dict:
    fields = (
        "unique_cells",
        "meta_covered_cells",
        "buy_meta_covered_cells",
        "sell_meta_covered_cells",
        "buy_fallback_cells",
        "sell_fallback_cells",
        "any_side_fallback_cells",
        "both_sides_fallback_cells",
        "invalid_upper_limit_price_count",
        "invalid_lower_limit_price_count",
    )
    row = {"date": date_value, "quality_flags": sorted((after.get("conflicts") or set()) - (before.get("conflicts") or set()))}
    for field in fields:
        row[field] = int((after.get(field) or 0) - (before.get(field) or 0))
    return row


def _validate_optional_panel_shape(value, expected_shape: tuple, field_name: str) -> None:
    if value is None:
        return
    array = np.asarray(value, dtype=object)
    if array.shape != expected_shape:
        raise ValueError(f"{field_name} shape mismatch: {array.shape} != {expected_shape}")


def _meta_to_dict(value) -> dict:
    """security_meta cell 归一化：支持 dict / dataclass / pydantic 模型。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    raise TypeError(f"SECURITY_META_CELL_INVALID:{type(value).__name__}")


def _optional_price(value) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _parse_limit_price_cell(
    value,
    field_name: str,
    *,
    treat_nan_as_missing: bool,
    fail_on_invalid: bool,
    pl_stats: dict | None,
) -> float | None:
    if treat_nan_as_missing:
        try:
            if value is not None and isinstance(float(value), float) and math.isnan(float(value)):
                return None
        except (TypeError, ValueError):
            pass
    parsed = parse_optional_price(value, field_name)
    if parsed.status is OptionalPriceStatus.MISSING:
        return None
    if parsed.status is OptionalPriceStatus.INVALID:
        code = "INVALID_UPPER_LIMIT_PRICE" if field_name == "upper_limit_price" else "INVALID_LOWER_LIMIT_PRICE"
        if pl_stats is not None:
            pl_stats["conflicts"].add(code)
            count_key = "invalid_upper_limit_price_count" if field_name == "upper_limit_price" else "invalid_lower_limit_price_count"
            pl_stats[count_key] += 1
        if fail_on_invalid:
            raise ValueError(parsed.error or f"PRICE_LIMIT_PRICE_INVALID:{field_name}")
        return None
    return parsed.value


def _check_score_time_contract(t: int, dates: Sequence, score_metadata: Sequence[dict] | None, allow_unsafe_without_metadata: bool) -> None:
    if not score_metadata:
        if allow_unsafe_without_metadata:
            return
        raise LookaheadViolation("score_metadata is mandatory")
    if t >= len(score_metadata) or not score_metadata[t]:
        if allow_unsafe_without_metadata:
            return
        raise LookaheadViolation(f"score metadata missing at {dates[t]}")
    meta_raw = dict(score_metadata[t])
    if "executable_from" not in meta_raw and "execution_time" in meta_raw:
        meta_raw["executable_from"] = meta_raw["execution_time"]
    if "data_snapshot_id" not in meta_raw:
        meta_raw["data_snapshot_id"] = "UNKNOWN"
    if "algorithm_version" not in meta_raw:
        meta_raw["algorithm_version"] = "UNKNOWN"
    try:
        meta = ScoreMetadata(**meta_raw)
    except Exception as exc:  # noqa: BLE001
        raise LookaheadViolation(f"invalid score metadata at {dates[t]}: {exc}") from exc
    if meta.feature_time > meta.available_at:
        raise LookaheadViolation(f"LOOKAHEAD_VIOLATION at {dates[t]}: feature_time after available_at")
    if meta.available_at >= meta.executable_from:
        raise LookaheadViolation(
            f"LOOKAHEAD_VIOLATION at {dates[t]}: available_at={meta.available_at.isoformat()} "
            f"executable_from={meta.executable_from.isoformat()}"
        )
    if not is_known_version(meta.data_snapshot_id):
        raise LookaheadViolation(f"LOOKAHEAD_VIOLATION at {dates[t]}: data_snapshot_id is required")
    if meta.data_version is not None and not is_known_version(meta.data_version):
        raise LookaheadViolation(f"LOOKAHEAD_VIOLATION at {dates[t]}: data_version is required")
    execution_date = _parse_date(dates[t])
    if execution_date is not None and meta.executable_from.date() != execution_date:
        raise LookaheadViolation(
            f"LOOKAHEAD_VIOLATION at {dates[t]}: executable_from date {meta.executable_from.date()} "
            f"does not match execution date {execution_date}"
        )


def _legacy_check_score_time_contract(t: int, dates: Sequence, score_metadata: Sequence[dict] | None) -> None:
    if not score_metadata:
        return
    if t >= len(score_metadata) or not score_metadata[t]:
        return
    meta = score_metadata[t]
    available_at = _parse_dt(meta.get("available_at"))
    execution_time = _parse_dt(meta.get("execution_time") or meta.get("execution_from"))
    if available_at is None or execution_time is None:
        raise LookaheadViolation(f"score metadata missing available_at/execution_time at {dates[t]}")
    if available_at >= execution_time:
        raise LookaheadViolation(
            f"LOOKAHEAD_VIOLATION at {dates[t]}: available_at={available_at.isoformat()} "
            f"execution_time={execution_time.isoformat()}"
        )


def _parse_date(value) -> datetime.date | None:
    parsed = _parse_dt(value)
    return parsed.date() if parsed is not None else None


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _round_buy_shares(symbol: str, shares: float) -> float:
    lot_size = 100
    if str(symbol).split(".")[0].startswith(("8", "4", "920")):
        lot_size = 100
    return float(int(shares // lot_size) * lot_size)


def _rebalance_day(
    t: int,
    state: _PortfolioState,
    scores: np.ndarray,
    opens: np.ndarray,
    execution_prices: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    last_close: np.ndarray,
    symbols: Sequence[str],
    dates: Sequence,
    trades: list,
    top_k: int,
    n_symbols: int,
    meta_cells: np.ndarray | None = None,
    upper_cells: np.ndarray | None = None,
    lower_cells: np.ndarray | None = None,
    pl_stats: dict | None = None,
    fail_on_ambiguous: bool = False,
    fail_on_invalid_meta: bool = True,
    limit_order: bool = False,
) -> float:
    """在调仓日 t 以开盘价执行调仓，返回当日成交总额（双边合计）。"""
    traded = 0.0

    def tradable(idx: int) -> bool:
        """当日可交易：未停牌且开盘价有效。"""
        if is_suspended(volumes[idx, t]) or not _valid_price(execution_prices[idx, t]):
            return False
        if np.any(np.isnan(highs[idx, t])) or np.any(np.isnan(lows[idx, t])):
            return True
        # LIMIT_PRICE represents an order resting at the submitted price, not a
        # guaranteed fill.  It must trade inside that day's observed range.
        return not (limit_order and not (lows[idx, t] <= execution_prices[idx, t] <= highs[idx, t]))

    # 候选池：分数非 NaN 且当日可交易，取得分最高的 top_k 只
    eligible = [
        i for i in range(n_symbols)
        if not np.isnan(scores[i, t]) and tradable(i)
    ]
    ranked = sorted(eligible, key=lambda i: scores[i, t], reverse=True)[:top_k]
    target = set(ranked)

    prev_closes = closes[:, t - 1] if t > 0 else None
    trade_date = _parse_date(dates[t])

    context_cache: dict[int, TradeRuleContext] = {}
    cell_ambiguity: dict[int, dict[str, bool]] = {}

    def _rule_context(idx: int) -> TradeRuleContext:
        # 同一调仓日同一标的只构建一次：Coverage 按唯一 Symbol-Date Cell 统计
        if idx in context_cache:
            return context_cache[idx]
        meta = _meta_to_dict(meta_cells[idx, t]) if meta_cells is not None else {}
        panel_upper = (
            _parse_limit_price_cell(
                upper_cells[idx, t],
                "upper_limit_price",
                treat_nan_as_missing=True,
                fail_on_invalid=fail_on_invalid_meta,
                pl_stats=pl_stats,
            )
            if upper_cells is not None
            else None
        )
        panel_lower = (
            _parse_limit_price_cell(
                lower_cells[idx, t],
                "lower_limit_price",
                treat_nan_as_missing=True,
                fail_on_invalid=fail_on_invalid_meta,
                pl_stats=pl_stats,
            )
            if lower_cells is not None
            else None
        )
        meta_upper = _parse_limit_price_cell(
            extract_upper_limit_price(meta),
            "upper_limit_price",
            treat_nan_as_missing=False,
            fail_on_invalid=fail_on_invalid_meta,
            pl_stats=pl_stats,
        )
        meta_lower = _parse_limit_price_cell(
            extract_lower_limit_price(meta),
            "lower_limit_price",
            treat_nan_as_missing=False,
            fail_on_invalid=fail_on_invalid_meta,
            pl_stats=pl_stats,
        )
        # 执行优先级：独立 Limit Price 面板 > Meta 中的 Limit Price > Limit Rate > 状态/阶段 > 本地规则
        upper = panel_upper if panel_upper is not None else meta_upper
        lower = panel_lower if panel_lower is not None else meta_lower
        if pl_stats is not None:
            capabilities = inspect_price_limit_meta(meta, upper_limit_price=upper, lower_limit_price=lower)
            pl_stats["unique_cells"] += 1
            if capabilities.buy_rule_meta or capabilities.sell_rule_meta:
                pl_stats["meta_covered_cells"] += 1
            else:
                pl_stats["fallback_cells"] += 1
            if not capabilities.buy_rule_meta:
                pl_stats["buy_fallback_cells"] += 1
            if not capabilities.sell_rule_meta:
                pl_stats["sell_fallback_cells"] += 1
            if not capabilities.buy_rule_meta or not capabilities.sell_rule_meta:
                pl_stats["any_side_fallback_cells"] += 1
            if not capabilities.buy_rule_meta and not capabilities.sell_rule_meta:
                pl_stats["both_sides_fallback_cells"] += 1
            if capabilities.buy_rule_meta:
                pl_stats["buy_meta_covered_cells"] += 1
            if capabilities.sell_rule_meta:
                pl_stats["sell_meta_covered_cells"] += 1
            if capabilities.actual_upper or capabilities.actual_lower:
                pl_stats["actual_limit_price_cells"] += 1
            if capabilities.rate_upper or capabilities.rate_lower:
                pl_stats["rate_based_cells"] += 1
            if panel_upper is not None and meta_upper is not None and abs(panel_upper - meta_upper) > 1e-9:
                pl_stats["conflicts"].add("UPPER_LIMIT_PRICE_CONFLICT")
            if panel_lower is not None and meta_lower is not None and abs(panel_lower - meta_lower) > 1e-9:
                pl_stats["conflicts"].add("LOWER_LIMIT_PRICE_CONFLICT")
            # 旧制度主板风险状态缺失时，买入/卖出规则的模糊性分开判断：
            # 有实际跌停价（或跌停率）时卖出规则精确，不因涨停信息缺失被阻断
            base_ambiguous = (
                trade_date is not None
                and trade_date < MAIN_BOARD_ST_10_EFFECTIVE_DATE
                and board_of(symbols[idx]) == "主板"
                and extract_risk_warning(meta) is None
                and extract_listing_stage(meta) is None
            )
            buy_ambiguous = base_ambiguous and upper is None and extract_limit_up_rate(meta) is None
            sell_ambiguous = base_ambiguous and lower is None and extract_limit_down_rate(meta) is None
            if buy_ambiguous:
                pl_stats["buy_ambiguous_cells"] += 1
            if sell_ambiguous:
                pl_stats["sell_ambiguous_cells"] += 1
            cell_ambiguity[idx] = {"buy": buy_ambiguous, "sell": sell_ambiguous}
        context = TradeRuleContext(
            symbol=symbols[idx],
            trade_date=trade_date,
            prev_close=prev_closes[idx],
            open_price=execution_prices[idx, t],
            quote=meta,
            upper_limit_price=upper,
            lower_limit_price=lower,
        )
        context_cache[idx] = context
        return context

    def sell_allowed(idx: int) -> bool:
        # 首日无前收价，不做涨跌停约束
        if prev_closes is None or not _valid_price(prev_closes[idx]):
            return True
        context = _rule_context(idx)
        if fail_on_ambiguous and cell_ambiguity.get(idx, {}).get("sell"):
            raise ValueError(f"BACKTEST_SELL_RULE_AMBIGUOUS:{symbols[idx]}@{trade_date}")
        return can_sell_with_context(context)

    def buy_allowed(idx: int) -> bool:
        if prev_closes is None or not _valid_price(prev_closes[idx]):
            return True
        context = _rule_context(idx)
        if fail_on_ambiguous and cell_ambiguity.get(idx, {}).get("buy"):
            raise ValueError(f"BACKTEST_BUY_RULE_AMBIGUOUS:{symbols[idx]}@{trade_date}")
        return can_buy_with_context(context)

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
        traded += state.sell(idx, t, state.shares_of(idx), execution_prices[idx, t], symbols, dates, trades)

    if not target:
        return traded

    # 第二步：按开盘价估算组合总市值，目标池内等权
    equity_open = state.cash
    for idx in state.books:
        shares = state.shares_of(idx)
        if shares <= 1e-9:
            continue
        price = execution_prices[idx, t] if _valid_price(execution_prices[idx, t]) else last_close[idx]
        if _valid_price(price):
            equity_open += shares * price
    target_value = equity_open * 0.99 / len(target)

    def current_value(idx: int) -> float:
        price = execution_prices[idx, t] if _valid_price(execution_prices[idx, t]) else last_close[idx]
        return state.shares_of(idx) * price if _valid_price(price) else 0.0

    # 第三步：先减持超配的目标股（受 T+1 与跌停约束）
    for idx in ranked:
        excess = current_value(idx) - target_value
        if excess <= _MIN_TRADE_VALUE:
            continue
        if not tradable(idx) or not sell_allowed(idx):
            continue
        traded += state.sell(idx, t, excess / execution_prices[idx, t], execution_prices[idx, t], symbols, dates, trades)

    # 第四步：买入/加仓低配的目标股（涨停不可买）
    for idx in ranked:
        gap = target_value - current_value(idx)
        if gap <= _MIN_TRADE_VALUE:
            continue
        if not buy_allowed(idx):
            continue
        traded += state.buy(idx, t, gap, execution_prices[idx, t], symbols, dates, trades)

    return traded
