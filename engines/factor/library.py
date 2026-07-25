"""因子库持久化：config/factor_library.yaml 读写与入库判重。"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from engines.factor.lifecycle import FactorLifecycleStatus
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

LIBRARY_PATH = "config/factor_library.yaml"
MAX_CORRELATION = 0.9  # 与库内 active 因子面板逐日截面相关绝对值上限，超过视为重复
ACTIVE_STATUS = FactorLifecycleStatus.ACTIVE.value
RESEARCH_STATUS = FactorLifecycleStatus.OOS_PASS.value
LEGACY_UNVERIFIED_STATUS = FactorLifecycleStatus.LEGACY_UNVERIFIED.value


def _default_path() -> Path:
    return project_root() / LIBRARY_PATH


def load_library(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else _default_path()
    data = _read_library_file(cfg_path)
    data.setdefault("factors", [])
    return data


def _read_library_file(cfg_path: Path) -> dict:
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取因子库失败 %s: %s", cfg_path, exc)
        data = {}
    return data


def save_library(data: dict, path: str | Path | None = None, lease_guard: Callable[[], None] | None = None) -> None:
    cfg_path = Path(path) if path else _default_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(cfg_path):
        if lease_guard:
            lease_guard()
        latest = _read_library_file(cfg_path)
        latest.setdefault("factors", [])
        if _has_new_factors(latest, data):
            _merge_factor_lists(latest, data)
        else:
            latest = dict(data)
            latest.setdefault("factors", [])
        if lease_guard:
            lease_guard()
        payload = yaml.safe_dump(latest, allow_unicode=True, sort_keys=False)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{cfg_path.name}.", suffix=".tmp", dir=str(cfg_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, cfg_path)
            data.clear()
            data.update(latest)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)


def next_factor_id(library: dict) -> str:
    max_id = 0
    for factor in library.get("factors", []):
        fid = str(factor.get("id") or "")
        if fid.startswith("F") and fid[1:].isdigit():
            max_id = max(max_id, int(fid[1:]))
    return f"F{max_id + 1:03d}"


def _merge_factor_lists(latest: dict, incoming: dict) -> None:
    latest_factors = latest.setdefault("factors", [])
    seen_hashes = {str(f.get("candidate_hash")) for f in latest_factors if f.get("candidate_hash")}
    seen_rpn = {json_key(f.get("rpn") or []) for f in latest_factors}
    seen_ids = {str(f.get("id")) for f in latest_factors if f.get("id")}
    for factor in incoming.get("factors", []):
        candidate_hash = str(factor.get("candidate_hash") or "")
        rpn_key = json_key(factor.get("rpn") or [])
        if (candidate_hash and candidate_hash in seen_hashes) or rpn_key in seen_rpn:
            continue
        if str(factor.get("id") or "") in seen_ids:
            factor["id"] = next_factor_id(latest)
        latest_factors.append(factor)
        seen_ids.add(str(factor.get("id")))
        if candidate_hash:
            seen_hashes.add(candidate_hash)
        seen_rpn.add(rpn_key)


def _has_new_factors(latest: dict, incoming: dict) -> bool:
    latest_factors = latest.get("factors") or []
    seen_hashes = {str(f.get("candidate_hash")) for f in latest_factors if f.get("candidate_hash")}
    seen_rpn = {json_key(f.get("rpn") or []) for f in latest_factors}
    seen_ids = {str(f.get("id")) for f in latest_factors if f.get("id")}
    for factor in incoming.get("factors", []):
        candidate_hash = str(factor.get("candidate_hash") or "")
        if candidate_hash and candidate_hash in seen_hashes:
            continue
        if json_key(factor.get("rpn") or []) in seen_rpn:
            continue
        if str(factor.get("id") or "") in seen_ids:
            continue
        return True
    return False


def json_key(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def _file_lock(path: Path):
    try:
        from filelock import FileLock
    except ImportError:
        yield
        return
    lock = FileLock(str(path) + ".lock")
    with lock:
        yield


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
    """追加入库条目并返回该条目。

    新挖出的因子只完成研究准入，不能直接进入 ACTIVE。
    """
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
        "status": RESEARCH_STATUS,
        "validation_stage": RESEARCH_STATUS,
    }
    library.setdefault("factors", []).append(entry)
    return entry


def active_factors(library: dict, limit: int | None = None) -> list[dict]:
    factors = [f for f in library.get("factors", []) if f.get("status") == ACTIVE_STATUS]
    factors.sort(key=lambda f: (f.get("metrics") or {}).get("fitness", float("-inf")), reverse=True)
    return factors[:limit] if limit else factors


def research_validated_factors(library: dict, limit: int | None = None) -> list[dict]:
    """返回可用于研究预检的因子，不包含旧版未核验因子。"""
    allowed = {
        FactorLifecycleStatus.OOS_PASS.value,
        FactorLifecycleStatus.PAPER_TRADING.value,
        FactorLifecycleStatus.APPROVED.value,
        FactorLifecycleStatus.ACTIVE.value,
    }
    factors = [f for f in library.get("factors", []) if f.get("status") in allowed]
    factors.sort(key=lambda f: (f.get("metrics") or {}).get("fitness", float("-inf")), reverse=True)
    return factors[:limit] if limit else factors


def paper_trading_factors(library: dict, limit: int | None = None) -> list[dict]:
    """Factors allowed in the forward paper portfolio."""
    from engines.factor.lifecycle import FactorLifecycleStatus

    allowed = {
        FactorLifecycleStatus.PAPER_TRADING.value,
        FactorLifecycleStatus.APPROVED.value,
        FactorLifecycleStatus.ACTIVE.value,
    }
    factors = [f for f in library.get("factors", []) if f.get("status") in allowed]
    factors.sort(key=lambda f: (f.get("metrics") or {}).get("fitness", float("-inf")), reverse=True)
    return factors[:limit] if limit else factors


__all__ = [
    "load_library",
    "save_library",
    "add_factor",
    "active_factors",
    "research_validated_factors",
    "paper_trading_factors",
    "is_duplicate",
    "next_factor_id",
    "ACTIVE_STATUS",
    "RESEARCH_STATUS",
    "LEGACY_UNVERIFIED_STATUS",
]
