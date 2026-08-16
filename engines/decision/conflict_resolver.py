from __future__ import annotations


def resolve_conflicts(signal_score: float, portfolio_ok: bool, retrieved_warnings: list[str] | None = None) -> dict:
    warnings = list(retrieved_warnings or [])
    final_action = "observe"
    if signal_score >= 80 and portfolio_ok:
        final_action = "candidate_buy"
    elif signal_score >= 65 and portfolio_ok:
        final_action = "wait_confirmation"
    else:
        warnings.append("信号或组合约束不足")
    return {"final_action": final_action, "warnings": warnings}


# 详细修改方案 §11：Conflict Resolver v2 冲突类型与领域权威。
CONFLICT_TYPES = ("FACT_CONFLICT", "REGIME_CONFLICT", "SIGNAL_CONFLICT", "PORTFOLIO_CONFLICT", "RISK_CONFLICT")

DOMAIN_AUTHORITY = {
    "FACT_CONFLICT": "EVIDENCE_AUTHORITY",
    "REGIME_CONFLICT": "MARKET_SPECIALIST",
    "SIGNAL_CONFLICT": "FACTOR_SPECIALIST",
    "RISK_CONFLICT": "RISK_SPECIALIST",
    "PORTFOLIO_CONFLICT": "PORTFOLIO_SPECIALIST",
}


def resolve_conflicts_v2(conflicts: list[dict]) -> dict:
    """§11：按领域权威解析冲突；Risk 拥有 VETO 权（不是仅“意见之一”）。

    每条 conflict：{type, dimension, options: [{agent, value}], risk_veto?: bool}
    """
    resolutions: list[dict] = []
    vetoed = False
    veto_reasons: list[str] = []
    for conflict in conflicts or []:
        conflict_type = conflict.get("type") or "SIGNAL_CONFLICT"
        authority = DOMAIN_AUTHORITY.get(conflict_type, "SUPERVISOR")
        options = [item for item in conflict.get("options") or [] if isinstance(item, dict)]
        owner = next((item for item in options if item.get("agent") == authority), None)
        resolved_value = (owner or (options[0] if options else {})).get("value")
        if conflict_type == "RISK_CONFLICT" and (conflict.get("risk_veto") or (owner or {}).get("veto")):
            vetoed = True
            veto_reasons.append(str(conflict.get("dimension") or "risk"))
        resolutions.append(
            {
                "dimension": conflict.get("dimension"),
                "type": conflict_type,
                "resolved_by": authority,
                "resolved_value": resolved_value,
            }
        )
    final_action = "veto" if vetoed else "proceed"
    return {
        "final_action": final_action,
        "vetoed": vetoed,
        "veto_reasons": veto_reasons,
        "resolutions": resolutions,
    }


__all__ = ["resolve_conflicts", "resolve_conflicts_v2", "CONFLICT_TYPES", "DOMAIN_AUTHORITY"]

