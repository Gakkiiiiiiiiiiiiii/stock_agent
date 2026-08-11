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


def graded_recall_at_k(retrieved: list[str], gains: dict[str, float], k: int) -> float:
    """Recall@k counting every labeled doc with gain >= 1 (partially_relevant included)."""
    relevant = {doc_id for doc_id, gain in gains.items() if gain >= 1}
    return len(set(retrieved[:k]) & relevant) / len(relevant) if relevant else 1.0


def graded_reciprocal_rank(retrieved: list[str], gains: dict[str, float]) -> float:
    """MRR: reciprocal rank of the first retrieved doc with gain >= 1."""
    relevant = {doc_id for doc_id, gain in gains.items() if gain >= 1}
    return next((1 / index for index, value in enumerate(retrieved, 1) if value in relevant), 0.0)


def graded_ndcg_at_k(retrieved: list[str], gains: dict[str, float], k: int) -> float:
    """nDCG@k with graded gains (gain / log2(rank+1)).

    Uniform gains (e.g. v1 binary labels mapped to grade 2) are scale-invariant,
    so this reduces exactly to the legacy binary ndcg_at_k for v1 datasets.
    """
    relevant = {doc_id: gain for doc_id, gain in gains.items() if gain >= 1}
    dcg = sum(relevant[value] / math.log2(index + 1) for index, value in enumerate(retrieved[:k], 1) if value in relevant)
    ideal_gains = sorted(relevant.values(), reverse=True)[:k]
    ideal = sum(gain / math.log2(index + 1) for index, gain in enumerate(ideal_gains, 1))
    return dcg / ideal if ideal else 1.0


def temporal_precision(records: list[dict], expired_ids: set[str], superseded_ids: set[str]) -> float:
    """Fraction of returned docs that are neither expired nor superseded.

    A doc is superseded when the golden data marks a fresher valid version in
    the corpus (case.superseded maps stale doc -> replacement).
    """
    if not records:
        return 1.0
    bad = sum(1 for record in records if _record_id(record) in expired_ids | superseded_ids)
    return 1.0 - bad / len(records)


def expired_context_rate(records: list[dict], expired_ids: set[str]) -> float:
    """Fraction of returned docs labeled expired (label or record status)."""
    if not records:
        return 0.0
    count = sum(1 for record in records if _record_id(record) in expired_ids or str(record.get("status", "")).lower() == "expired")
    return count / len(records)


def conflict_resolution_accuracy(retrieved: list[str], pairs: list) -> float | None:
    """For each contradictory pair, the winner must appear and rank above the loser (or loser absent).

    Returns None when the case annotates no pairs (excluded from the mean).
    """
    if not pairs:
        return None
    rank = {doc_id: index for index, doc_id in enumerate(retrieved)}
    correct = 0
    for pair in pairs:
        winner = rank.get(pair.winner_id)
        loser = rank.get(pair.loser_id)
        if winner is not None and (loser is None or winner < loser):
            correct += 1
    return correct / len(pairs)


def source_diversity(records: list[dict], k: int, available_source_types: set[str] | None = None) -> float:
    """Distinct source types in top-k / min(k, distinct available in corpus).

    When the corpus is unknown, the denominator falls back to the retrieved
    distinct count (i.e. 1.0), so the metric never penalizes uninstrumented runs.
    """
    top = records[:k]
    if not top:
        return 1.0
    distinct = {str(record.get("source_type") or "") for record in top} - {""}
    if not distinct:
        return 1.0
    available = available_source_types if available_source_types else distinct
    denominator = min(k, len(available))
    return len(distinct) / denominator if denominator else 1.0


def _record_id(record: dict) -> str:
    return str(record.get("id") or record.get("memory_id") or record.get("knowledge_uid") or record.get("title") or "")


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
