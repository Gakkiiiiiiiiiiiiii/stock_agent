from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel


class TemporalCheckResult(BaseModel):
    valid: bool | None


def recall_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & expected) / len(expected) if expected else 1.0


def precision_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    return len(set(retrieved[:k]) & expected) / k if k else 0.0


def reciprocal_rank(retrieved: list[str], expected: set[str]) -> float:
    return next((1 / index for index, value in enumerate(retrieved, 1) if value in expected), 0.0)


def ndcg_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    dcg = sum(1 / math.log2(index + 1) for index, value in enumerate(retrieved[:k], 1) if value in expected)
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(len(expected), k) + 1))
    return dcg / ideal if ideal else 1.0


def temporal_checks(contexts: list[dict], as_of: datetime | None) -> list[TemporalCheckResult]:
    if as_of is None:
        return [TemporalCheckResult(valid=None) for _ in contexts]
    reference = as_of.astimezone(UTC) if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    results = []
    for item in contexts:
        record = item.get("record") or item
        raw = record.get("source_date") or record.get("as_of_time")
        if not raw:
            results.append(TemporalCheckResult(valid=None))
            continue
        try:
            observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            observed = observed.astimezone(UTC) if observed.tzinfo else observed.replace(tzinfo=UTC)
            results.append(TemporalCheckResult(valid=observed <= reference))
        except ValueError:
            results.append(TemporalCheckResult(valid=None))
    return results


def temporal_summary(checks: list[TemporalCheckResult]) -> dict[str, float]:
    known = [check.valid for check in checks if check.valid is not None]
    valid = sum(item is True for item in known)
    invalid = sum(item is False for item in known)
    total = len(checks)
    return {
        "temporal_accuracy": valid / (valid + invalid) if known else 0.0,
        "temporal_coverage": len(known) / total if total else 0.0,
        "temporal_unknown_rate": (total - len(known)) / total if total else 0.0,
        "future_leakage_count": float(invalid),
    }
