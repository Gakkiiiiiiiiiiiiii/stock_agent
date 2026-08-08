from __future__ import annotations

import json
from pathlib import Path


def write_evaluation_report(result: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "cases.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in result.get("cases") or []), encoding="utf-8")
    summary = result.get("summary") or {}
    lines = ["# Retrieval evaluation", "", "| Metric | Value |", "|---|---:|"]
    lines.extend(f"| {key} | {float(value):.4f} |" for key, value in summary.items())
    path = output_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
