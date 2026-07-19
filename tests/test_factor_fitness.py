import numpy as np

from engines.factor.fitness import evaluate_factor


def _make_closes(n_symbols: int = 20, n_days: int = 80, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.02, size=(n_symbols, n_days))
    return 100 * np.cumprod(1 + returns, axis=1)


def test_positively_correlated_factor_has_positive_rank_ic():
    closes = _make_closes()
    horizon = 5
    fwd = np.full_like(closes, np.nan)
    fwd[:, :-horizon] = closes[:, horizon:] / closes[:, :-horizon] - 1.0
    metrics = evaluate_factor(fwd, closes, horizon=horizon)  # 因子=未来收益本身
    assert metrics["rank_ic"] > 0.9
    assert metrics["passed"] is True


def test_negatively_correlated_factor_has_negative_rank_ic():
    closes = _make_closes()
    horizon = 5
    fwd = np.full_like(closes, np.nan)
    fwd[:, :-horizon] = closes[:, horizon:] / closes[:, :-horizon] - 1.0
    metrics = evaluate_factor(-fwd, closes, horizon=horizon)
    assert metrics["rank_ic"] < -0.9
    assert metrics["passed"] is False


def test_random_factor_has_near_zero_rank_ic():
    rng = np.random.default_rng(42)
    closes = _make_closes()
    noise = rng.normal(size=closes.shape)
    metrics = evaluate_factor(noise, closes, horizon=5)
    assert abs(metrics["rank_ic"]) < 0.1


def test_low_coverage_factor_is_rejected():
    closes = _make_closes(n_days=80)
    factor = np.full_like(closes, np.nan)
    factor[:, :10] = 1.0  # 仅前 10 天有值，coverage 远低于 0.6
    metrics = evaluate_factor(factor, closes, horizon=5)
    assert metrics["fitness"] == float("-inf")
    assert metrics["passed"] is False


def test_eval_window_restricts_evaluation_to_recent_days():
    # 因子只在最后 20 天与未来收益正相关，早期为噪声
    rng = np.random.default_rng(11)
    closes = _make_closes(n_days=80)
    horizon = 5
    fwd = np.full_like(closes, np.nan)
    fwd[:, :-horizon] = closes[:, horizon:] / closes[:, :-horizon] - 1.0
    factor = rng.normal(size=closes.shape)
    factor[:, -20:] = fwd[:, -20:]  # 近期完美预测
    full = evaluate_factor(factor, closes, horizon=horizon)
    windowed = evaluate_factor(factor, closes, horizon=horizon, eval_window=20)
    assert windowed["rank_ic"] > 0.9
    assert windowed["coverage"] >= 0.6
    assert full["rank_ic"] < windowed["rank_ic"]
