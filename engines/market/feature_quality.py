"""市场数据质量门控：根据快照覆盖度评估 OK / DEGRADED / INSUFFICIENT。"""
from __future__ import annotations

from typing import Any

DATA_QUALITY_INSUFFICIENT = "DATA_QUALITY_INSUFFICIENT"

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "min_quote_coverage": 0.9,
    "degraded_quote_coverage": 0.7,
    "min_sector_membership_coverage": 0.8,
    "degraded_sector_membership_coverage": 0.6,
    "require_index_data": True,
    "require_limit_metadata": True,
}


def evaluate_market_data_quality(snapshot: dict[str, Any], thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    """评估快照数据质量，返回 {"status", "quality_score", "quality_flags"}。

    检查项：全市场行情覆盖度、指数数据可用性、板块成分覆盖度、涨跌停元数据。
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    flags: list[str] = []
    checks_total = 0
    checks_passed = 0
    insufficient = False
    degraded = False

    # 1) 行情覆盖度
    quote_coverage = snapshot.get("quote_coverage")
    if quote_coverage is not None:
        checks_total += 1
        coverage = float(quote_coverage)
        if coverage >= float(cfg["min_quote_coverage"]):
            checks_passed += 1
        elif coverage >= float(cfg["degraded_quote_coverage"]):
            degraded = True
            flags.append("QUOTE_COVERAGE_DEGRADED")
        else:
            insufficient = True
            flags.append("QUOTE_COVERAGE_INSUFFICIENT")

    # 2) 指数数据可用性
    if cfg.get("require_index_data"):
        checks_total += 1
        indices = snapshot.get("indices") or {}
        index_available = bool(indices) or snapshot.get("index_return_5d") is not None or snapshot.get("index_return_20d") is not None
        if index_available:
            checks_passed += 1
        else:
            insufficient = True
            flags.append("INDEX_DATA_UNAVAILABLE")

    # 3) 板块成分覆盖度
    membership_coverage = snapshot.get("sector_membership_coverage")
    if membership_coverage is not None:
        checks_total += 1
        coverage = float(membership_coverage)
        if coverage >= float(cfg["min_sector_membership_coverage"]):
            checks_passed += 1
        elif coverage >= float(cfg["degraded_sector_membership_coverage"]):
            degraded = True
            flags.append("SECTOR_MEMBERSHIP_COVERAGE_DEGRADED")
        else:
            insufficient = True
            flags.append("SECTOR_MEMBERSHIP_COVERAGE_INSUFFICIENT")

    # 4) 涨跌停元数据
    if cfg.get("require_limit_metadata"):
        checks_total += 1
        if snapshot.get("limit_up_count") is not None and snapshot.get("limit_down_count") is not None:
            checks_passed += 1
        else:
            degraded = True
            flags.append("LIMIT_METADATA_MISSING")

    if insufficient:
        status = "INSUFFICIENT"
        flags.append(DATA_QUALITY_INSUFFICIENT)
    elif degraded:
        status = "DEGRADED"
    else:
        status = "OK"
    quality_score = round(checks_passed / checks_total, 4) if checks_total else 0.0
    return {"status": status, "quality_score": quality_score, "quality_flags": sorted(set(flags))}
