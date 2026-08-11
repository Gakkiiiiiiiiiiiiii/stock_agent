"""旧版组合构建接口（向后兼容适配器）。

底层已切换为 engines/portfolio/pipeline.py 的 v2 流水线；
这里保留 construct_portfolio_actions 的输入/输出契约：
final_signal_score → opportunity_score，risk_limits 映射为自定义 regime 预算与个股上限。
"""
from __future__ import annotations

import copy

from engines.portfolio.pipeline import load_portfolio_rules, run_portfolio_pipeline
from engines.portfolio.theme_exposure import summarize_theme_exposure

_ADAPTER_REGIME = "custom_risk_limits"


def _adapter_rules(risk_limits: dict) -> dict:
    rules = copy.deepcopy(load_portfolio_rules())
    rules.setdefault("regime_risk_budget", {})[_ADAPTER_REGIME] = {
        "max_total_position": float(risk_limits.get("max_total_position", 1.0))
    }
    if risk_limits.get("max_single_stock") is not None:
        rules.setdefault("exposure", {})["max_single_stock"] = float(risk_limits["max_single_stock"])
    return rules


def construct_portfolio_actions(candidates: list[dict], positions: list[dict], risk_limits: dict) -> dict:
    total_position_before = sum(float(item.get("weight", 0)) for item in positions)
    theme_before = summarize_theme_exposure(positions)
    normalized_candidates = [
        {
            **candidate,
            "opportunity_score": candidate.get(
                "opportunity_score", candidate.get("final_signal_score", 0)
            ),
        }
        for candidate in candidates
    ]
    result = run_portfolio_pipeline(
        candidates=normalized_candidates,
        positions=positions,
        context={"regime": _ADAPTER_REGIME},
        rules=_adapter_rules(risk_limits or {}),
    )
    candidate_symbols = {candidate["symbol"] for candidate in candidates}
    actions = []
    for action in result["actions"]:
        if action["symbol"] not in candidate_symbols:
            continue
        if action["action"] in {"watch", "hold"} and action["current_weight"] > 0:
            continue
        actions.append(
            {
                "symbol": action["symbol"],
                "theme": action.get("theme"),
                "portfolio_action": "add_watch" if action["action"] == "watch" else "add_position",
                "suggested_weight": action["target_weight"],
            }
        )
    return {
        "actions": actions,
        "total_position_before": round(total_position_before, 4),
        "total_position_after": result["summary"]["total_target_weight"],
        "theme_exposure_before": theme_before,
    }
