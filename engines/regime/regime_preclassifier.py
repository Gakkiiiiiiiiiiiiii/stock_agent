from __future__ import annotations

from financial_agent.config import load_yaml_config

UNKNOWN_REGIME = "UNKNOWN"


def _unknown(missing_fields: list[str]) -> dict:
    return {
        "primary_regime": UNKNOWN_REGIME,
        "secondary_regime": None,
        "confidence": 0.0,
        "missing_fields": missing_fields,
    }


def preclassify_regime(features: dict) -> dict:
    required = ("breadth", "crowding_score", "drawdown_risk", "retreat_score")
    missing = [name for name in required if features.get(name) is None]
    if missing:
        return _unknown(missing)
    thresholds = load_yaml_config("market_regime_thresholds.yaml")["market_regime"]
    retreat_score = features["retreat_score"]
    crowding = features["crowding_score"]
    breadth = features["breadth"]
    drawdown_risk = features["drawdown_risk"]
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
