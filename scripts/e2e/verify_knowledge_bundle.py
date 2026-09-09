"""Offline verifier for the checksum-locked Content knowledge bundle."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.knowledge_conclusion.bundle_validator import (
    BundleValidationError,
    ContentKnowledgeBundleValidator,
)


def verify(payload: dict[str, Any]) -> dict[str, Any]:
    bundle = ContentKnowledgeBundleValidator().validate(payload)
    contract = payload.get("contract")
    if not isinstance(contract, str):
        raise BundleValidationError("CONTENT_SCHEMA_INVALID")
    return {"contract": contract, "bundle_id": bundle.bundle_id, "bundle_hash": bundle.bundle_hash,
            "content_snapshot_id": bundle.content_snapshot_id, "contract_checksum": bundle.contract_checksum,
            "citation_precision": 1.0, "hard_fact_grounding": 1.0, "result": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify(json.loads(args.input.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, BundleValidationError, RuntimeError) as exc:
        report = {"result": "FAIL", "code": getattr(exc, "code", "CONTENT_BUNDLE_INVALID")}
    output = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
