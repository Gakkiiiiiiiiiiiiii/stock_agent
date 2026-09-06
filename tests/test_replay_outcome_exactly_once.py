from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.application.decision.replay import DecisionReplayApplicationService
from app.application.outcomes.lease_repository import OutcomeLeaseRepository
from app.application.replay.run_service import (
    ReplayRun,
    ReplayRunService,
    _replay_identity,
)


def test_exact_replay_uses_only_fixed_snapshot_refs() -> None:
    snapshot = {
        "snapshot_id": "snapshot-fixed",
        "decision_id": "decision-fixed",
        "contract_ref": "decision.snapshot.v3@sha256:contract",
        "code_ref": "stock-agent@commit:abc123",
        "lock_ref": "requirements.lock@sha256:lock",
        "market": {"close": 10},
    }
    service = DecisionReplayApplicationService()
    first = service.exact("snapshot-fixed", snapshot)

    # Current state can change after the decision; it is not supplied to, or
    # read by, the fixed replay projection.
    current_state = {"market": {"close": 999}, "code_ref": "main"}
    current_state["market"]["close"] = -1
    second = service.exact("snapshot-fixed", snapshot)

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["replay_run_id"] == second["replay_run_id"]
    assert first["actual_output_hash"] == second["actual_output_hash"]


def test_outcome_same_event_payload_conflict_is_rejected() -> None:
    repository = OutcomeLeaseRepository()
    lease = repository.claim("outcome-event", "worker-a")
    assert lease is not None
    assert repository.complete(lease, {"return_pct": 0.1}) == {"return_pct": 0.1}
    assert repository.complete(lease, {"return_pct": 0.1}) == {"return_pct": 0.1}

    with pytest.raises(RuntimeError, match="OUTCOME_EVENT_PAYLOAD_CONFLICT"):
        repository.complete(lease, {"return_pct": 0.2})


def test_persistent_outcome_same_event_payload_conflict_is_rejected(isolated_database) -> None:
    repository = OutcomeLeaseRepository(persistent=True)
    lease = repository.claim("persistent-outcome-event", "worker-a")
    assert lease is not None
    repository.complete(lease, {"return_pct": 0.1})

    with pytest.raises(RuntimeError, match="OUTCOME_EVENT_PAYLOAD_CONFLICT"):
        repository.complete(lease, {"return_pct": 0.2})


def test_expired_replay_lease_recovers_and_fences_crashed_owner(isolated_database) -> None:
    snapshot = {"snapshot_version": "fixed", "market": {"close": 10}}
    crashed = ReplayRunService(persistent=True)
    lease = crashed._claim_persistent(
        ReplayRun(
            "crashed-run",
            "crash-recovery",
            _replay_identity(
                decision_snapshot_id="crash-recovery",
                snapshot=snapshot,
                expected_output_hash=None,
            ),
            owner_id=crashed.owner_id,
        )
    )
    assert lease is not None
    with isolated_database.begin() as connection:
        connection.execute(text("""UPDATE decision_replay_runs
            SET lease_expires_at=:expired WHERE decision_snapshot_id='crash-recovery'"""),
            {"expired": datetime(2000, 1, 1, tzinfo=UTC)})

    recovered = ReplayRunService(persistent=True).run_exact_fixed(
        decision_snapshot_id="crash-recovery", snapshot=snapshot
    )
    assert recovered["status"] == "SUCCEEDED"
    with pytest.raises(RuntimeError, match="REPLAY_RUN_FENCED"):
        ReplayRunService._persist(lease, strict=True)


def test_replay_identity_binds_snapshot_input_and_all_frozen_refs(isolated_database) -> None:
    base = {
        "snapshot_id": "snapshot-identity",
        "input": {"market": {"close": 10}},
        "profile_ref": "profile@sha256:one",
        "code_ref": "agent@commit:one",
        "contract_ref": "replay.v2@sha256:one",
        "lock_ref": "requirements.lock@sha256:one",
    }
    service = ReplayRunService(persistent=True)
    run_ids = set()
    for field, value in (
        ("input", {"market": {"close": 11}}),
        ("profile_ref", "profile@sha256:two"),
        ("code_ref", "agent@commit:two"),
        ("contract_ref", "replay.v2@sha256:two"),
        ("lock_ref", "requirements.lock@sha256:two"),
    ):
        snapshot = dict(base)
        snapshot[field] = value
        result = service.run_exact(
            decision_snapshot_id="snapshot-identity",
            snapshot=snapshot,
            calculator=lambda fixed: {"identity": fixed},
        )
        run_ids.add(result["replay_run_id"])
    assert len(run_ids) == 5


def test_replay_identity_conflict_is_rejected(isolated_database) -> None:
    snapshot = {"snapshot_version": "identity-conflict"}
    service = ReplayRunService(persistent=True)
    input_hash = _replay_identity(
        decision_snapshot_id="identity-conflict",
        snapshot=snapshot,
        expected_output_hash="sha256:first",
    )
    claimed = service._claim_persistent(
        ReplayRun("identity-first", "identity-conflict", input_hash, "sha256:first", owner_id=service.owner_id)
    )
    assert claimed is not None
    with pytest.raises(RuntimeError, match="REPLAY_IDENTITY_CONFLICT"):
        service._claim_persistent(
            ReplayRun("identity-second", "identity-conflict", input_hash, "sha256:other", owner_id=service.owner_id)
        )


def test_expiry_between_compute_and_commit_does_not_cache_ghost_success(isolated_database) -> None:
    snapshot = {"snapshot_version": "expire-between-compute-and-commit"}
    service = ReplayRunService(persistent=True)
    calls = []

    def expires_lease(_snapshot):
        calls.append("expired")
        input_hash = _replay_identity(
            decision_snapshot_id="expire-between-compute-and-commit",
            snapshot=snapshot,
            expected_output_hash=None,
        )
        with isolated_database.begin() as connection:
            connection.execute(text("""UPDATE decision_replay_runs SET lease_expires_at=:expired
                WHERE decision_snapshot_id=:snapshot AND input_hash=:input"""),
                {"expired": datetime(2000, 1, 1, tzinfo=UTC), "snapshot": "expire-between-compute-and-commit", "input": input_hash})
        return {"ok": True}

    with pytest.raises(RuntimeError, match="REPLAY_RUN_FENCED"):
        service.run_exact(
            decision_snapshot_id="expire-between-compute-and-commit",
            snapshot=snapshot,
            calculator=expires_lease,
        )
    assert all(run.status != "SUCCEEDED" for run in service._runs.values())

    retried = service.run_exact(
        decision_snapshot_id="expire-between-compute-and-commit",
        snapshot=snapshot,
        calculator=lambda _snapshot: calls.append("retried") or {"ok": True},
    )
    assert retried["status"] == "SUCCEEDED"
    assert calls == ["expired", "retried"]


def test_persistence_failure_is_not_cached_and_same_instance_can_retry(isolated_database, monkeypatch) -> None:
    snapshot = {"snapshot_version": "persistence-failure"}
    service = ReplayRunService(persistent=True)
    calls = []

    def fail_persist(_run, *, strict=False):
        assert strict is True
        raise RuntimeError("PERSISTENCE_UNAVAILABLE")

    monkeypatch.setattr(service, "_persist", fail_persist)
    with pytest.raises(RuntimeError, match="PERSISTENCE_UNAVAILABLE"):
        service.run_exact(
            decision_snapshot_id="persistence-failure",
            snapshot=snapshot,
            calculator=lambda _snapshot: calls.append("failed") or {"ok": True},
        )
    assert all(run.status != "SUCCEEDED" for run in service._runs.values())

    monkeypatch.setattr(service, "_persist", ReplayRunService._persist)
    retried = service.run_exact(
        decision_snapshot_id="persistence-failure",
        snapshot=snapshot,
        calculator=lambda _snapshot: calls.append("retried") or {"ok": True},
    )
    assert retried["status"] == "SUCCEEDED"
    assert calls == ["failed", "retried"]
