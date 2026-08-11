from __future__ import annotations

import numpy as np

from engines.factor.diagnostics import compute_factor_autocorrelation, compute_ic_decay, compute_turnover
from engines.factor.exposure import compute_factor_exposures
from engines.factor.statistical_validation import benjamini_hochberg, deflated_sharpe_ratio, validate_factor_statistics


def test_benjamini_hochberg_controls_fdr():
    result = benjamini_hochberg([.001, .02, .9], .05)
    assert result["rejected"] == [True, True, False]


def test_deflated_sharpe_penalizes_multiple_trials():
    assert deflated_sharpe_ratio(1.0, 100, 100) < deflated_sharpe_ratio(1.0, 100, 1)


def test_factor_diagnostics_and_exposure():
    panel = np.array([[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6]], dtype=float)
    closes = panel + 10
    assert compute_factor_autocorrelation(panel) is not None
    assert compute_ic_decay(panel, closes, 2)[1] is not None
    assert compute_turnover(panel, 1) >= 0
    result = compute_factor_exposures(panel[:, -1], np.array([1, 2, 3]), np.array([3, 2, 1]), ["A", "A", "B"])
    assert set(result["industry_exposure"]) == {"A", "B"}


def test_statistical_gate_rejects_unstable_factor():
    result = validate_factor_statistics([.8, .9], .1, 10, 100, np.array([[.2, -.2, .2, -.2], [-.2, .2, -.2, .2]]))
    assert not result["passed"]
