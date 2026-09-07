"""Fail-closed evidence scanner that never prints matched text or paths."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PATTERNS = (re.compile(rb"(?i)(?:authorization|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]"),
             re.compile(rb"(?i)https?://[^\s\"']+[?&](?:token|signature|sig|key|auth|cookie)="),
             re.compile(rb"(?i)(?:bearer\s+)[a-z0-9._~+/=-]{8,}"))


def scan(path: Path) -> dict[str, object]:
    if not path.is_dir():
        return {"result": "FAIL", "code": "EVIDENCE_DIRECTORY_INVALID", "matches": 0}
    matches = 0
    files = 0
    for item in sorted(path.rglob("*")):
        if item.is_symlink() or not item.is_file():
            return {"result": "FAIL", "code": "EVIDENCE_SYMLINK_OR_SPECIAL_FILE", "matches": 0}
        files += 1
        try:
            raw = item.read_bytes()
        except OSError:
            return {"result": "FAIL", "code": "EVIDENCE_READ_FAILED", "matches": 0}
        matches += sum(bool(pattern.search(raw)) for pattern in _PATTERNS)
    return {"result": "PASS" if matches == 0 else "FAIL", "code": "SECRET_SCAN_CLEAN" if matches == 0 else "SECRET_SCAN_FAILED", "matches": matches, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = scan(args.evidence_dir)
    output = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
