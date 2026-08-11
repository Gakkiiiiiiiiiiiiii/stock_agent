from __future__ import annotations

from pathlib import Path

from engines.retrieval.evaluation.models import AblationResult, RetrievalGoldenCase
from engines.retrieval.evaluation.report import write_evaluation_report
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner
from engines.retrieval.config import RetrievalConfig
from engines.retrieval.hybrid_retriever import HybridRetriever


class RetrievalAblationRunner:
    def __init__(self, variants: dict[str, object], corpus_records: list[dict] | None = None) -> None:
        self.variants = variants
        self.corpus_records = corpus_records

    def run(self, cases: list[RetrievalGoldenCase], output_root: Path | None = None) -> list[AblationResult]:
        results: list[AblationResult] = []
        for name, retriever in self.variants.items():
            evaluation = RetrievalEvaluationRunner(retriever, corpus_records=self.corpus_records).run(cases)
            summary = evaluation["summary"]
            result = AblationResult(
                variant=name,
                expired_leakage=summary["expired_knowledge_leakage_rate"],
                avg_latency_ms=summary["latency_ms"],
                **{
                    key: summary[key]
                    for key in AblationResult.model_fields
                    if key not in {"variant", "expired_leakage", "avg_latency_ms"} and key in summary
                },
            )
            results.append(result)
            if output_root:
                write_evaluation_report(evaluation, output_root / name)
        return results


def build_standard_ablation_variants(factory=HybridRetriever) -> dict[str, object]:
    """Component ladder: dense_only / sparse_only / dense+sparse, then +bm25 score,
    +reranker, +freshness, +source_priority, +conflict_resolution (one at a time)."""
    configs = {
        "dense_only": RetrievalConfig(sparse_recall_enabled=False, bm25_score_enabled=False, reranker_enabled=False, freshness_score_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "sparse_only": RetrievalConfig(dense_recall_enabled=False, bm25_score_enabled=False, reranker_enabled=False, freshness_score_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "dense_sparse": RetrievalConfig(bm25_score_enabled=False, reranker_enabled=False, freshness_score_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_bm25_score": RetrievalConfig(reranker_enabled=False, freshness_score_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_reranker": RetrievalConfig(freshness_score_enabled=False, source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_freshness": RetrievalConfig(source_priority_enabled=False, conflict_resolution_enabled=False),
        "with_source_priority": RetrievalConfig(conflict_resolution_enabled=False),
        "with_conflict_resolution": RetrievalConfig(),
    }
    return {name: factory(config=config) for name, config in configs.items()}
