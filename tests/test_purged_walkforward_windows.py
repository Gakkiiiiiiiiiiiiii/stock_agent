import numpy as np

from engines.factor.purged_walkforward import _build_eval_windows, run_purged_walkforward


def test_eval_windows_are_balanced_with_embargo():
    windows = _build_eval_windows(10, 70, n_windows=3, embargo=4)
    lengths = [end - start for start, end in windows]
    assert max(lengths) - min(lengths) <= 1
    assert windows[1][0] - windows[0][1] == 4
    assert windows[2][0] - windows[1][1] == 4


def test_walkforward_window_metadata_uses_named_ranges(monkeypatch):
    monkeypatch.setattr(
        "engines.factor.purged_walkforward.evaluate_factor_range",
        lambda *a, **k: {"passed": True, "rank_ic": 0.1, "topk_excess_annual_return": 0.1},
    )
    factor = np.ones((12, 80))
    closes = np.ones((12, 80))
    result = run_purged_walkforward(factor, closes, eval_start=10, eval_end=70, horizon=5, n_windows=3, embargo=4)
    first = result["windows"][0]
    assert "history_range" in first
    assert "embargo_range" in first
    assert "test_range" in first
    assert first["test"] == first["test_range"]
