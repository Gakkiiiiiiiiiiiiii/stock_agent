from __future__ import annotations

from financial_agent.config import load_yaml_config


def preclassify_regime(features: dict) -> dict:
    thresholds = load_yaml_config("market_regime_thresholds.yaml")["market_regime"]
    retreat_score = features.get("retreat_score", 0.0)
    crowding = features.get("crowding_score", 0.0)
    breadth = features.get("breadth", 0.5)
    drawdown_risk = features.get("drawdown_risk", 0.0)
    if retreat_score >= thresholds["high_position_retreat"]["min_retreat_score"]:
        primary = "high_position_retreat"
    elif drawdown_risk >= thresholds["downtrend"]["min_drawdown_risk"] and breadth <= thresholds["downtrend"]["max_breadth"]:
        primary = "downtrend_market"
    elif crowding >= 0.72:
        primary = "crowding_market"
    elif thresholds["range"]["min_breadth"] <= breadth <= thresholds["range"]["max_breadth"]:
        primary = "range_market"
    else:
        primary = "rotation_market"
    return {
        "primary_regime": primary,
        "secondary_regime": "rotation_market" if primary != "rotation_market" else "range_market",
        "confidence": round(max(crowding, 1 - drawdown_risk, 0.55), 4),
    }

