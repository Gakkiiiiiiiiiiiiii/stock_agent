from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.replay.run_service import ReplayRunService


class DecisionReplayApplicationService:
    def __init__(self, runs: ReplayRunService | None = None):
        self.runs = runs or ReplayRunService()

    def exact(self, snapshot_id: str, snapshot: dict[str, Any], calculator: Callable[[dict[str, Any]], Any], expected_output_hash: str | None = None) -> dict[str, Any]:
        return self.runs.run_exact(decision_snapshot_id=snapshot_id, snapshot=snapshot, calculator=calculator, expected_output_hash=expected_output_hash)
