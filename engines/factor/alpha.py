"""alpha 分数合成：把因子库 active 因子等权合成为截面打分。

供 walk-forward 预检（engines/factor/walkforward.py）与前向模拟盘
（workers/factor_paper_worker.py）共用，也可替代 mcp_servers/factor_mining_server
.scan_alpha_factors 中的内联逻辑（因子均以 cs_* 收尾，截面分位直接可比）。
"""
from __future__ import annotations

import numpy as np

from engines.factor.fitness import _rank
from engines.factor.vm import StackVM


def compose_alpha_scores(panel: dict[str, np.ndarray], factors: list[dict]) -> tuple[np.ndarray | None, int]:
    """各因子最新一日截面分位的等权均值，返回 (scores, 有效因子数)。

    scores 形状 (n_symbols,)，某标的在所有因子上均为 NaN 时合成值保持 NaN；
    没有任何因子可计算时返回 (None, 0)。
    """
    vm = StackVM()
    columns: list[np.ndarray] = []
    for factor in factors:
        values = vm.execute(factor.get("rpn") or [], panel)
        if values is None:
            continue
        latest = _rank(np.asarray(values[:, -1], dtype=float))
        if np.isnan(latest).all():
            continue
        columns.append(latest)
    if not columns:
        return None, 0
    with np.errstate(invalid="ignore"):
        combined = np.nanmean(np.vstack(columns), axis=0)
    return combined, len(columns)


__all__ = ["compose_alpha_scores"]
