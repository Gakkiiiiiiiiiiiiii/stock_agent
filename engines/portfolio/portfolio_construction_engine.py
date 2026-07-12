from __future__ import annotations

from engines.portfolio.theme_exposure import summarize_theme_exposure


def construct_portfolio_actions(candidates: list[dict], positions: list[dict], risk_limits: dict) -> dict:
    total_position_before = sum(float(item.get("weight", 0)) for item in positions)
    theme_before = summarize_theme_exposure(positions)
    actions = []
    current_total = total_position_before
    for candidate in sorted(candidates, key=lambda item: item.get("final_signal_score", 0), reverse=True):
        if current_total >= risk_limits.get("max_total_position", 1.0):
            break
        suggested_weight = min(candidate.get("suggested_weight", 0.05), risk_limits.get("max_single_stock", 0.1))
        actions.append(
            {
                "symbol": candidate["symbol"],
                "theme": candidate.get("theme"),
                "portfolio_action": "add_watch" if candidate.get("final_signal_score", 0) < 75 else "add_position",
                "suggested_weight": round(suggested_weight, 4),
            }
        )
        current_total += suggested_weight
    return {
        "actions": actions,
        "total_position_before": round(total_position_before, 4),
        "total_position_after": round(current_total, 4),
        "theme_exposure_before": theme_before,
    }

