from __future__ import annotations
from engines.strategy_factory.models import StrategyStatus

def can_generate_live_trade_intent(strategy) -> bool:
    return strategy.status == StrategyStatus.ACTIVE
