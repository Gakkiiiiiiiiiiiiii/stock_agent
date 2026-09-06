from __future__ import annotations

from app.application.replay.run_service import ReplayRunService


class DecisionReplayApplicationService:
    def __init__(self, runs: ReplayRunService | None = None):
        self.runs = runs or ReplayRunService()

    def exact(self, snapshot_id: str, snapshot: dict[str, object], expected_output_hash: str | None = None) -> dict[str, object]:
        """Run exact replay from the persisted snapshot only.

        Application callers cannot inject a calculator that observes current
        market state; the run service owns the deterministic projection.
        """
        return self.runs.run_exact_fixed(
            decision_snapshot_id=snapshot_id,
            snapshot=snapshot,
            expected_output_hash=expected_output_hash,
        )
