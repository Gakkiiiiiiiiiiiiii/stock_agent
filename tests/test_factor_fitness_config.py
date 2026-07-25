import numpy as np

from engines.factor.fitness import evaluate_factor_range
from financial_agent.research_config import EvaluationConfig


def test_evaluate_factor_uses_configured_thresholds():
    n_symbols, n_days = 12, 30
    factor = np.tile(np.arange(n_symbols, dtype=float)[:, None], (1, n_days))
    drift = np.linspace(0.001, 0.012, n_symbols)[:, None]
    closes = 100 * np.cumprod(1 + np.tile(drift, (1, n_days)), axis=1)
    loose = EvaluationConfig(min_coverage=0.6, min_rank_ic=0.01, min_icir=0.1, min_topk_excess_annual_return=-1.0)
    strict = EvaluationConfig(min_coverage=0.6, min_rank_ic=1.1, min_icir=0.1, min_topk_excess_annual_return=-1.0)
    assert evaluate_factor_range(factor, closes, 0, 20, horizon=5, thresholds=loose)["passed"] is True
    assert evaluate_factor_range(factor, closes, 0, 20, horizon=5, thresholds=strict)["passed"] is False
