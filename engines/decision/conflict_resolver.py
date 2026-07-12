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

