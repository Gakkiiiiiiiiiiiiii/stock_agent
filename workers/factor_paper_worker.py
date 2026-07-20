"""前向模拟盘：每日按因子库合成 alpha 分数组 TopK 组合并记账。

每日流程：加载股票池 → load_factor_panel(days=60) → 重挖开关（距上次挖掘满
FACTOR_PAPER_REMINE_DAYS 个交易日先跑 FactorMiner，LLM 不可用只告警不阻塞）→
当前因子库等权合成 alpha_score → TopK（池子 1%）→ 落库 positions_YYYY-MM-DD.json
→ 按 portfolio_backtest 的执行规则（涨跌停/停牌/T+1/成本）对昨日持仓→今日记账，
维护 portfolio_state.json 并追加 equity.jsonl。

同日幂等：positions 文件已存在则跳过组池；当日已记账则跳过记账；--force 可重跑组池。
行情不可用（如容器内无 QMT 桥接）时优雅告警并返回退出码 0。

CLI：python -m workers.factor_paper_worker [--force] [--state-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from engines.backtest.execution import can_buy, can_sell, cost_of, is_suspended
from engines.factor.alpha import compose_alpha_scores
from engines.factor.data import load_factor_panel, load_universe
from engines.factor.library import active_factors, load_library
from engines.factor.miner import FactorMiner
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

STATE_DIR = "storage/runtime/factor_paper"
INITIAL_CASH = 1_000_000.0
TOP_K_RATIO = 0.01   # TopK 为池子的 1%
TOP_K_MIN = 5
PANEL_DAYS = 60      # 合成分数所需的历史长度
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


def _valid_price(value: float) -> bool:
    return value is not None and not np.isnan(value) and value > 0


def _remine_due(dates: list[str], state_dir: Path, remine_days: int) -> bool:
    """距上次挖掘满 remine_days 个交易日（按当前面板交易日计数）则到期。"""
    state = _load_json(state_dir / "remine_state.json", {}) or {}
    last = state.get("last_remine_date")
    if not last or last not in dates:
        return True
    return len(dates) - 1 - dates.index(last) >= remine_days


def _maybe_remine(panel, symbols, dates, state_dir, remine_days, miner_factory) -> str | None:
    """到期则先跑 FactorMiner 再组池；LLM 不可用时返回 warning，不阻塞记账。"""
    if not _remine_due(dates, state_dir, remine_days):
        return None
    miner = miner_factory(model_client=None)
    try:
        result = miner.mine(panel, symbols)
    except Exception as exc:  # noqa: BLE001
        return f"重挖失败已跳过：{exc}"
    if result.get("warning"):
        return f"重挖跳过：{result['warning']}"
    _write_json(state_dir / "remine_state.json", {
        "last_remine_date": dates[-1],
        "remined_at": _now_iso(),
        "accepted": len(result.get("accepted") or []),
    })
    return None


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


def _advance_portfolio(panel, dates, symbols, picks, state_dir: Path, trade_date: str) -> dict:
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

    cash = float(state["cash"])
    positions = {s: [dict(lot) for lot in lots] for s, lots in (state.get("positions") or {}).items()}
    last_prices = dict(state.get("last_prices") or {})
    traded = 0.0

    def tradable(i: int) -> bool:
        return not is_suspended(volumes[i, t]) and _valid_price(opens[i, t])

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
        if _valid_price(prev_closes[i]) and not can_sell(opens[i, t], prev_closes[i], symbol):
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
        if _valid_price(prev_closes[i]) and not can_sell(opens[i, t], prev_closes[i], symbol):
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
        if _valid_price(prev_closes[i]) and not can_buy(opens[i, t], prev_closes[i], symbol):
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
    force: bool = False,
    remine_days: int | None = None,
) -> dict:
    """执行单日组池 + 记账，返回摘要 dict（QMT 不可用时 warning 优雅返回）。"""
    state = _state_dir(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    remine_days = remine_days if remine_days is not None else int(
        os.getenv("FACTOR_PAPER_REMINE_DAYS", DEFAULT_REMINE_DAYS))
    panel_loader = panel_loader or load_factor_panel
    miner_factory = miner_factory or FactorMiner

    symbols = load_universe()
    if not symbols:
        return {"date": None, "warning": "股票池为空（config/factor_universe.yaml 未配置或读取失败）"}
    panel, dates, symbols, warning = panel_loader(symbols, PANEL_DAYS)
    if not panel:
        # QMT 桥接不可用（如容器内）等场景：告警并优雅退出
        return {"date": None, "warning": warning or "行情数据不可用（QMT 桥接不可达），今日跳过"}

    trade_date = dates[-1]
    positions_path = state / f"positions_{trade_date}.json"
    if positions_path.exists() and not force:
        return {
            "date": trade_date, "skipped": True,
            "message": f"{trade_date} 持仓已生成，跳过组池（--force 可重跑）",
            "positions_file": str(positions_path),
        }

    warnings = [warning] if warning else []
    remine_warning = _maybe_remine(panel, symbols, dates, state, remine_days, miner_factory)
    if remine_warning:
        warnings.append(remine_warning)

    factors = active_factors(load_library(library_path))
    scores, factor_count = compose_alpha_scores(panel, factors)
    if scores is None:
        return {"date": trade_date,
                "warning": "; ".join(warnings + ["因子库为空或全部不可计算，无法组池"])}

    top_k = max(TOP_K_MIN, int(len(symbols) * TOP_K_RATIO))
    valid_idx = np.where(~np.isnan(scores))[0]
    order = valid_idx[np.argsort(-scores[valid_idx])]
    picks = [
        {"symbol": symbols[i], "alpha_score": round(float(scores[i]), 4), "rank": rank}
        for rank, i in enumerate(order[:top_k], start=1)
    ]
    payload = {"date": trade_date, "generated_at": _now_iso(), "top_k": top_k, "picks": picks}
    _write_json(positions_path, payload)

    bookkeeping = _advance_portfolio(panel, dates, symbols, [p["symbol"] for p in picks], state, trade_date)
    return {
        "date": trade_date, "skipped": False,
        "positions_file": str(positions_path),
        "top_k": top_k, "factor_count": factor_count,
        "bookkeeping": bookkeeping,
        "warning": "; ".join(w for w in warnings if w) or None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="因子前向模拟盘：每日组池 + 记账")
    parser.add_argument("--force", action="store_true", help="当日已生成持仓时强制重跑组池")
    parser.add_argument("--state-dir", default=None, help="状态目录（默认 storage/runtime/factor_paper）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run_daily(force=args.force, state_dir=args.state_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
