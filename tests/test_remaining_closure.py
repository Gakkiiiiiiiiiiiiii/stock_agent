from __future__ import annotations

import numpy as np

from engines.backtest.execution_model import ExecutionModel
from engines.backtest.portfolio_backtest import run_topk_backtest
from engines.factor.promotion_gate import evaluate_promotion_gate


def _gate(**overrides):
    payload = {
        "walkforward": {"passed": True, "window_pass_ratio": .8},
        "statistics": {"passed": True},
        "diagnostics": {"ic_decay": {1: .1, 5: .05}},
        "exposure": {"liquidity_exposure": .1},
        "capacity": {"daily_notional_capacity_proxy": 100.0},
        "data_snapshot_id": "snapshot_v1",
    }
    payload.update(overrides)
    return evaluate_promotion_gate(**payload)


def test_promotion_gate_rejects_capacity_and_is_deterministic():
    rejected = _gate(capacity={"daily_notional_capacity_proxy": 0.0})
    assert not rejected.passed and "CAPACITY_FAILED" in rejected.reject_reasons
    first = _gate().model_dump()
    assert first == _gate().model_dump()
    assert first["metrics"]["promotion_gate_version"] == "promotion_gate_v2"


def test_limit_price_not_touched_never_fills_and_vwap_marks_approximation():
    scores = np.array([[1.0, 1.0]])
    opens = closes = np.array([[10.0, 10.0]])
    highs, lows = np.array([[9.0, 9.0]]), np.array([[8.0, 8.0]])
    volumes = np.array([[1000.0, 1000.0]])
    common = dict(rebalance_interval=1, top_k=1, allow_unsafe_without_metadata=True)
    limit = run_topk_backtest(scores, opens, highs, lows, closes, volumes, ["600000.SH"], ["2026-01-01", "2026-01-02"], execution_model=ExecutionModel.LIMIT_PRICE, limit_prices=np.array([[10.0, 10.0]]), **common)
    assert limit["trades"] == []
    vwap = run_topk_backtest(scores, opens, highs, lows, closes, volumes, ["600000.SH"], ["2026-01-01", "2026-01-02"], execution_model=ExecutionModel.VWAP, **common)
    assert "VWAP_APPROXIMATED" in vwap["price_limit_quality_flags"]
