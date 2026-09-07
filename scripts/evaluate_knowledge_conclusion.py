"""Run the local, frozen knowledge-conclusion Golden evaluator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A direct ``python scripts/...`` invocation has ``scripts`` rather than the
# repository root on ``sys.path``. Keep the CLI self-contained and offline.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.knowledge_conclusion.evaluation import evaluate_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen knowledge-conclusion Golden fixtures.")
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/knowledge_conclusion/manifest.json"))
    parser.add_argument("--report", type=Path, help="Optional local JSON report path.")
    args = parser.parse_args()
    result = evaluate_manifest(args.manifest)
    encoded = json.dumps(result.report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if args.report:
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
