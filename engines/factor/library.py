"""因子库持久化：config/factor_library.yaml 读写与入库判重。"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from engines.factor.lifecycle import FactorLifecycleStatus
from financial_agent.research_config import get_research_config
from financial_agent.utils import project_root

logger = logging.getLogger(__name__)

LIBRARY_PATH = "config/factor_library.yaml"
MAX_CORRELATION = 0.9  # 与库内 active 因子面板逐日截面相关绝对值上限，超过视为重复
ACTIVE_STATUS = FactorLifecycleStatus.ACTIVE.value
RESEARCH_STATUS = FactorLifecycleStatus.OOS_PASS.value
LEGACY_UNVERIFIED_STATUS = FactorLifecycleStatus.LEGACY_UNVERIFIED.value

_STATUS_RANK = {
    FactorLifecycleStatus.DRAFT.value: 0,
    FactorLifecycleStatus.COMPUTABLE.value: 1,
    FactorLifecycleStatus.LEGACY_UNVERIFIED.value: 1,
    FactorLifecycleStatus.IN_SAMPLE_PASS.value: 2,
    FactorLifecycleStatus.OOS_PASS.value: 3,
    FactorLifecycleStatus.PAPER_TRADING.value: 4,
    FactorLifecycleStatus.APPROVED.value: 5,
    FactorLifecycleStatus.ACTIVE.value: 6,
    FactorLifecycleStatus.DEGRADED.value: 7,
    FactorLifecycleStatus.RETIRED.value: 8,
}


@dataclass
class LibraryMergeResult:
    library: dict
    persisted_entries: list[dict] = field(default_factory=list)
    inserted_candidate_hashes: set[str] = field(default_factory=set)
    updated_candidate_hashes: set[str] = field(default_factory=set)
    reassigned_ids: dict[str, str] = field(default_factory=dict)

    @property
    def persisted_by_hash(self) -> dict[str, dict]:
        return {
            str(item.get("candidate_hash")): item
            for item in self.persisted_entries
            if item.get("candidate_hash")
        }


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


def save_library(data: dict, path: str | Path | None = None, lease_guard: Callable[[], None] | None = None) -> LibraryMergeResult:
    cfg_path = Path(path) if path else _default_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(cfg_path):
        if lease_guard:
            lease_guard()
        latest = _read_library_file(cfg_path)
        latest.setdefault("factors", [])
        result = merge_library(latest, data)
        if lease_guard:
            lease_guard()
        payload = yaml.safe_dump(result.library, allow_unicode=True, sort_keys=False)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{cfg_path.name}.", suffix=".tmp", dir=str(cfg_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, cfg_path)
            data.clear()
            data.update(result.library)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
    return result


def next_factor_id(library: dict) -> str:
    max_id = 0
    for factor in library.get("factors", []):
        fid = str(factor.get("id") or "")
        if fid.startswith("F") and fid[1:].isdigit():
            max_id = max(max_id, int(fid[1:]))
    return f"F{max_id + 1:03d}"


def merge_library(latest: dict, incoming: dict) -> LibraryMergeResult:
    merged = dict(latest or {})
    merged_factors = [dict(item) for item in (latest or {}).get("factors") or []]
    merged["factors"] = merged_factors
    result = LibraryMergeResult(library=merged)
    by_identity = {_factor_identity(item): item for item in merged_factors}
    used_ids = {str(item.get("id")) for item in merged_factors if item.get("id")}

    for raw in (incoming or {}).get("factors") or []:
        factor = dict(raw)
        identity = _factor_identity(factor)
        candidate_hash = str(factor.get("candidate_hash") or "")
        existing = by_identity.get(identity)
        if existing is not None:
            _merge_factor(existing, factor)
            result.persisted_entries.append(existing)
            if candidate_hash:
                result.updated_candidate_hashes.add(candidate_hash)
            continue

        old_id = str(factor.get("id") or "")
        if not old_id or old_id in used_ids:
            factor["id"] = next_factor_id(merged)
            if old_id:
                result.reassigned_ids[old_id] = str(factor["id"])
        merged_factors.append(factor)
        by_identity[identity] = factor
        used_ids.add(str(factor.get("id")))
        result.persisted_entries.append(factor)
        if candidate_hash:
            result.inserted_candidate_hashes.add(candidate_hash)
    return result


def _factor_identity(factor: dict) -> str:
    if factor.get("candidate_hash"):
        return f"hash:{factor['candidate_hash']}"
    rpn = factor.get("normalized_rpn") or factor.get("rpn") or []
    if rpn:
        return f"rpn:{json_key(rpn)}"
    return f"id:{factor.get('id')}"


def _merge_factor(existing: dict, incoming: dict) -> None:
    old_status = existing.get("status")
    old_stage = existing.get("validation_stage")
    existing_metrics = dict(existing.get("metrics") or {})
    incoming_metrics = dict(incoming.get("metrics") or {})
    protected_metric_keys = {"final_oos_summary", "final_oos_audit_ref", "final_oos_audit"}
    merged_metrics = {**existing_metrics, **incoming_metrics}
    for key in protected_metric_keys:
        if key in existing_metrics and key not in incoming_metrics:
            merged_metrics[key] = existing_metrics[key]
    existing.update({key: value for key, value in incoming.items() if key != "metrics"})
    existing["metrics"] = merged_metrics
    existing["status"] = _max_status(old_status, incoming.get("status"))
    existing["validation_stage"] = _max_status(old_stage, incoming.get("validation_stage"))


def _max_status(left, right) -> str:
    left = str(left or RESEARCH_STATUS)
    right = str(right or left)
    return right if _STATUS_RANK.get(right, -1) > _STATUS_RANK.get(left, -1) else left


def json_key(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def _file_lock(path: Path):
    from filelock import FileLock, Timeout

    timeout = get_research_config().factor_library.lock_timeout_seconds
    lock = FileLock(str(path) + ".lock", timeout=timeout)
    try:
        with lock:
            yield
    except Timeout as exc:
        raise RuntimeError("FACTOR_LIBRARY_LOCK_TIMEOUT") from exc


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
    "merge_library",
    "LibraryMergeResult",
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
