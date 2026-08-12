from __future__ import annotations
import numpy as np
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.strategy_factory.statistical_validation import validate_strategy_statistics

def evaluate_strategy(
    compiled: dict, scores: np.ndarray, opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    volumes: np.ndarray, symbols, dates, p_values: list[float], trials_returns: np.ndarray,
    *, score_metadata, security_meta=None, amounts=None, limit_prices=None,
) -> dict:
    """Production strategy evaluation never bypasses timing metadata."""
    backtest = run_topk_backtest(
        scores, opens, highs, lows, closes, volumes, symbols, dates,
        score_metadata=score_metadata, security_meta=security_meta,
        execution_model=compiled.get("execution_model", "NEXT_OPEN"), amounts=amounts, limit_prices=limit_prices,
    )
    returns = np.diff(np.asarray(backtest["equity_curve"], dtype=float)) / np.maximum(np.asarray(backtest["equity_curve"], dtype=float)[:-1], 1e-9)
    sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(250)) if len(returns) > 1 and np.std(returns) > 1e-12 else 0.0
    statistics = validate_strategy_statistics(p_values, sharpe, len(returns), max(1, trials_returns.shape[0]), trials_returns)
    degraded = list(backtest.get("price_limit_quality_flags") or [])
    return {"backtest": backtest, "statistics": statistics, "quality_flags": degraded, "passed": bool(backtest.get("metrics", {}).get("total_return", 0) is not None and statistics["passed"] and not degraded)}
