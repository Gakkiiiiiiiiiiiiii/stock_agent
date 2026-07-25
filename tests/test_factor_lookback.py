import numpy as np
import pytest

from engines.factor.lookback import max_lookback_from_rpn
from engines.factor.vm import StackVM


def test_max_lookback_accumulates_nested_ts_ops():
    assert max_lookback_from_rpn(["close", "ts_mean_20", "ts_delay_5", "cs_rank"]) == 25
    assert max_lookback_from_rpn(["close", "volume", "ts_corr_120", "cs_rank"]) == 120


def test_future_tokens_are_forbidden():
    with pytest.raises(ValueError):
        max_lookback_from_rpn(["close", "lead_5", "cs_rank"])


def test_full_panel_execution_matches_incremental_historical_execution():
    rng = np.random.default_rng(3)
    close = rng.normal(10, 1, size=(12, 90)).cumsum(axis=1) + 100
    panel = {
        "close": close,
        "volume": np.full_like(close, 1_000_000.0),
        "open": close,
        "high": close,
        "low": close,
        "amount": close * 1_000_000.0,
        "turnover": np.full_like(close, 0.01),
        "vwap": close,
        "ret": np.diff(close, prepend=close[:, :1], axis=1) / close,
    }
    rpn = ["close", "ts_mean_60", "cs_rank"]
    full_values = StackVM().execute(rpn, panel)
    for day in (60, 70, 89):
        prefix_panel = {key: value[:, : day + 1] for key, value in panel.items()}
        prefix_values = StackVM().execute(rpn, prefix_panel)
        assert np.allclose(full_values[:, day], prefix_values[:, -1], equal_nan=True)
