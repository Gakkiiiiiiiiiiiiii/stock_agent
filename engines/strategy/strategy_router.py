from __future__ import annotations

from financial_agent.config import load_yaml_config


def route_strategies(market_regime: str) -> dict:
    config = load_yaml_config("strategy_router.yaml")["regimes"]
    regime = config.get(market_regime, config["rotation_market"])
    return {
        "market_regime": market_regime,
        "regime_name": regime["name"],
        "preferred_strategies": regime["preferred_strategies"],
        "risk_limits": {
            "max_total_position": regime["max_total_position"],
            "max_single_stock": regime["max_single_stock"],
            "max_single_theme": regime["max_single_theme"],
        },
        "rule": regime["rule"],
    }

