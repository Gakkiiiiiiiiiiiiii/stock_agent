"""前向模拟盘：每日按因子库合成 alpha 分数组 TopK 组合并记账。

每日流程：T-1 收盘后显式调用 generate_orders() 冻结
signals_T-1.json 和 orders_T.json；T 日 run_daily() 只读取已存在的 orders_T.json 执行 →
按 portfolio_backtest 的执行规则（涨跌停/停牌/T+1/成本）对昨日持仓→今日记账，
维护 portfolio_state.json 并追加 equity.jsonl。

同日幂等：正式模式下 orders 文件冻结后不可覆盖；当日已记账则跳过记账。
行情不可用（如容器内无 QMT 桥接）时优雅告警并返回退出码 0。

CLI：python -m workers.factor_paper_worker [--force] [--state-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from engines.backtest.execution import (
    TradeRuleContext,
    can_buy_with_context,
    can_sell_with_context,
    cost_of,
    is_suspended,
)
from engines.factor.alpha import compose_alpha_scores
from engines.factor.data import (
    FactorPanelBundle,
    FactorPanelMetadata,
    load_factor_panel_bundle,
    load_universe,
)
from engines.factor.library import load_library, paper_trading_factors
from engines.factor.lookback import max_lookback_from_rpn
from engines.factor.miner import FactorMiner
from engines.market.trading_calendar import next_trading_day
from engines.factor.versioning import is_known_version
from financial_agent.research_config import get_research_config
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

STATE_DIR = "storage/runtime/factor_paper"
INITIAL_CASH = 1_000_000.0
TOP_K_RATIO = 0.01   # TopK 为池子的 1%
TOP_K_MIN = 5
DEFAULT_SCORING_PANEL_DAYS = 60
DEFAULT_MINING_PANEL_DAYS = 250
PANEL_DAYS = DEFAULT_SCORING_PANEL_DAYS      # 兼容旧引用：合成分数所需的历史长度
DEFAULT_REMINE_DAYS = 5
_MIN_TRADE_VALUE = 1.0  # 忽略的交易金额下限（元），避免碎单


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_dir(path: str | Path | None = None) -> Path:
    return Path(path) if path else project_root() / STATE_DIR


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _validate_frozen_order(order_payload: dict, execution_date: str) -> str | None:
    signal_date = order_payload.get("signal_date")
    generated_at = order_payload.get("generated_at")
    signal_available_after = order_payload.get("signal_available_after") or f"{signal_date}T15:00:00+08:00"
    must_exist_before = order_payload.get("must_exist_before")
    if order_payload.get("execution_date") != execution_date:
        return "订单文件执行日与当前执行日不一致，跳过执行"
    if not signal_date or not generated_at or not must_exist_before:
        return "订单文件缺少冻结元数据，跳过执行"
    expected_execution_date = next_trading_day(datetime.fromisoformat(signal_date).date()).isoformat()
    if execution_date != expected_execution_date:
        return f"订单执行日不是信号日的下一交易日：expected={expected_execution_date}"
    if _parse_datetime(generated_at) < _parse_datetime(signal_available_after):
        return "订单生成时间早于信号日收盘可用时间，跳过执行"
    if _parse_datetime(generated_at) >= _parse_datetime(must_exist_before):
        return "订单生成时间晚于开盘前冻结截止时间，跳过执行"
    return None


def _valid_price(value: float) -> bool:
    return value is not None and not np.isnan(value) and value > 0


def _remine_due(dates: list[str], state_dir: Path, remine_days: int) -> bool:
    """距上次挖掘满 remine_days 个交易日（按当前面板交易日计数）则到期。"""
    state = _load_json(state_dir / "remine_state.json", {}) or {}
    last = state.get("last_remine_date")
    if not last or last not in dates:
        return True
    return len(dates) - 1 - dates.index(last) >= remine_days


def _scoring_panel_days() -> int:
    return get_research_config().paper_trading.scoring_panel_days


def _mining_panel_days() -> int:
    return get_research_config().paper_trading.mining_panel_days


def _remine_days_default() -> int:
    return get_research_config().paper_trading.remine_days


def _required_scoring_days(library: dict) -> int:
    config = get_research_config().paper_trading
    max_lookback = 1
    for factor in paper_trading_factors(library):
        try:
            max_lookback = max(max_lookback, max_lookback_from_rpn(factor.get("rpn") or []))
        except ValueError:
            continue
    return max(config.scoring_panel_days, max_lookback + config.scoring_buffer_days)


def _default_panel_loader():
    """生产默认 Loader：返回 FactorPanelBundle（携带真实 Data Version / Snapshot ID）。"""
    return load_factor_panel_bundle


def _is_production_panel_metadata(metadata: FactorPanelMetadata) -> bool:
    return (
        metadata.source != "legacy"
        and is_known_version(metadata.data_version)
        and is_known_version(metadata.data_snapshot_id)
    )


def _unpack_panel(result):
    """兼容 FactorPanelBundle 与旧四元组，返回 (panel, dates, symbols, warning, metadata)。"""
    if isinstance(result, FactorPanelBundle):
        return result.panel, result.dates, result.symbols, result.warning, result.metadata
    panel, dates, symbols, warning = result
    metadata = FactorPanelMetadata(
        source="legacy",
        data_version="UNKNOWN",
        data_snapshot_id="UNKNOWN",
        generated_at=_now_iso(),
        start_date=dates[0] if dates else None,
        end_date=dates[-1] if dates else None,
        universe_hash="",
        adjust="",
        period="",
    )
    return panel, dates, symbols, warning, metadata


def _maybe_remine(panel, symbols, dates, state_dir, remine_days, miner_factory,
                  data_version: str | None = None, data_snapshot_id: str | None = None) -> dict:
    """到期则先跑 FactorMiner 再组池；LLM 不可用时返回 warning，不阻塞记账。"""
    if not _remine_due(dates, state_dir, remine_days):
        return {"attempted": False, "run_valid": True, "accepted": 0, "oos_window_count": 0, "warning": None, "failure_code": None}
    # 严格模式：版本/Snapshot 为 UNKNOWN 等占位值时不得进入 Final OOS
    if get_research_config().require_data_version_for_oos:
        if not is_known_version(data_version):
            return {"attempted": True, "run_valid": False, "accepted": 0, "oos_window_count": 0,
                    "warning": "重挖跳过：DATA_VERSION_REQUIRED", "failure_code": "DATA_VERSION_REQUIRED"}
        if not is_known_version(data_snapshot_id):
            return {"attempted": True, "run_valid": False, "accepted": 0, "oos_window_count": 0,
                    "warning": "重挖跳过：DATA_SNAPSHOT_ID_REQUIRED", "failure_code": "DATA_SNAPSHOT_ID_REQUIRED"}
    miner = miner_factory(model_client=None)
    try:
        result = miner.mine(
            panel, symbols, dates=dates,
            data_version=data_version,
            data_snapshot_id=data_snapshot_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "attempted": True,
            "run_valid": False,
            "accepted": 0,
            "oos_window_count": 0,
            "warning": f"重挖失败已跳过：{exc}",
            "failure_code": type(exc).__name__,
        }
    if result.get("warning"):
        return {
            "attempted": True,
            "run_valid": False,
            "accepted": 0,
            "oos_window_count": 0,
            "warning": f"重挖跳过：{result['warning']}",
            "failure_code": "REMINE_WARNING",
        }
    diagnostics = result.get("diagnostics") or {}
    run_valid = bool(diagnostics.get("run_valid", True))
    accepted_count = len(result.get("accepted") or [])
    oos_window_count = int(diagnostics.get("oos_window_count") or 0)
    if not run_valid:
        failure_code = diagnostics.get("run_failure_code") or "REMINE_INVALID"
        return {
            "attempted": True,
            "run_valid": False,
            "accepted": accepted_count,
            "oos_window_count": oos_window_count,
            "warning": f"重挖无效已跳过：{failure_code}",
            "failure_code": failure_code,
        }
    _write_json(state_dir / "remine_state.json", {
        "last_remine_date": dates[-1],
        "remined_at": _now_iso(),
        "accepted": accepted_count,
        "oos_window_count": oos_window_count,
        "run_valid": True,
    })
    return {
        "attempted": True,
        "run_valid": True,
        "accepted": accepted_count,
        "oos_window_count": oos_window_count,
        "warning": None,
        "failure_code": None,
    }


def _panel_until(panel: dict[str, np.ndarray], end_exclusive: int) -> dict[str, np.ndarray]:
    return {key: value[:, :end_exclusive] for key, value in panel.items()}


def _library_hash(library: dict) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(library, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def generate_orders(
    *,
    execution_date: str,
    state_dir: str | Path | None = None,
    library_path: str | None = None,
    panel_loader=None,
    miner_factory=None,
    force: bool = False,
    allow_historical_regeneration: bool = False,
    remine_days: int | None = None,
) -> dict:
    """T-1 收盘后生成并冻结 T 日开盘订单。"""
    state = _state_dir(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    remine_days = remine_days if remine_days is not None else _remine_days_default()
    panel_loader = panel_loader or _default_panel_loader()
    miner_factory = miner_factory or FactorMiner
    symbols = load_universe()
    if not symbols:
        return {"execution_date": execution_date, "warning": "股票池为空（config/factor_universe.yaml 未配置或读取失败）"}
    library = load_library(library_path)
    scoring_days = _required_scoring_days(library)
    panel, dates, symbols, warning, _scoring_meta = _unpack_panel(panel_loader(symbols, scoring_days))
    if not panel:
        return {"execution_date": execution_date, "warning": warning or "行情数据不可用（QMT 桥接不可达），无法生成订单"}
    signal_date = dates[-1]
    signals_path = state / f"signals_{signal_date}.json"
    orders_path = state / f"orders_{execution_date}.json"
    expected_execution_date = next_trading_day(datetime.fromisoformat(signal_date).date()).isoformat()
    if execution_date != expected_execution_date:
        return {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "warning": f"执行日必须是信号日的下一交易日：expected={expected_execution_date}",
        }
    if orders_path.exists() and not allow_historical_regeneration:
        return {"execution_date": execution_date, "signal_date": signal_date, "skipped": True, "orders_file": str(orders_path)}
    if orders_path.exists() and allow_historical_regeneration:
        logger.warning("historical regeneration enabled for frozen order file: %s", orders_path)
    warnings = [warning] if warning else []
    remine_result = {"attempted": False, "run_valid": True, "accepted": 0, "oos_window_count": 0, "warning": None, "failure_code": None}
    if _remine_due(dates, state, remine_days):
        mining_panel, mining_dates, mining_symbols, mining_warning, mining_meta = _unpack_panel(
            panel_loader(symbols, _mining_panel_days())
        )
        if not mining_panel:
            remine_result = {
                "attempted": True,
                "run_valid": False,
                "accepted": 0,
                "oos_window_count": 0,
                "warning": mining_warning or "重挖取数不可用，已跳过",
                "failure_code": "REMINE_DATA_UNAVAILABLE",
            }
        else:
            remine_result = _maybe_remine(
                mining_panel, mining_symbols, mining_dates, state, remine_days, miner_factory,
                data_version=mining_meta.data_version,
                data_snapshot_id=mining_meta.data_snapshot_id,
            )
        if remine_result.get("warning"):
            warnings.append(remine_result["warning"])
    if remine_result.get("accepted"):
        library = load_library(library_path)
        refreshed_days = _required_scoring_days(library)
        if refreshed_days > len(dates):
            panel, dates, symbols, warning, _ = _unpack_panel(panel_loader(symbols, refreshed_days))
            if not panel:
                return {"execution_date": execution_date, "warning": warning or "重挖后评分面板取数不可用，无法生成订单"}
            signal_date = dates[-1]
            signals_path = state / f"signals_{signal_date}.json"
            orders_path = state / f"orders_{execution_date}.json"
    factors = paper_trading_factors(library)
    if len(dates) < _required_scoring_days(library):
        return {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "warning": f"评分面板历史不足：需要至少 {_required_scoring_days(library)} 日，实际 {len(dates)} 日",
        }
    scores, factor_count = compose_alpha_scores(panel, factors)
    if scores is None:
        return {"execution_date": execution_date, "signal_date": signal_date, "warning": "; ".join(warnings + ["因子库为空或全部不可计算，无法生成订单"])}
    top_k = max(TOP_K_MIN, int(len(symbols) * TOP_K_RATIO))
    valid_idx = np.where(~np.isnan(scores))[0]
    order = valid_idx[np.argsort(-scores[valid_idx])]
    picks = [
        {"symbol": symbols[i], "alpha_score": round(float(scores[i]), 4), "rank": rank}
        for rank, i in enumerate(order[:top_k], start=1)
    ]
    generated_at = _now_iso()
    signal_available_after = f"{signal_date}T15:00:00+08:00"
    must_exist_before = f"{execution_date}T09:30:00+08:00"
    if _parse_datetime(generated_at) < _parse_datetime(signal_available_after) and not allow_historical_regeneration:
        return {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "warning": f"订单生成时间早于信号日收盘可用时间：{signal_available_after}",
        }
    if _parse_datetime(generated_at) >= _parse_datetime(must_exist_before) and not allow_historical_regeneration:
        return {
            "execution_date": execution_date,
            "signal_date": signal_date,
            "warning": f"订单生成时间已晚于开盘前冻结截止时间：{must_exist_before}",
        }
    factor_ids = [str(item.get("id")) for item in factors]
    factor_versions = {str(item.get("id")): item.get("version", item.get("updated_at", item.get("discovered_at"))) for item in factors}
    order_payload = {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "generated_at": generated_at,
        "signal_available_after": signal_available_after,
        "must_exist_before": must_exist_before,
        "execution_model": "next_open",
        "mode": "historical_replay" if allow_historical_regeneration else "forward_paper",
        "factor_count": factor_count,
        "factor_ids": factor_ids,
        "factor_versions": factor_versions,
        "library_hash": _library_hash(library),
        "code_commit": os.getenv("CODE_COMMIT", "UNKNOWN"),
        "remine": remine_result,
        "picks": picks,
    }
    signal_payload = {
        "signal_date": signal_date,
        "generated_at": generated_at,
        "feature_window_end": signal_date,
        "top_k": top_k,
        "factor_count": factor_count,
        "factor_ids": factor_ids,
        "library_hash": order_payload["library_hash"],
        "picks": picks,
    }
    _write_json(signals_path, signal_payload)
    _write_json(orders_path, order_payload)
    return {
        "execution_date": execution_date,
        "signal_date": signal_date,
        "skipped": False,
        "signals_file": str(signals_path),
        "orders_file": str(orders_path),
        "top_k": len(picks),
        "factor_count": factor_count,
        "remine": remine_result,
        "warning": "; ".join(w for w in warnings if w) or None,
    }


def _sell(positions: dict, symbol: str, shares: float, trade_date: str) -> float:
    """卖出指定份额（仅 buy_date < 当日的批次可卖，T+1），返回实际卖出份额。"""
    sellable = [lot for lot in positions.get(symbol) or [] if lot["buy_date"] < trade_date]
    remaining = min(shares, sum(lot["shares"] for lot in sellable))
    taken = remaining
    kept = []
    for lot in positions.get(symbol) or []:
        if lot["buy_date"] < trade_date and remaining > 1e-12:
            take = min(lot["shares"], remaining)
            lot = {**lot, "shares": lot["shares"] - take}
            remaining -= take
        if lot["shares"] > 1e-9:
            kept.append(lot)
    if kept:
        positions[symbol] = kept
    else:
        positions.pop(symbol, None)
    return taken


def _fetch_quotes_batched(symbols: list[str], batch_size: int = 200) -> tuple[dict, int]:
    """分批拉取 Quote，返回 (quotes, failed_chunk_count)；单批失败保留其他成功批次。"""
    if not symbols:
        return {}, 0
    try:
        from engines.market.data_provider import batched, get_market_data_provider, to_qmt_symbol

        provider = get_market_data_provider()
        bridge = getattr(provider, "bridge", None)
        if bridge is None:
            return {}, 0
        quotes: dict = {}
        failed_chunks = 0
        for chunk in batched([to_qmt_symbol(s) for s in symbols], batch_size):
            try:
                quotes.update(bridge.get_quotes(list(chunk)) or {})
            except Exception as exc:  # noqa: BLE001
                failed_chunks += 1
                logger.warning("Quote batch failed: size=%s error=%s", len(chunk), exc)
        return quotes, failed_chunks
    except Exception as exc:  # noqa: BLE001
        logger.warning("T 日 Quote 获取失败，按规则比例回退执行: %s", exc)
        return {}, 0


def _default_quote_loader(symbols: list[str], batch_size: int = 200) -> tuple[dict, int]:
    """T 日执行时从行情桥接拉取实时 Quote；不可用返回空 dict（回退到规则比例）。"""
    return _fetch_quotes_batched(symbols, batch_size)


def _quote_for_symbol(quotes: dict, symbol: str) -> dict:
    """QMT Quote 字典的键可能是 QMT 代码或原代码，两种都尝试。"""
    if not quotes:
        return {}
    if symbol in quotes:
        return quotes[symbol] or {}
    from engines.market.data_provider import to_qmt_symbol

    return quotes.get(to_qmt_symbol(symbol)) or {}


def _advance_portfolio(panel, dates, symbols, picks, state_dir: Path, trade_date: str, quotes: dict | None = None) -> dict:
    """对"昨日持仓→今日"记账并落库状态，返回记账摘要。"""
    state_path = state_dir / "portfolio_state.json"
    state = _load_json(state_path, None)
    if state and state.get("last_date") == trade_date:
        return {"advanced": False, "message": f"{trade_date} 已记账，跳过"}
    if not state:
        state = {"cash": INITIAL_CASH, "positions": {}, "equity": INITIAL_CASH,
                 "benchmark": INITIAL_CASH, "last_prices": {}, "last_date": None}

    t = len(dates) - 1
    idx = {s: i for i, s in enumerate(symbols)}
    opens, closes, volumes = panel["open"], panel["close"], panel["volume"]
    prev_closes = closes[:, t - 1] if t > 0 else np.full(len(symbols), np.nan)
    trade_day = date.fromisoformat(trade_date)

    cash = float(state["cash"])
    positions = {s: [dict(lot) for lot in lots] for s, lots in (state.get("positions") or {}).items()}
    last_prices = dict(state.get("last_prices") or {})
    traded = 0.0
    quotes = quotes or {}

    def tradable(i: int) -> bool:
        return not is_suspended(volumes[i, t]) and _valid_price(opens[i, t])

    def _rule_context(i: int) -> TradeRuleContext:
        quote = _quote_for_symbol(quotes, symbols[i])

        def _limit_price(key: str) -> float | None:
            value = quote.get(key)
            if value in (None, ""):
                return None
            try:
                price = float(value)
            except (TypeError, ValueError):
                return None
            return price if math.isfinite(price) and price > 0 else None

        return TradeRuleContext(
            symbol=symbols[i],
            trade_date=trade_day,
            prev_close=float(prev_closes[i]),
            open_price=float(opens[i, t]),
            quote=quote,
            upper_limit_price=_limit_price("upper_limit_price"),
            lower_limit_price=_limit_price("lower_limit_price"),
        )

    def do_sell(symbol: str, i: int, shares: float) -> None:
        nonlocal cash, traded
        sold = _sell(positions, symbol, shares, trade_date)
        if sold <= 0:
            return
        value = sold * float(opens[i, t])
        cash += value - cost_of(value, "sell")
        traded += value

    def do_buy(symbol: str, i: int, value: float) -> None:
        nonlocal cash, traded
        value = min(value, cash)
        if value <= _MIN_TRADE_VALUE:
            return
        # 成本随金额变化（佣金有最低 5 元），迭代两次逼近“金额+成本≤现金”
        for _ in range(2):
            cost = cost_of(value, "buy")
            if value + cost <= cash + 1e-9:
                break
            value = max(cash - cost, 0.0)
        if value <= _MIN_TRADE_VALUE:
            return
        cash -= value + cost_of(value, "buy")
        traded += value
        positions.setdefault(symbol, []).append(
            {"shares": value / float(opens[i, t]), "buy_date": trade_date})

    # 第一步：卖出已调出目标池的持仓（跌停/停牌/T+1 受限则保留）
    for symbol in list(positions):
        if symbol in picks:
            continue
        i = idx.get(symbol)
        if i is None or not tradable(i):
            continue
        if _valid_price(prev_closes[i]) and not can_sell_with_context(_rule_context(i)):
            continue
        do_sell(symbol, i, sum(lot["shares"] for lot in positions.get(symbol) or []))

    def shares_of(symbol: str) -> float:
        return sum(lot["shares"] for lot in positions.get(symbol) or [])

    def open_price(i: int) -> float:
        return float(opens[i, t]) if _valid_price(opens[i, t]) else float(last_prices.get(symbols[i]) or 0.0)

    # 第二步：按开盘价估算组合总市值，目标池等权
    equity_open = cash
    for symbol in positions:
        i = idx.get(symbol)
        price = open_price(i) if i is not None else float(last_prices.get(symbol) or 0.0)
        equity_open += shares_of(symbol) * price
    target_value = equity_open / len(picks) if picks else 0.0

    # 第三步：先减持超配的目标股（受 T+1 与跌停约束）
    for symbol in picks:
        i = idx.get(symbol)
        if i is None:
            continue
        excess = shares_of(symbol) * open_price(i) - target_value
        if excess <= _MIN_TRADE_VALUE or not tradable(i):
            continue
        if _valid_price(prev_closes[i]) and not can_sell_with_context(_rule_context(i)):
            continue
        do_sell(symbol, i, excess / float(opens[i, t]))

    # 第四步：买入/加仓低配的目标股（涨停不可买）
    for symbol in picks:
        i = idx.get(symbol)
        if i is None or not tradable(i):
            continue
        gap = target_value - shares_of(symbol) * open_price(i)
        if gap <= _MIN_TRADE_VALUE:
            continue
        if _valid_price(prev_closes[i]) and not can_buy_with_context(_rule_context(i)):
            continue
        do_buy(symbol, i, gap)

    # 估值：收盘价优先，缺失回退到最近已知价格
    for i, symbol in enumerate(symbols):
        if _valid_price(closes[i, t]):
            last_prices[symbol] = float(closes[i, t])
    equity = cash + sum(shares_of(s) * float(last_prices.get(s) or 0.0) for s in positions)

    # 基准：全池等权日收益净值
    benchmark = float(state.get("benchmark") or INITIAL_CASH)
    if t > 0:
        rets = [closes[i, t] / prev_closes[i] - 1 for i in range(len(symbols))
                if _valid_price(prev_closes[i]) and _valid_price(closes[i, t])]
        if rets:
            benchmark *= 1 + float(np.mean(rets))

    turnover = traded / equity if equity > 0 else 0.0
    state.update({
        "cash": cash, "positions": positions, "equity": equity,
        "benchmark": benchmark, "last_prices": last_prices,
        "last_date": trade_date, "updated_at": _now_iso(),
    })
    _write_json(state_path, state)
    with (state_dir / "equity.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "date": trade_date, "equity": round(equity, 2),
            "benchmark": round(benchmark, 2), "turnover": round(turnover, 4),
        }, ensure_ascii=False) + "\n")
    return {"advanced": True, "equity": round(equity, 2),
            "benchmark": round(benchmark, 2), "turnover": round(turnover, 4)}


def run_daily(
    *,
    state_dir: str | Path | None = None,
    library_path: str | None = None,
    panel_loader=None,
    miner_factory=None,
    quote_loader=None,
    force: bool = False,
    remine_days: int | None = None,
    generate_next_orders: bool = False,
    next_execution_date: str | None = None,
) -> dict:
    """执行单日组池 + 记账，返回摘要 dict（QMT 不可用时 warning 优雅返回）。"""
    state = _state_dir(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    remine_days = remine_days if remine_days is not None else _remine_days_default()
    panel_loader = panel_loader or _default_panel_loader()
    miner_factory = miner_factory or FactorMiner
    quote_loader = quote_loader or _default_quote_loader

    symbols = load_universe()
    if not symbols:
        return {"date": None, "warning": "股票池为空（config/factor_universe.yaml 未配置或读取失败）"}
    library = load_library(library_path)
    panel, dates, symbols, warning, _ = _unpack_panel(panel_loader(symbols, _required_scoring_days(library)))
    if not panel:
        # QMT 桥接不可用（如容器内）等场景：告警并优雅退出
        return {"date": None, "warning": warning or "行情数据不可用（QMT 桥接不可达），今日跳过"}
    if len(dates) < 2:
        return {"date": dates[-1] if dates else None, "warning": "前向模拟盘至少需要 T-1 与 T 两个交易日，今日跳过"}

    execution_date = dates[-1]
    orders_path = state / f"orders_{execution_date}.json"
    if not orders_path.exists():
        generated = None
        if generate_next_orders and next_execution_date:
            generated = generate_orders(
                execution_date=next_execution_date,
                state_dir=state,
                library_path=library_path,
                panel_loader=panel_loader,
                miner_factory=miner_factory,
                force=force,
                allow_historical_regeneration=False,
                remine_days=remine_days,
            )
        return {
            "date": execution_date,
            "execution_date": execution_date,
            "skipped": True,
            "warning": f"{execution_date} 开盘前订单不存在，跳过执行",
            "orders_file": str(orders_path),
            "generated_next_orders": generated,
        }
    order_payload = _load_json(orders_path, {})
    validation_error = _validate_frozen_order(order_payload, execution_date)
    if validation_error:
        return {
            "date": execution_date,
            "execution_date": execution_date,
            "skipped": True,
            "warning": validation_error,
            "orders_file": str(orders_path),
        }
    signal_date = order_payload.get("signal_date")
    picks = order_payload.get("picks") or []
    factor_count = int(order_payload.get("factor_count") or 0)

    pick_symbols = [p["symbol"] for p in picks]
    portfolio_state = _load_json(state / "portfolio_state.json", {}) or {}
    # 只请求当前 Picks + 当前持仓，避免大股票池整体超时
    required_symbols = sorted(set(pick_symbols) | set((portfolio_state.get("positions") or {}).keys()))
    try:
        quote_result = quote_loader(required_symbols)
    except Exception as exc:  # noqa: BLE001
        logger.warning("T 日 Quote 获取失败，按规则比例回退执行: %s", exc)
        quote_result = {}
    if isinstance(quote_result, tuple):
        quotes, quote_failed_chunks = quote_result
    else:
        quotes, quote_failed_chunks = quote_result or {}, 0
    quote_received = sum(1 for s in required_symbols if _quote_for_symbol(quotes, s))
    quote_coverage = quote_received / len(required_symbols) if required_symbols else None
    paper_config = get_research_config().paper_trading
    quote_quality_flags: list[str] = []
    if quote_coverage is not None and quote_coverage < paper_config.min_quote_coverage:
        quote_quality_flags.append("PAPER_QUOTE_COVERAGE_LOW")
        if paper_config.fail_on_low_quote_coverage:
            return {
                "date": execution_date,
                "execution_date": execution_date,
                "skipped": True,
                "warning": f"PAPER_QUOTE_COVERAGE_LOW:{quote_coverage:.4f}",
                "quote_requested_count": len(required_symbols),
                "quote_received_count": quote_received,
                "quote_coverage": quote_coverage,
                "quote_failed_chunk_count": quote_failed_chunks,
                "quote_quality_flags": quote_quality_flags,
            }
    bookkeeping = _advance_portfolio(
        panel, dates, symbols, pick_symbols, state, execution_date, quotes=quotes
    )
    generated_next = None
    if generate_next_orders and next_execution_date:
        generated_next = generate_orders(
            execution_date=next_execution_date,
            state_dir=state,
            library_path=library_path,
            panel_loader=panel_loader,
            miner_factory=miner_factory,
            force=force,
            allow_historical_regeneration=False,
            remine_days=remine_days,
        )
    return {
        "date": execution_date,
        "signal_date": signal_date,
        "execution_date": execution_date,
        "skipped": not bookkeeping.get("advanced"),
        "orders_file": str(orders_path),
        "top_k": len(picks),
        "factor_count": factor_count,
        "bookkeeping": bookkeeping,
        "generated_next_orders": generated_next,
        "quote_requested_count": len(required_symbols),
        "quote_received_count": quote_received,
        "quote_coverage": quote_coverage,
        "quote_failed_chunk_count": quote_failed_chunks,
        "price_limit_rule_fallback_count": len(required_symbols) - quote_received,
        "quote_quality_flags": quote_quality_flags,
        "warning": warning,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="因子前向模拟盘：每日组池 + 记账")
    parser.add_argument("--force", action="store_true", help="当日已记账时仍尝试执行；不会覆盖已冻结订单")
    parser.add_argument("--state-dir", default=None, help="状态目录（默认 storage/runtime/factor_paper）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run_daily(force=args.force, state_dir=args.state_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
