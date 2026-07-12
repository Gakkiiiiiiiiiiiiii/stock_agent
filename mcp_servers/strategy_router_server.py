from __future__ import annotations

from engines.strategy.signal_score_engine import score_signal
from engines.strategy.strategy_router import route_strategies
from engines.technical.signal_adjuster import adjust_signal_score


def route_strategy(market_regime: str) -> dict:
    return route_strategies(market_regime)


def adjust_signal(pattern: str, raw_signal_score: float, market_regime: str, theme_strength: float = 50, liquidity_ok: bool = True) -> dict:
    route = route_strategies(market_regime)
    scored = score_signal(pattern=pattern, base_score=raw_signal_score, route=route)
    adjusted = adjust_signal_score(scored, market_regime=market_regime, theme_strength=theme_strength, liquidity_ok=liquidity_ok)
    return {"route": route, "signal": adjusted, "pattern": pattern}

