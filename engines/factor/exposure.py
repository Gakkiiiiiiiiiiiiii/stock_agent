"""Lightweight cross-sectional exposure diagnostics (not a Barra replacement)."""
from __future__ import annotations

import numpy as np


def _correlation(factor: np.ndarray, exposure: np.ndarray) -> float | None:
    valid = np.isfinite(factor) & np.isfinite(exposure)
    if valid.sum() < 3 or np.std(factor[valid]) <= 1e-12 or np.std(exposure[valid]) <= 1e-12:
        return None
    return round(float(np.corrcoef(factor[valid], exposure[valid])[0, 1]), 8)


def compute_factor_exposures(factor: np.ndarray, market_caps: np.ndarray | None = None, liquidity: np.ndarray | None = None, industries: list[str] | None = None) -> dict:
    values = np.asarray(factor, dtype=float)
    result = {"size_exposure": _correlation(values, np.log(np.asarray(market_caps, dtype=float))) if market_caps is not None else None, "liquidity_exposure": _correlation(values, np.asarray(liquidity, dtype=float)) if liquidity is not None else None, "industry_exposure": {}}
    if industries is not None:
        if len(industries) != len(values):
            raise ValueError("industries length mismatch")
        for industry in sorted(set(str(item) for item in industries)):
            dummy = np.asarray([1.0 if str(item) == industry else 0.0 for item in industries])
            result["industry_exposure"][industry] = _correlation(values, dummy)
    return result
