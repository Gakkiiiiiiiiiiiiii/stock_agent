"""Deterministic diagnostics used by the factor statistical gate."""
from __future__ import annotations

import numpy as np


def compute_factor_autocorrelation(panel: np.ndarray, lag: int = 1) -> float | None:
    values = np.asarray(panel, dtype=float)
    if values.ndim != 2 or lag <= 0 or values.shape[1] <= lag:
        return None
    correlations = []
    for index in range(lag, values.shape[1]):
        left, right = values[:, index - lag], values[:, index]
        valid = np.isfinite(left) & np.isfinite(right)
        if valid.sum() >= 3 and np.std(left[valid]) > 1e-12 and np.std(right[valid]) > 1e-12:
            correlations.append(float(np.corrcoef(left[valid], right[valid])[0, 1]))
    return round(float(np.mean(correlations)), 8) if correlations else None


def compute_ic_decay(factor_panel: np.ndarray, closes: np.ndarray, max_horizon: int = 20) -> dict[int, float | None]:
    factor, prices = np.asarray(factor_panel, dtype=float), np.asarray(closes, dtype=float)
    output: dict[int, float | None] = {}
    for horizon in range(1, max(1, int(max_horizon)) + 1):
        values = []
        for day in range(0, prices.shape[1] - horizon):
            score = factor[:, day]
            with np.errstate(divide="ignore", invalid="ignore"):
                forward = prices[:, day + horizon] / prices[:, day] - 1
            valid = np.isfinite(score) & np.isfinite(forward)
            if valid.sum() >= 3 and np.std(score[valid]) > 1e-12 and np.std(forward[valid]) > 1e-12:
                values.append(float(np.corrcoef(score[valid], forward[valid])[0, 1]))
        output[horizon] = round(float(np.mean(values)), 8) if values else None
    return output


def compute_turnover(factor_panel: np.ndarray, top_k: int) -> float:
    values = np.asarray(factor_panel, dtype=float)
    if values.ndim != 2 or values.shape[1] < 2 or top_k <= 0:
        return 0.0
    previous: set[int] | None = None
    changes = []
    for day in range(values.shape[1]):
        valid = np.where(np.isfinite(values[:, day]))[0]
        if not len(valid):
            continue
        current = set(valid[np.argsort(values[valid, day], kind="mergesort")[-min(top_k, len(valid)):]].tolist())
        if previous is not None:
            changes.append(len(current - previous) / max(len(current), 1))
        previous = current
    return round(float(np.mean(changes)), 8) if changes else 0.0


def compute_capacity_proxy(volumes: np.ndarray, selected_weights: np.ndarray | None = None, participation_rate: float = .1) -> dict:
    volume = np.asarray(volumes, dtype=float)
    if volume.ndim != 2:
        raise ValueError("volumes must be 2D")
    usable = volume[np.isfinite(volume) & (volume > 0)]
    daily_capacity = float(np.sum(usable) / max(volume.shape[1], 1) * participation_rate) if usable.size else 0.0
    concentration = None
    if selected_weights is not None:
        weights = np.asarray(selected_weights, dtype=float)
        valid = weights[np.isfinite(weights) & (weights > 0)]
        concentration = float(np.max(valid) / np.sum(valid)) if valid.size and np.sum(valid) else None
    return {"daily_notional_capacity_proxy": round(daily_capacity, 6), "participation_rate": participation_rate, "max_weight_concentration": concentration}
