from __future__ import annotations

import argparse
from pathlib import Path

from engines.retrieval.evaluation.ablation_runner import RetrievalAblationRunner
from engines.retrieval.evaluation.fixture_corpus import build_fixture_hybrid_retriever
from engines.retrieval.evaluation.report import write_evaluation_report
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic HybridRetriever evaluation against the versioned fixture corpus.")
    parser.add_argument("--dataset", type=Path, default=Path("engines/retrieval/evaluation/datasets/golden_v1.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ablation", action="store_true")
    args = parser.parse_args()
    runner = RetrievalEvaluationRunner(build_fixture_hybrid_retriever())
    cases = runner.load_dataset(args.dataset)
    evaluation = runner.run(cases)
    write_evaluation_report(evaluation, args.output / "baseline")
    if args.ablation:
        from engines.retrieval.evaluation.ablation_runner import build_standard_ablation_variants

        variants = build_standard_ablation_variants(factory=build_fixture_hybrid_retriever)
        RetrievalAblationRunner(variants).run(cases, args.output / "ablation")


if __name__ == "__main__":
    main()
