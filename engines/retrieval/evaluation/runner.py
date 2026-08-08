from __future__ import annotations

import json
import time
from pathlib import Path

from engines.retrieval.evaluation.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank, temporal_checks, temporal_summary
from engines.retrieval.evaluation.models import RetrievalGoldenCase


class RetrievalEvaluationRunner:
    def __init__(self, retriever) -> None:
        self.retriever = retriever

    def load_dataset(self, path: Path) -> list[RetrievalGoldenCase]:
        return [RetrievalGoldenCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def run(self, cases: list[RetrievalGoldenCase]) -> dict:
        rows = []
        for case in cases:
            started = time.perf_counter()
            result = self.retriever.retrieve(case.query, task_type=case.task_type, filters=case.retrieval_filters or None, top_k=10)
            latency_ms = (time.perf_counter() - started) * 1000
            contexts = result.get("contexts") or []
            retrieved = [_identifier(item) for item in contexts]
            expected = set(case.expected_ids or case.expected_subjects)
            records = [item.get("record") or item for item in contexts]
            types = {str(record.get("memory_type") or "") for record in records}
            regimes = {str(record.get("related_regime") or "") for record in records}
            temporal = temporal_summary(temporal_checks(contexts, case.as_of))
            row = {"case_id": case.case_id, "recall_at_5": recall_at_k(retrieved, expected, 5), "recall_at_10": recall_at_k(retrieved, expected, 10), "precision_at_5": precision_at_k(retrieved, expected, 5), "mrr": reciprocal_rank(retrieved, expected), "ndcg_at_10": ndcg_at_k(retrieved, expected, 10), "expired_leakage": any(str(record.get("status", "")).lower() == "expired" for record in records), **temporal, "conflict_accuracy": float(not bool(set(retrieved) & set(case.forbidden_ids))), "memory_type_recall": float(not case.expected_memory_types or set(case.expected_memory_types).issubset(types)), "regime_conditioned_recall": float(not case.expected_regime or case.expected_regime in regimes), "source_priority_accuracy": float(not case.expected_sources or any(record.get("source_type") in {item.get("source_type") for item in case.expected_sources} for record in records[:3])), "latency_ms": latency_ms}
            rows.append(row)
        count = len(rows) or 1
        metric_names = ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10", "temporal_accuracy", "temporal_coverage", "temporal_unknown_rate", "conflict_accuracy", "memory_type_recall", "regime_conditioned_recall", "source_priority_accuracy", "latency_ms")
        return {"cases": rows, "summary": {key: sum(float(row[key]) for row in rows) / count for key in metric_names} | {"expired_knowledge_leakage_rate": sum(row["expired_leakage"] for row in rows) / count, "future_leakage_count": sum(row["future_leakage_count"] for row in rows)}}


def _identifier(item: dict) -> str:
    record = item.get("record") or {}
    return str(record.get("id") or record.get("memory_id") or record.get("knowledge_uid") or record.get("title") or item.get("title") or "")
