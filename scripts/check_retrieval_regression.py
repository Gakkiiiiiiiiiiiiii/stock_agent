from __future__ import annotations

import argparse
import json
from pathlib import Path

from engines.retrieval.evaluation.regression import compare_to_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail when deterministic retrieval metrics regress from the committed baseline.")
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.current.read_text(encoding="utf-8"))
    baseline_document = json.loads(args.baseline.read_text(encoding="utf-8"))
    violations = compare_to_baseline(current, baseline_document["metrics"])
    if violations:
        raise SystemExit("\n".join(violations))


if __name__ == "__main__":
    main()
