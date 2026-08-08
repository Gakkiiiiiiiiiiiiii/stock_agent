from __future__ import annotations

from pathlib import Path

from engines.retrieval.evaluation.models import AblationResult, RetrievalGoldenCase
from engines.retrieval.evaluation.report import write_evaluation_report
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner


class RetrievalAblationRunner:
    def __init__(self, variants: dict[str, object]) -> None:
        self.variants = variants

    def run(self, cases: list[RetrievalGoldenCase], output_root: Path | None = None) -> list[AblationResult]:
        results: list[AblationResult] = []
        for name, retriever in self.variants.items():
            evaluation = RetrievalEvaluationRunner(retriever).run(cases)
            summary = evaluation["summary"]
            result = AblationResult(variant=name, expired_leakage=summary["expired_knowledge_leakage_rate"], avg_latency_ms=summary["latency_ms"], **{key: summary[key] for key in AblationResult.model_fields if key not in {"variant", "expired_leakage", "avg_latency_ms"}})
            results.append(result)
            if output_root:
                write_evaluation_report(evaluation, output_root / name)
        return results
