"""诊断报告输出(9.9 / 19.4):report.json 机器可读。"""

from __future__ import annotations

from pathlib import Path

from ..models import DownloadReport


def write_report(report: DownloadReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_report(path: Path) -> DownloadReport:
    import json

    return DownloadReport(**json.loads(path.read_text(encoding="utf-8")))
