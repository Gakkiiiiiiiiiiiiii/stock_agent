from __future__ import annotations
from engines.strategy_factory.models import StrategyDefinition

def generate_hypothesis(name: str, factor_id: str, universe: dict | None = None) -> StrategyDefinition:
    """Constrained generator output: data-only DSL, never arbitrary code."""
    return StrategyDefinition(name=name, universe=universe or {"name": "a_share"}, entry_rules=[{"op": "factor_rank_lte", "factor": factor_id, "value": .05}], exit_rules=[{"op": "holding_days_gte", "value": 5}], ranking=[{"factor": factor_id, "direction": "desc"}])
