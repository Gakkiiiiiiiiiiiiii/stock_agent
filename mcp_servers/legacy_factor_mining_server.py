"""Legacy in-process Factor adapter used only while FACTOR_BACKEND=local.

New callers must use ``mcp_servers.factor_mining_server``; keeping this module
separate makes deletion after Factor Cutover a single, auditable operation.
"""
from __future__ import annotations

import logging

from engines.factor.data import load_factor_panel, load_factor_panel_bundle, load_universe
from engines.factor.fitness import evaluate_factor as evaluate_panel
from engines.factor.library import active_factors, load_library, recent_alpha_factors
from engines.factor.miner import FactorMiner
from engines.factor.vm import StackVM
from financial_agent.research_config import get_research_config

logger = logging.getLogger(__name__)


def mine_factors(rounds=None, candidates_per_round=None, universe=None, days=None, eval_window=None, lease_guard=None) -> dict:
    symbols = universe or load_universe()
    config = get_research_config()
    bundle = load_factor_panel_bundle(symbols, days=days or config.data_split.total_days)
    panel, dates, symbols, warning = bundle.panel, bundle.dates, bundle.symbols, bundle.warning
    if not panel:
        return {"accepted": [], "rejected": [], "warning": warning or "行情数据不可用，无法挖掘因子"}
    result = FactorMiner().mine(
        panel, symbols, rounds=rounds, candidates_per_round=candidates_per_round,
        eval_window=eval_window, lease_guard=lease_guard, dates=dates,
        data_version=bundle.metadata.data_version, data_snapshot_id=bundle.metadata.data_snapshot_id,
    )
    result["data_window"] = {"start": dates[0], "end": dates[-1]} if dates else None
    result["eval_window"] = eval_window
    result["universe_size"] = len(symbols)
    if warning:
        result["warning"] = (result.get("warning") or "") + f"; 数据告警: {warning}"
    result["disclaimer"] = "样本内挖掘结果，存在过拟合风险，结论标记为【待核验】，不构成投资建议"
    return result


def list_factor_library(limit: int = 20) -> dict:
    factors = active_factors(load_library(), limit=limit)
    return {"count": len(factors), "factors": factors, "disclaimer": "指标为样本内评估结果，【待核验】，不构成投资建议"}


def list_recent_alpha_candidates(limit: int = 20) -> dict:
    factors = recent_alpha_factors(load_library(), limit=limit)
    return {"count": len(factors), "factors": factors, "disclaimer": "近期有效候选仅供观察与纸面验证，未通过 ACTIVE 准入，不构成投资建议"}


def evaluate_factor(factor_id=None, rpn=None, universe=None) -> dict:
    entry = None
    if factor_id:
        entry = next((f for f in load_library().get("factors", []) if f.get("id") == factor_id), None)
        if entry is None:
            return {"error": f"因子 {factor_id} 不存在"}
        rpn, universe = entry.get("rpn") or [], universe or entry.get("universe") or None
    if not rpn:
        return {"error": "需要提供 factor_id 或 rpn"}
    symbols = universe or load_universe()
    panel, dates, symbols, warning = load_factor_panel(symbols)
    if not panel:
        return {"error": "行情数据不可用", "warning": warning}
    values = StackVM().execute(rpn, panel)
    if values is None:
        return {"error": "因子公式非法或计算失败", "rpn": rpn}
    metrics = evaluate_panel(values, panel["close"], horizon=int(entry.get("horizon") or 5) if entry else 5)
    return {"rpn": rpn, "metrics": metrics, "data_window": {"start": dates[0], "end": dates[-1]} if dates else None, "universe_size": len(symbols), "warning": warning, "disclaimer": "样本内评估结果，【待核验】，不构成投资建议"}


def scan_alpha_factors(symbols=None) -> dict:
    factors = active_factors(load_library())
    if not factors:
        return {"items": [], "warning": "因子库为空，请先运行 mine_factors 挖掘因子"}
    universe = list(symbols) if symbols else load_universe()
    panel, dates, universe, warning = load_factor_panel(universe)
    if not panel:
        return {"items": [], "warning": warning or "行情数据不可用，无法计算 alpha 分数"}
    import numpy as np

    combined, used = None, 0
    for factor in factors:
        values = StackVM().execute(factor.get("rpn") or [], panel)
        if values is None:
            continue
        latest = values[:, -1]
        if (latest >= 0).sum() == 0:
            continue
        combined = latest if combined is None else combined + latest
        used += 1
    if combined is None or used == 0:
        return {"items": [], "warning": "active 因子在当前数据上均无法计算"}
    combined = combined / used
    valid = ~np.isnan(combined)
    valid_idx = np.where(valid)[0]
    order = valid_idx[np.argsort(-combined[valid_idx])]
    return {"date": dates[-1] if dates else None, "factor_count": used, "items": [{"symbol": universe[idx], "alpha_score": round(float(combined[idx]), 4), "alpha_rank": rank, "factor_count": used} for rank, idx in enumerate(order, start=1)], "warning": warning, "disclaimer": "样本内挖掘因子合成分数，【待核验】，不构成投资建议"}
