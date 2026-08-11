from __future__ import annotations
import numpy as np
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.factor.statistical_validation import validate_factor_statistics

def evaluate_strategy(compiled: dict, scores: np.ndarray, opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray, symbols, dates, p_values: list[float], trials_returns: np.ndarray) -> dict:
    backtest = run_topk_backtest(scores, opens, highs, lows, closes, volumes, symbols, dates, allow_unsafe_without_metadata=True)
    returns = np.diff(np.asarray(backtest["equity_curve"], dtype=float)) / np.maximum(np.asarray(backtest["equity_curve"], dtype=float)[:-1], 1e-9)
    sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(250)) if len(returns) > 1 and np.std(returns) > 1e-12 else 0.0
    statistics = validate_factor_statistics(p_values, sharpe, len(returns), max(1, trials_returns.shape[0]), trials_returns)
    return {"backtest": backtest, "statistics": statistics, "passed": bool(backtest.get("metrics", {}).get("total_return", 0) is not None and statistics["passed"])}
