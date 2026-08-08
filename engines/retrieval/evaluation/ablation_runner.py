from __future__ import annotations

from pathlib import Path

from engines.retrieval.evaluation.models import AblationResult, RetrievalGoldenCase
from engines.retrieval.evaluation.report import write_evaluation_report
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner
from engines.retrieval.config import RetrievalConfig
from engines.retrieval.hybrid_retriever import HybridRetriever


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


def build_standard_ablation_variants(factory=HybridRetriever) -> dict[str, object]:
    configs = {
        "dense_only": RetrievalConfig(sparse_enabled=False, reranker_enabled=False, freshness_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "dense_sparse": RetrievalConfig(reranker_enabled=False, freshness_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_reranker": RetrievalConfig(freshness_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_freshness": RetrievalConfig(source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_source_priority": RetrievalConfig(conflict_resolution_enabled=False),
        "with_conflict_resolution": RetrievalConfig(),
    }
    return {name: factory(config=config) for name, config in configs.items()}
