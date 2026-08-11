from __future__ import annotations
from engines.backtest.execution_model import ExecutionModel

def compile_strategy(definition) -> dict:
    execution = definition.execution
    if execution.get("signal_at") != "close" or execution.get("fill_at") not in {"next_open", "next_close", "vwap", "limit_price"}:
        raise ValueError("INVALID_EXECUTION_TIMELINE")
    model = {"next_open": ExecutionModel.NEXT_OPEN, "next_close": ExecutionModel.NEXT_CLOSE, "vwap": ExecutionModel.VWAP, "limit_price": ExecutionModel.LIMIT_PRICE}[execution["fill_at"]]
    if not definition.ranking: raise ValueError("STRATEGY_REQUIRES_RANKING")
    return {"strategy_id": definition.strategy_id, "factor_dsl": definition.ranking, "technical_rules": definition.entry_rules + definition.exit_rules, "portfolio_rules": definition.portfolio, "execution_model": model.value, "no_lookahead": True}
