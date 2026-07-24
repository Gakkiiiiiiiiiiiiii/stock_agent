import numpy as np
import pytest

from engines.backtest.portfolio_backtest import LookaheadViolation, run_topk_backtest


def test_backtest_rejects_lookahead_score_execution():
    scores = np.ones((2, 2))
    prices = np.full((2, 2), 10.0)
    volumes = np.full((2, 2), 1000.0)
    metadata = [
        {"available_at": "2026-01-01T15:05:00", "execution_time": "2026-01-01T09:30:00"},
        {"available_at": "2026-01-02T15:05:00", "execution_time": "2026-01-03T09:30:00"},
    ]
    with pytest.raises(LookaheadViolation):
        run_topk_backtest(scores, prices, prices, prices, prices, volumes, ["600000.SH", "600001.SH"], ["2026-01-01", "2026-01-02"], score_metadata=metadata)
