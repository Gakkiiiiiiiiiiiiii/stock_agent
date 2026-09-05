"""Golden characterization for the split freeze boundary."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.decision_runtime import DecisionRuntime
from contracts.decision_input import build_bundle
from contracts.decision_snapshot import canonical_hash
from services.evidence.bundle import DecisionInputBundleBuilder


class _DecisionStore:
    def save_decision(self, **payload):
        return {"decision_id": payload["id"]}


class _SnapshotStore:
    def save_formal_segments(self, **_payload):
        return None

    def save_v3(self, snapshot):
        self.snapshot = snapshot
        return snapshot


class _EmptySpecialists:
    def run(self, **_kwargs):
        return []


GOLDEN_OUTPUTS = json.loads(
    (Path(__file__).parent / "fixtures" / "decision_runtime_golden_v3.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", range(100))
def test_split_formal_pipeline_preserves_legacy_golden_hashes(case: int):
    """One hundred frozen cases compare façade and extracted formal output."""
    decision_time = datetime(2026, 1, 2, 9, 30, tzinfo=UTC) + timedelta(minutes=case)
    values = {
        "bundle_id": f"golden-bundle-{case:03d}",
        "created_at": decision_time,
        "decision_time": decision_time,
        "task_type": "daily_market_decision",
        "objective": f"characterization-{case}",
        "subjects": [f"S{case:03d}.SH"],
        "query_context": {"skill": "daily-market-decision", "case": case, "trace_context": {"trace_id": f"t-{case:03d}", "decision_id": f"d-{case:03d}", "snapshot_id": f"s-{case:03d}"}},
        "strategy_context": {"horizon": "swing"},
    }
    legacy = build_bundle(**values)
    extracted = DecisionInputBundleBuilder().build(**values)
    assert extracted.bundle_hash == legacy.bundle_hash

    def calculate():
        snapshots = _SnapshotStore()
        runtime = DecisionRuntime(
            decision_service=_DecisionStore(), snapshot_repository=snapshots,
            clock=lambda: decision_time,
        )
        runtime.specialist_runner = _EmptySpecialists()
        return runtime.decide_from_frozen_bundle(
            bundle=extracted, task_type="daily_market_decision",
            objective=f"characterization-{case}", subjects=[f"S{case:03d}.SH"],
            context={"skill": "daily-market-decision"},
            decision_id=f"d-{case:03d}", snapshot_id=f"s-{case:03d}",
        ), snapshots.snapshot

    first, first_snapshot = calculate()
    second, second_snapshot = calculate()
    first_hashes = (
        canonical_hash(first["decision"]),
        first_snapshot.proposal["proposal_hash"],
        first_snapshot.policy["result_hash"],
        first_snapshot.snapshot_hash,
    )
    second_hashes = (
        canonical_hash(second["decision"]),
        second_snapshot.proposal["proposal_hash"],
        second_snapshot.policy["result_hash"],
        second_snapshot.snapshot_hash,
    )
    expected = GOLDEN_OUTPUTS[case]
    assert first_hashes == (
        expected["final"],
        expected["proposal"],
        expected["policy"],
        expected["snapshot"],
    )
    assert first_hashes == second_hashes
    assert all(len(value) == 64 for value in first_hashes)
