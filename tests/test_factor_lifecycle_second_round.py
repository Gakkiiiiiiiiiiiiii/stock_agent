import json

import numpy as np
import pytest
import yaml

from engines.factor.lifecycle_service import FactorLifecycleService, InvalidLifecycleTransition
from engines.factor.purged_split import build_purged_windows
from engines.factor.purged_walkforward import run_purged_walkforward


def test_factor_status_transition(tmp_path):
    lib = tmp_path / "lib.yaml"
    lib.write_text(
        yaml.safe_dump({"factors": [{"id": "F001", "rpn": ["ret", "cs_rank"], "status": "OOS_PASS"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.jsonl"
    event = FactorLifecycleService(library_path=lib, audit_path=audit).transition(
        "F001", "PAPER_TRADING", reason="paper precheck passed", actor="tester", research_run_id="R001"
    )
    assert event["from_status"] == "OOS_PASS"
    assert event["to_status"] == "PAPER_TRADING"
    saved = yaml.safe_load(lib.read_text(encoding="utf-8"))
    assert saved["factors"][0]["status"] == "PAPER_TRADING"
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[0])["actor"] == "tester"


def test_invalid_lifecycle_transition_rejected(tmp_path):
    lib = tmp_path / "lib.yaml"
    lib.write_text(yaml.safe_dump({"factors": [{"id": "F001", "status": "DRAFT"}]}), encoding="utf-8")
    with pytest.raises(InvalidLifecycleTransition):
        FactorLifecycleService(library_path=lib, audit_path=tmp_path / "audit.jsonl").transition("F001", "ACTIVE", "skip", "tester")


def test_purged_split_has_gap():
    windows = build_purged_windows(n_days=80, horizon=5, n_windows=2)
    assert windows
    for window in windows:
        assert window.validation[0] - window.train[1] >= 5
        assert window.test[0] - window.validation[1] >= 5


def test_purged_walkforward_positive_predictor_passes_without_mock():
    rng = np.random.default_rng(7)
    n_symbols, n_days = 30, 90
    daily_returns = rng.normal(0, 0.01, size=(n_symbols, n_days))
    signal_component = np.linspace(-0.02, 0.02, n_symbols)[:, None]
    daily_returns[:, 1:] += signal_component
    closes = 100 * np.cumprod(1 + daily_returns, axis=1)
    factor = np.full_like(closes, np.nan)
    factor[:, :-1] = daily_returns[:, 1:]
    result = run_purged_walkforward(factor, closes, horizon=1)
    assert result["passed"] is True
    assert result["window_pass_ratio"] >= 0.6
    assert all(window["metrics"]["coverage"] >= 0.6 for window in result["windows"])


def test_purged_walkforward_random_predictor_fails_without_mock():
    rng = np.random.default_rng(11)
    n_symbols, n_days = 30, 90
    daily_returns = rng.normal(0, 0.01, size=(n_symbols, n_days))
    closes = 100 * np.cumprod(1 + daily_returns, axis=1)
    factor = rng.normal(0, 1, size=(n_symbols, n_days))
    result = run_purged_walkforward(factor, closes, horizon=1)
    assert result["passed"] is False
