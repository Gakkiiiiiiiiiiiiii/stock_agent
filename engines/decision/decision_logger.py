from __future__ import annotations

from datetime import datetime


def build_decision_log(task_type: str, tools_called: list[str], output_summary: str, confidence_score: float) -> dict:
    return {
        "decision_time": datetime.utcnow().isoformat(),
        "task_type": task_type,
        "tools_called": tools_called,
        "output_summary": output_summary,
        "confidence_score": confidence_score,
    }

