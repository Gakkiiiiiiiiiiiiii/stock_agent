from __future__ import annotations

import argparse
from pathlib import Path

from engines.retrieval.evaluation.pipeline import DEFAULT_DATASET, run_fixture_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic HybridRetriever evaluation against the versioned fixture corpus.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ablation", action="store_true")
    args = parser.parse_args()
    run_fixture_evaluation(dataset=args.dataset, output_dir=args.output, ablation=args.ablation)


if __name__ == "__main__":
    main()
