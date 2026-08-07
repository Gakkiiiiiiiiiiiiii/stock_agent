from __future__ import annotations

import json
from pathlib import Path

from engines.retrieval.evaluation.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from engines.retrieval.evaluation.models import RetrievalGoldenCase


class RetrievalEvaluationRunner:
    def __init__(self, retriever) -> None:
        self.retriever = retriever

    def load_dataset(self, path: Path) -> list[RetrievalGoldenCase]:
        return [RetrievalGoldenCase.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def run(self, cases: list[RetrievalGoldenCase]) -> dict:
        rows = []
        for case in cases:
            result = self.retriever.retrieve(case.query, task_type=case.task_type, top_k=10)
            contexts = result.get("contexts") or []
            retrieved = [str((item.get("record") or {}).get("title") or item.get("title") or "") for item in contexts]
            expected = set(case.expected_subjects)
            rows.append({"id": case.id, "recall_at_5": recall_at_k(retrieved, expected, 5), "recall_at_10": recall_at_k(retrieved, expected, 10), "precision_at_5": precision_at_k(retrieved, expected, 5), "mrr": reciprocal_rank(retrieved, expected), "ndcg_at_10": ndcg_at_k(retrieved, expected, 10), "expired_leakage": any(str(item.get("status", "")).lower() == "expired" for item in contexts)})
        count = len(rows) or 1
        return {"cases": rows, "summary": {key: sum(float(row[key]) for row in rows) / count for key in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")} | {"expired_knowledge_leakage_rate": sum(row["expired_leakage"] for row in rows) / count}}
