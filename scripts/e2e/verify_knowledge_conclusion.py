"""Offline schema, ownership, hard-fact, and action-boundary verifier."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.knowledge_conclusion.grounding import (
    GroundingError,
    ground_findings,
)
from app.application.knowledge_conclusion.run_service import canonical_hash
from app.domain.knowledge_conclusion import KnowledgeConclusion


def verify(conclusion: dict[str, Any], bundle: dict[str, Any], lineage: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = json.loads((ROOT / "contracts" / "knowledge-conclusion.v1.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(conclusion)
    parsed = KnowledgeConclusion.model_validate(conclusion)
    if parsed.content_bundle_id != bundle.get("bundle_id") or parsed.content_snapshot_id != bundle.get("content_snapshot_id"):
        raise ValueError("CONCLUSION_BUNDLE_BINDING_INVALID")
    ground_findings(bundle, parsed.findings)
    if lineage is not None:
        if lineage.get("bundle_id") != parsed.content_bundle_id or lineage.get("snapshot_id") != parsed.content_snapshot_id:
            raise ValueError("CONCLUSION_LINEAGE_BINDING_INVALID")
        if lineage.get("result_hash") not in {None, canonical_hash(parsed.model_dump(mode="json"))}:
            raise ValueError("CONCLUSION_HASH_INVALID")
        cited = {(row.get("knowledge_id"), row.get("evidence_id")) for row in lineage.get("findings", []) if isinstance(row, dict)}
        expected = {(knowledge_id, evidence_id) for finding in parsed.findings for knowledge_id in finding.knowledge_ids for evidence_id in finding.evidence_ids}
        if cited and cited != expected:
            raise ValueError("CONCLUSION_CITATION_INVALID")
    return {"contract": "knowledge-conclusion.v1", "conclusion_id": parsed.conclusion_id,
            "content_bundle_id": parsed.content_bundle_id, "content_snapshot_id": parsed.content_snapshot_id,
            "citation_precision": 1.0, "hard_fact_grounding": 1.0, "execution_eligible": False, "result": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conclusion", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--lineage", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        lineage = json.loads(args.lineage.read_text(encoding="utf-8")) if args.lineage else None
        report = verify(json.loads(args.conclusion.read_text(encoding="utf-8")), json.loads(args.bundle.read_text(encoding="utf-8")), lineage)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, ValueError, GroundingError) as exc:
        report = {"result": "FAIL", "code": getattr(exc, "code", str(exc).split(":", 1)[0])}
    output = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
