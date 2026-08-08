from __future__ import annotations

import math
from datetime import UTC, datetime


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


def temporal_accuracy(contexts: list[dict], as_of: datetime | None) -> float:
    if as_of is None or not contexts:
        return 1.0
    reference = as_of.astimezone(UTC) if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    valid = 0
    for item in contexts:
        record = item.get("record") or item
        raw = record.get("source_date") or record.get("as_of_time")
        if not raw:
            valid += 1
            continue
        try:
            observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            observed = observed.astimezone(UTC) if observed.tzinfo else observed.replace(tzinfo=UTC)
            valid += observed <= reference
        except ValueError:
            valid += 1
    return valid / len(contexts)
