"""因子库持久化：config/factor_library.yaml 读写与入库判重。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

LIBRARY_PATH = "config/factor_library.yaml"
MAX_CORRELATION = 0.9  # 与库内 active 因子面板逐日截面相关绝对值上限，超过视为重复


def _default_path() -> Path:
    return project_root() / LIBRARY_PATH


def load_library(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else _default_path()
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取因子库失败 %s: %s", cfg_path, exc)
        data = {}
    data.setdefault("factors", [])
    return data


def save_library(data: dict, path: str | Path | None = None) -> None:
    cfg_path = Path(path) if path else _default_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def next_factor_id(library: dict) -> str:
    max_id = 0
    for factor in library.get("factors", []):
        fid = str(factor.get("id") or "")
        if fid.startswith("F") and fid[1:].isdigit():
            max_id = max(max_id, int(fid[1:]))
    return f"F{max_id + 1:03d}"


def _daily_cross_corr(a: np.ndarray, b: np.ndarray) -> float:
    """两个因子面板的逐日截面相关系数均值（NaN 日跳过）。"""
    corrs: list[float] = []
    for d in range(min(a.shape[1], b.shape[1])):
        x, y = a[:, d], b[:, d]
        valid = ~np.isnan(x) & ~np.isnan(y)
        if valid.sum() < 10:
            continue
        xv, yv = x[valid], y[valid]
        if np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
            continue
        corrs.append(float(np.corrcoef(xv, yv)[0, 1]))
    return float(np.mean(corrs)) if corrs else 0.0


def is_duplicate(
    rpn: list[str],
    factor_panel: np.ndarray,
    library: dict,
    active_panels: dict[str, np.ndarray] | None = None,
) -> bool:
    """判重：RPN 完全相同，或与任一 active 因子的面板逐日截面相关绝对值 > 0.9。"""
    for factor in library.get("factors", []):
        if factor.get("rpn") == list(rpn):
            return True
    if active_panels:
        for panel in active_panels.values():
            if panel.shape != factor_panel.shape:
                continue
            if abs(_daily_cross_corr(factor_panel, panel)) > MAX_CORRELATION:
                return True
    return False


def add_factor(
    library: dict,
    rpn: list[str],
    expression: str,
    hypothesis: str,
    metrics: dict,
    universe: list[str],
    horizon: int,
    llm_model: str = "",
) -> dict:
    """追加入库条目并返回该条目。"""
    entry = {
        "id": next_factor_id(library),
        "rpn": list(rpn),
        "expression": expression,
        "hypothesis": hypothesis,
        "metrics": metrics,
        "universe": list(universe),
        "horizon": horizon,
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm_model": llm_model,
        "status": "active",
    }
    library.setdefault("factors", []).append(entry)
    return entry


def active_factors(library: dict, limit: int | None = None) -> list[dict]:
    factors = [f for f in library.get("factors", []) if f.get("status") == "active"]
    factors.sort(key=lambda f: (f.get("metrics") or {}).get("fitness", float("-inf")), reverse=True)
    return factors[:limit] if limit else factors


__all__ = ["load_library", "save_library", "add_factor", "active_factors", "is_duplicate", "next_factor_id"]
