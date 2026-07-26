"""因子库持久化：config/factor_library.yaml 读写与入库判重。"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
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

# 稳定身份字段：同一因子被识别后不允许被旧 Worker 的 incoming 快照覆盖，
# 否则磁盘中的规范 ID 可能被改写并与库内其他条目撞号。
_IMMUTABLE_IDENTITY_FIELDS = {
    "id",
    "candidate_hash",
    "rpn",
    "normalized_rpn",
    "discovered_at",
}

# 来源字段：首次创建后不允许被旧 Worker 覆盖。
_PROTECTED_PROVENANCE_FIELDS = {
    "research_run_id",
    "first_code_commit",
    "first_data_version",
}

# OOS 审计类指标：incoming 缺失时保留 existing，不得被擦除。
_PROTECTED_METRIC_KEYS = {
    "final_oos_summary",
    "final_oos_audit_ref",
    "final_oos_audit",
}

# 研究类指标：对某一因子身份不可变，普通监控更新不得覆盖。
# Discovery 原始指标（rank_ic/icir/fitness/coverage/topk_excess_annual_return）
# 与研究审计字段同属 research 语义，一旦写入即不可变。
_IMMUTABLE_RESEARCH_METRIC_KEYS = {
    "final_oos_summary",
    "final_oos_audit_ref",
    "final_oos_audit",
    "discovery_rank_ic",
    "discovery_icir",
    "research_data_version",
    "research_run_id",
    "rank_ic",
    "icir",
    "fitness",
    "coverage",
    "topk_excess_annual_return",
}

# 终态：退役因子不允许被自动流程重新激活。
_TERMINAL_STATUSES = {
    FactorLifecycleStatus.RETIRED.value,
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
        _validate_library_uniqueness(result.library)
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
    # 三个索引：candidate_hash > normalized_rpn/rpn > id。
    # 旧因子可能缺 Hash，仅靠单身份键会漏匹配，导致同一因子被重复插入。
    by_hash: dict[str, dict] = {}
    by_rpn: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for item in merged_factors:
        _index_factor(item, by_hash, by_rpn, by_id)
    used_ids = {str(item.get("id")) for item in merged_factors if item.get("id")}

    for raw in (incoming or {}).get("factors") or []:
        factor = dict(raw)
        candidate_hash = str(factor.get("candidate_hash") or "")
        rpn_key = _rpn_key(factor)
        factor_id = str(factor.get("id") or "")

        existing = None
        if candidate_hash:
            existing = by_hash.get(candidate_hash)
        if existing is None and rpn_key:
            existing = by_rpn.get(rpn_key)
        # id 兜底只在 incoming 既无 Hash 也无 RPN 时启用：
        # Hash/RPN 不同但 id 相同属于新因子撞号，应重新编号而不是误合并。
        if existing is None and not candidate_hash and not rpn_key and factor_id:
            existing = by_id.get(factor_id)

        if existing is not None:
            _merge_factor(existing, factor)
            # RPN 命中缺 Hash 的旧因子时补写 Hash；已有 Hash 不得修改。
            if not existing.get("candidate_hash") and candidate_hash:
                existing["candidate_hash"] = candidate_hash
                by_hash[candidate_hash] = existing
            result.persisted_entries.append(existing)
            if candidate_hash:
                result.updated_candidate_hashes.add(candidate_hash)
            continue

        old_id = factor_id
        if not old_id or old_id in used_ids:
            factor["id"] = next_factor_id(merged)
            if old_id:
                result.reassigned_ids[old_id] = str(factor["id"])
        merged_factors.append(factor)
        _index_factor(factor, by_hash, by_rpn, by_id)
        used_ids.add(str(factor.get("id")))
        result.persisted_entries.append(factor)
        if candidate_hash:
            result.inserted_candidate_hashes.add(candidate_hash)
    return result


def _rpn_key(factor: dict) -> str:
    rpn = factor.get("normalized_rpn") or factor.get("rpn") or []
    return json_key(rpn) if rpn else ""


def _index_factor(item: dict, by_hash: dict, by_rpn: dict, by_id: dict) -> None:
    candidate_hash = str(item.get("candidate_hash") or "")
    if candidate_hash:
        by_hash.setdefault(candidate_hash, item)
    rpn_key = _rpn_key(item)
    if rpn_key:
        by_rpn.setdefault(rpn_key, item)
    factor_id = str(item.get("id") or "")
    if factor_id:
        by_id.setdefault(factor_id, item)


def _validate_library_uniqueness(library: dict) -> None:
    """写盘前的全局唯一性终检：id / candidate_hash / rpn 任一重复即拒绝保存。"""
    ids: set[str] = set()
    hashes: set[str] = set()
    rpns: set[str] = set()
    for factor in library.get("factors") or []:
        factor_id = str(factor.get("id") or "")
        candidate_hash = str(factor.get("candidate_hash") or "")
        rpn_key = json_key(factor.get("normalized_rpn") or factor.get("rpn") or [])
        if factor_id:
            if factor_id in ids:
                raise ValueError(f"DUPLICATE_FACTOR_ID:{factor_id}")
            ids.add(factor_id)
        if candidate_hash:
            if candidate_hash in hashes:
                raise ValueError(f"DUPLICATE_CANDIDATE_HASH:{candidate_hash}")
            hashes.add(candidate_hash)
        if rpn_key != "[]":
            if rpn_key in rpns:
                raise ValueError(f"DUPLICATE_FACTOR_RPN:{rpn_key}")
            rpns.add(rpn_key)


def _merge_factor(existing: dict, incoming: dict) -> None:
    old_status = existing.get("status")
    old_stage = existing.get("validation_stage")
    merged_metrics = _merge_metrics(
        dict(existing.get("metrics") or {}),
        dict(incoming.get("metrics") or {}),
    )
    for key, value in incoming.items():
        if key == "metrics":
            continue
        if key in _IMMUTABLE_IDENTITY_FIELDS:
            continue
        if key in _PROTECTED_PROVENANCE_FIELDS and existing.get(key) is not None:
            continue
        existing[key] = value
    existing["metrics"] = merged_metrics
    existing["status"] = _merge_status(old_status, incoming.get("status"))
    existing["validation_stage"] = _merge_status(old_stage, incoming.get("validation_stage"))


@dataclass(frozen=True)
class MetricsFreshness:
    """指标新鲜度：as_of → updated_at → revision；data_version 只做一致性检查，不参与排序。"""

    as_of: str
    updated_at: str
    revision: int
    data_version: str

    def has_version(self) -> bool:
        return bool(self.as_of or self.updated_at or self.revision > 0)


def _metrics_freshness(metrics: dict) -> MetricsFreshness:
    try:
        revision = int(metrics.get("metrics_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    return MetricsFreshness(
        as_of=str(metrics.get("metrics_as_of") or ""),
        updated_at=str(metrics.get("metrics_updated_at") or ""),
        revision=revision,
        data_version=str(metrics.get("metrics_data_version") or ""),
    )


def _compare_freshness(existing: MetricsFreshness, incoming: MetricsFreshness) -> int:
    """incoming 更新返回 1，更旧返回 -1，同一时间返回 0（版本冲突抛错）。"""
    left = (existing.as_of, existing.updated_at, existing.revision)
    right = (incoming.as_of, incoming.updated_at, incoming.revision)
    if right > left:
        return 1
    if right < left:
        return -1
    if (
        existing.data_version
        and incoming.data_version
        and existing.data_version != incoming.data_version
    ):
        raise ValueError("METRICS_VERSION_CONFLICT")
    return 0


def _merge_metrics(existing: dict, incoming: dict) -> dict:
    existing_freshness = _metrics_freshness(existing)
    incoming_freshness = _metrics_freshness(incoming)
    if incoming_freshness.has_version() or existing_freshness.has_version():
        comparison = _compare_freshness(existing_freshness, incoming_freshness)
        # 旧快照不得覆盖较新指标；两边都无版本信息时保持原合并行为（兼容模式）。
        if incoming_freshness.has_version() and comparison < 0:
            return dict(existing)
        # 严格模式：existing 有版本而 incoming 无版本时禁止覆盖。
        if existing_freshness.has_version() and not incoming_freshness.has_version():
            return dict(existing)
    merged = {**existing, **incoming}
    for key in _PROTECTED_METRIC_KEYS:
        if key in existing and key not in incoming:
            merged[key] = existing[key]
    # 研究类指标一旦写入即不可变（因子版本化在 v2.3 PostgreSQL 完成）。
    for key in _IMMUTABLE_RESEARCH_METRIC_KEYS:
        if key in existing:
            merged[key] = existing[key]
    # research 块整体不可变：禁止覆盖既有研究记录
    if "research" in existing:
        merged["research"] = deepcopy(existing["research"])
    elif "research" in incoming:
        merged["research"] = deepcopy(incoming["research"])
    # monitoring 块按自身新鲜度合并
    merged["monitoring"] = _merge_monitoring_metrics(
        existing.get("monitoring") or {},
        incoming.get("monitoring") or {},
    )
    return merged


def _merge_monitoring_metrics(existing: dict, incoming: dict) -> dict:
    if not existing:
        return dict(incoming)
    if not incoming:
        return dict(existing)
    existing_freshness = _metrics_freshness({
        "metrics_as_of": existing.get("as_of"),
        "metrics_updated_at": existing.get("updated_at"),
        "metrics_revision": existing.get("revision"),
        "metrics_data_version": existing.get("data_version"),
    })
    incoming_freshness = _metrics_freshness({
        "metrics_as_of": incoming.get("as_of"),
        "metrics_updated_at": incoming.get("updated_at"),
        "metrics_revision": incoming.get("revision"),
        "metrics_data_version": incoming.get("data_version"),
    })
    if incoming_freshness.has_version() or existing_freshness.has_version():
        comparison = _compare_freshness(existing_freshness, incoming_freshness)
        if comparison < 0:
            return dict(existing)
    return {**existing, **incoming}


def build_research_metrics(
    discovery: dict,
    final_oos: dict,
    *,
    data_version: str | None,
    research_run_id: str,
    evaluated_at: str,
    audit_ref: str,
) -> dict:
    """研究指标结构：research 不可变 + monitoring 占位 + 平铺兼容字段。"""
    return {
        "research": {
            "discovery": {
                **discovery,
                "evaluated_at": evaluated_at,
                "data_version": data_version,
                "research_run_id": research_run_id,
            },
            "final_oos": {
                **final_oos,
                "audit_ref": audit_ref,
            },
        },
        "monitoring": {},
        # 平铺兼容字段（与 research.discovery 同源，合并时同样不可变）
        **discovery,
        "final_oos_summary": final_oos,
        "final_oos_audit_ref": audit_ref,
        "research_data_version": data_version,
        "research_run_id": research_run_id,
    }


def _merge_status(old_status, incoming_status) -> str:
    old = str(old_status or RESEARCH_STATUS)
    if old in _TERMINAL_STATUSES:
        return old
    return _max_status(old, incoming_status)


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
    research_run_id: str | None = None,
    data_version: str | None = None,
    metrics_as_of: str | None = None,
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
    if research_run_id:
        entry["research_run_id"] = research_run_id
    if data_version:
        entry["first_data_version"] = data_version
    if metrics_as_of:
        metrics.setdefault("metrics_as_of", metrics_as_of)
        metrics.setdefault(
            "metrics_updated_at",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        if data_version:
            metrics.setdefault("metrics_data_version", data_version)
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
    "build_research_metrics",
    "active_factors",
    "research_validated_factors",
    "paper_trading_factors",
    "is_duplicate",
    "next_factor_id",
    "ACTIVE_STATUS",
    "RESEARCH_STATUS",
    "LEGACY_UNVERIFIED_STATUS",
]
