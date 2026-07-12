from __future__ import annotations


def judge_regime_with_llm_hint(features: dict, research_hint: str | None = None) -> dict:
    result = {
        "regime_probabilities": {
            "crowding_market": round(features.get("crowding_score", 0.0), 4),
            "rotation_market": round(features.get("rotation_score", 0.0), 4),
            "range_market": round(features.get("range_score", 0.0), 4),
            "downtrend_market": round(features.get("drawdown_risk", 0.0), 4),
            "high_position_retreat": round(features.get("retreat_score", 0.0), 4),
        },
        "research_hint": research_hint,
    }
    return result

