from __future__ import annotations

import logging

from engines.factor.data import load_factor_panel, load_universe
from engines.factor.fitness import evaluate_factor as evaluate_panel
from engines.factor.library import active_factors, load_library
from engines.factor.miner import FactorMiner
from engines.factor.vm import StackVM

logger = logging.getLogger(__name__)


def mine_factors(
    rounds: int | None = None,
    candidates_per_round: int | None = None,
    universe: list[str] | None = None,
    days: int | None = None,
    eval_window: int | None = None,
) -> dict:
    """LLM 自动挖掘横截面选股因子，达标者写入因子库（样本内评估，结论待核验）。

    days 控制取数窗口（默认 250 个交易日）；eval_window 指定时只在最近
    eval_window 个交易日上评估，因子值仍用全量历史计算。
    """
    symbols = universe or load_universe()
    panel, dates, symbols, warning = load_factor_panel(symbols, days=days or 250)
    if not panel:
        return {"accepted": [], "rejected": [], "warning": warning or "行情数据不可用，无法挖掘因子"}
    miner = FactorMiner()
    result = miner.mine(
        panel, symbols, rounds=rounds, candidates_per_round=candidates_per_round,
        eval_window=eval_window,
    )
    result["data_window"] = {"start": dates[0], "end": dates[-1]} if dates else None
    result["eval_window"] = eval_window
    result["universe_size"] = len(symbols)
    if warning:
        result["warning"] = (result.get("warning") or "") + f"; 数据告警: {warning}"
    result["disclaimer"] = "样本内挖掘结果，存在过拟合风险，结论标记为【待核验】，不构成投资建议"
    return result


def list_factor_library(limit: int = 20) -> dict:
    """列出因子库中的 active 因子及样本内指标。"""
    factors = active_factors(load_library(), limit=limit)
    return {
        "count": len(factors),
        "factors": factors,
        "disclaimer": "指标为样本内评估结果，【待核验】，不构成投资建议",
    }


def evaluate_factor(
    factor_id: str | None = None,
    rpn: list[str] | None = None,
    universe: list[str] | None = None,
) -> dict:
    """对指定因子（按库内 id 或直接给 RPN）在当前股票池上重新评估。"""
    if factor_id:
        library = load_library()
        entry = next((f for f in library.get("factors", []) if f.get("id") == factor_id), None)
        if entry is None:
            return {"error": f"因子 {factor_id} 不存在"}
        rpn = entry.get("rpn") or []
        universe = universe or entry.get("universe") or None
    if not rpn:
        return {"error": "需要提供 factor_id 或 rpn"}

    symbols = universe or load_universe()
    panel, dates, symbols, warning = load_factor_panel(symbols)
    if not panel:
        return {"error": "行情数据不可用", "warning": warning}
    values = StackVM().execute(rpn, panel)
    if values is None:
        return {"error": "因子公式非法或计算失败", "rpn": rpn}
    horizon = 5
    if factor_id and entry:
        horizon = int(entry.get("horizon") or 5)
    metrics = evaluate_panel(values, panel["close"], horizon=horizon)
    return {
        "rpn": rpn,
        "metrics": metrics,
        "data_window": {"start": dates[0], "end": dates[-1]} if dates else None,
        "universe_size": len(symbols),
        "warning": warning,
        "disclaimer": "样本内评估结果，【待核验】，不构成投资建议",
    }


def scan_alpha_factors(symbols: list[str] | None = None) -> dict:
    """用库内 active 因子等权合成 alpha 分数，对标的做截面排名。

    因子库为空或行情不可用时按惯例返回 warning，不抛异常。
    """
    factors = active_factors(load_library())
    if not factors:
        return {"items": [], "warning": "因子库为空，请先运行 mine_factors 挖掘因子"}

    requested = list(symbols) if symbols else None
    universe = requested or load_universe()
    panel, dates, universe, warning = load_factor_panel(universe)
    if not panel:
        return {"items": [], "warning": warning or "行情数据不可用，无法计算 alpha 分数"}

    vm = StackVM()
    # 各因子最新一日的截面分位等权合成
    combined = None
    used = 0
    for factor in factors:
        values = vm.execute(factor.get("rpn") or [], panel)
        if values is None:
            continue
        latest = values[:, -1]
        if (latest >= 0).sum() == 0:
            continue
        combined = latest if combined is None else combined + latest
        used += 1
    if combined is None or used == 0:
        return {"items": [], "warning": "active 因子在当前数据上均无法计算"}

    import numpy as np

    combined = combined / used
    valid = ~np.isnan(combined)
    # 只在有效标的上排名：NaN 直接剔除，避免挤占名次
    valid_idx = np.where(valid)[0]
    order = valid_idx[np.argsort(-combined[valid_idx])]
    items = []
    for rank, idx in enumerate(order, start=1):
        items.append({
            "symbol": universe[idx],
            "alpha_score": round(float(combined[idx]), 4),
            "alpha_rank": rank,
            "factor_count": used,
        })
    return {
        "date": dates[-1] if dates else None,
        "factor_count": used,
        "items": items,
        "warning": warning,
        "disclaimer": "样本内挖掘因子合成分数，【待核验】，不构成投资建议",
    }
