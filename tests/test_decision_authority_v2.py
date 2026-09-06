from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.adapters.postgres.decision_repository import SessionDecisionRepository
from app.adapters.postgres.session_outbox import SessionDecisionOutbox
from app.application.decision.commands import CreateDecisionCommand
from app.application.decision.unit_of_work import DecisionUnitOfWork
from app.application.outcomes.lease_repository import OutcomeLeaseRepository
from app.application.replay.run_service import ReplayRunService
from app.domain.decision.execution_authorization import FormalDecisionFinalizer
from app.domain.decision.run import DecisionRun, DecisionRunState
from app.ports.decision_repository import (
    IdempotencyConflict,
    InMemoryDecisionRepository,
)
from app.ports.outbox import InMemoryOutbox
from storage.db import SessionLocal


def _finalized():
    run = DecisionRun("decision", "request")
    for state in (DecisionRunState.BUNDLE_FROZEN, DecisionRunState.SPECIALISTS_COMPLETED,
                  DecisionRunState.FORMAL_CALCULATED, DecisionRunState.GOVERNED,
                  DecisionRunState.FINALIZED):
        run.transition(state)
    return run


def test_uow_idempotency_and_single_finalization_event():
    uow = DecisionUnitOfWork.in_memory()
    command = CreateDecisionCommand("portfolio", "request-1", {"objective": "hold"})
    first = uow.receive(portfolio_id=command.portfolio_id, idempotency_key=command.idempotency_key, request_hash=command.request_hash)
    again = uow.receive(portfolio_id=command.portfolio_id, idempotency_key=command.idempotency_key, request_hash=command.request_hash)
    assert first.decision_id == again.decision_id
    with pytest.raises(IdempotencyConflict):
        uow.receive(portfolio_id=command.portfolio_id, idempotency_key=command.idempotency_key, request_hash="different")
    uow.freeze_bundle({}, bundle_id="bundle", bundle_hash="hash")
    for state in (DecisionRunState.SPECIALISTS_COMPLETED, DecisionRunState.FORMAL_CALCULATED, DecisionRunState.GOVERNED):
        uow.advance(state)
    uow.finalize(final_result={}, snapshot_id="snapshot", event_payload={})
    assert len(uow.outbox.events) == 1
    assert uow.run.execution_eligible


def test_uow_reverses_staged_outbox_when_final_save_fails_then_retries():
    """A failure after outbox staging cannot create a duplicate final effect."""
    class FailingFinalSaveRepository(InMemoryDecisionRepository):
        fail_final_save = True

        def save(self, run, *, expected_version=None):
            if self.fail_final_save and run.state is DecisionRunState.FINALIZED:
                raise RuntimeError("FINAL_SAVE_FAILED")
            return super().save(run, expected_version=expected_version)

    repository = FailingFinalSaveRepository()
    uow = DecisionUnitOfWork(repository, InMemoryOutbox())
    run = uow.receive(portfolio_id="portfolio", idempotency_key="request", request_hash="payload")
    uow.freeze_bundle({}, bundle_id="bundle", bundle_hash="hash")
    for state in (DecisionRunState.SPECIALISTS_COMPLETED, DecisionRunState.FORMAL_CALCULATED, DecisionRunState.GOVERNED):
        uow.advance(state)

    with pytest.raises(RuntimeError, match="FINAL_SAVE_FAILED"):
        uow.finalize(final_result={"decision_id": run.decision_id}, snapshot_id="snapshot", event_payload={})

    assert repository.get(run.decision_id).state is DecisionRunState.GOVERNED
    assert uow.outbox.events == {}

    repository.fail_final_save = False
    finalized = uow.finalize(final_result={"decision_id": run.decision_id}, snapshot_id="snapshot", event_payload={})
    assert finalized.state is DecisionRunState.FINALIZED
    assert len(uow.outbox.events) == 1


def test_authorization_requires_finalized_governed_lineage():
    lineage = {"trace_id": "t", "request_id": "r", "decision_bundle_id": "b", "decision_id": "d",
               "decision_snapshot_id": "s", "market_snapshot_id": "m", "factor_artifact_id": "f",
               "content_snapshot_id": "c", "portfolio_id": "p", "policy_version": "v", "producer_commit": "g"}
    with pytest.raises(ValueError, match="DECISION_NOT_FINALIZED"):
        FormalDecisionFinalizer().finalize(decision=DecisionRun("d", "r"), snapshot_id="s", governance=type("G", (), {"approved": True})(), policy_version="v", lineage=lineage, valid_until=datetime.now(UTC) + timedelta(minutes=1), portfolio_id="p", contract_checksums={"formal": "sha256:abc"})
    envelope = FormalDecisionFinalizer().finalize(decision=_finalized(), snapshot_id="s", governance=type("G", (), {"approved": True})(), policy_version="v", lineage=lineage, valid_until=datetime.now(UTC) + timedelta(minutes=1), portfolio_id="p", contract_checksums={"formal": "sha256:abc"})
    assert envelope.authority == "FORMAL"


def test_authorization_constructor_and_non_approved_actions_are_closed():
    values = {
        "envelope_id": "e", "decision_id": "d", "decision_snapshot_id": "s", "portfolio_id": "p",
        "allowed_actions": ("BUY",), "max_notional": 1, "valid_until": datetime.now(UTC) + timedelta(minutes=1),
        "contract_checksums": {"formal": "sha256:abc"},
    }
    from app.domain.decision.execution_authorization import (
        ExecutionAuthorizationEnvelope,
    )

    with pytest.raises(TypeError, match="FINALIZER_ONLY"):
        ExecutionAuthorizationEnvelope(**values)
    with pytest.raises(TypeError, match="FINALIZER_ONLY"):
        ExecutionAuthorizationEnvelope.model_construct(**values)
    with pytest.raises(AttributeError):
        ExecutionAuthorizationEnvelope._from_finalizer(**values)
    lineage = {"trace_id": "t", "request_id": "r", "decision_bundle_id": "b", "decision_id": "d",
               "decision_snapshot_id": "s", "market_snapshot_id": "m", "factor_artifact_id": "f",
               "content_snapshot_id": "c", "portfolio_id": "p", "policy_version": "v", "producer_commit": "g"}
    for action in ("HOLD", "VETO"):
        with pytest.raises(ValueError, match="EXECUTION_NOT_ELIGIBLE"):
            FormalDecisionFinalizer().finalize(
                decision={"decision_id": "d", "state": "FINALIZED", "investment_action": action},
                snapshot_id="s", governance={"approved": True}, policy_version="v", lineage=lineage,
                valid_until=datetime.now(UTC) + timedelta(minutes=1), portfolio_id="p",
                contract_checksums={"formal": "sha256:abc"}, allowed_actions=("BUY",), max_notional=1,
            )


def test_issued_authorization_cannot_be_mutated_with_model_copy():
    lineage = {"trace_id": "t", "request_id": "r", "decision_bundle_id": "b", "decision_id": "d",
               "decision_snapshot_id": "s", "market_snapshot_id": "m", "factor_artifact_id": "f",
               "content_snapshot_id": "c", "portfolio_id": "p", "policy_version": "v", "producer_commit": "g"}
    envelope = FormalDecisionFinalizer().finalize(
        decision={"decision_id": "d", "state": "FINALIZED", "investment_action": "BUY"},
        snapshot_id="s", governance={"approved": True}, policy_version="v", lineage=lineage,
        valid_until=datetime.now(UTC) + timedelta(minutes=1), portfolio_id="p",
        contract_checksums={"formal": "sha256:abc"}, allowed_actions=("BUY",), max_notional=1,
    )
    with pytest.raises(TypeError, match="EXECUTION_AUTHORIZATION_IMMUTABLE"):
        envelope.model_copy(update={"max_notional": 999})


def test_outcome_lease_fencing_and_idempotency():
    repository = OutcomeLeaseRepository()
    owner = repository.claim("snapshot", "worker-a", lease_seconds=60)
    assert owner is not None
    assert repository.claim("snapshot", "worker-b", lease_seconds=60) is None
    assert repository.complete(owner, {"result": "ok"}) == {"result": "ok"}
    with pytest.raises(RuntimeError, match="OUTCOME_EVENT_PAYLOAD_CONFLICT"):
        repository.complete(owner, {"result": "different"})


def test_session_uow_failure_rolls_back_run_and_outbox(isolated_database):
    """A failed finalization must not expose a partially finalized formal run."""
    from app.adapters.postgres.decision_repository import SessionDecisionRepository
    from storage.db import SessionLocal

    class BrokenOutbox:
        def enqueue(self, **_kwargs):
            raise RuntimeError("OUTBOX_WRITE_FAILED")

    with SessionLocal() as session:
        uow = DecisionUnitOfWork(SessionDecisionRepository(session), BrokenOutbox())
        run = uow.receive(portfolio_id="p", idempotency_key="k", request_hash="h")
        uow.freeze_bundle({"snapshot": "s"}, bundle_id="b", bundle_hash="bh", readiness_snapshot={"ready": True})
        for state in (DecisionRunState.SPECIALISTS_COMPLETED, DecisionRunState.FORMAL_CALCULATED, DecisionRunState.GOVERNED):
            uow.advance(state)
        with pytest.raises(RuntimeError, match="OUTBOX_WRITE_FAILED"):
            uow.finalize(final_result={"decision_id": run.decision_id}, snapshot_id="s", event_payload={})
        session.rollback()

    with isolated_database.connect() as connection:
        row = connection.execute(text("SELECT state FROM decision_runs WHERE decision_id=:id"), {"id": run.decision_id}).scalar_one_or_none()
        assert row is None
        assert connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one() == 0


def test_formal_route_is_idempotent_and_gate_precedes_runtime(isolated_database, monkeypatch):
    from app.routers import decision as decision_router

    calls = []

    class Runtime:
        def freeze_bundle(self, **kwargs):
            calls.append(("freeze", kwargs))
            from contracts.decision_input import DecisionInputBundle
            return DecisionInputBundle(
                created_at=kwargs["as_of"], decision_time=kwargs["as_of"], task_type=kwargs["task_type"],
                objective=kwargs["objective"], subjects=kwargs.get("subjects") or [],
                query_context={"trace_context": {"trace_id": "trace-route", "snapshot_id": "snapshot-route"}},
            )

        def decide_from_frozen_bundle(self, **kwargs):
            calls.append(("calculate", kwargs))
            decision_id = kwargs["decision_id"]
            return {
                "decision_id": decision_id,
                "snapshot_id": "snapshot-route",
                "bundle_id": "bundle-route",
                "bundle": {"bundle_id": "bundle-route", "snapshot_id": "snapshot-route"},
                "decision": {"investment_action": "HOLD"},
                "trace_id": "trace-route",
                "lineage": {"trace_id": "trace-route", "market_snapshot_id": "m-route", "factor_artifact_id": "f-route", "content_snapshot_id": "c-route", "policy_version": "p-route", "producer_commit": "commit-route"},
            }

    monkeypatch.setenv("FORMAL_IDEMPOTENCY_REQUIRED", "1")
    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (True, []))
    monkeypatch.setattr("app.dependencies.orchestrator", SimpleNamespace(runtime=Runtime()))
    request = decision_router.DecisionV2Request(
        task_type="analysis", objective="stable", as_of=datetime(2026, 1, 1, tzinfo=UTC),
        portfolio_id="p-route", idempotency_key="same-key",
    )
    first = decision_router.create_decision_v2(request)
    second = decision_router.create_decision_v2(request)
    assert first["decision_id"] == second["decision_id"]
    assert first == second
    assert [kind for kind, _ in calls] == ["freeze", "calculate"]

    with pytest.raises(Exception) as conflict:
        decision_router.create_decision_v2(request.model_copy(update={"objective": "different"}))
    assert "IDEMPOTENCY_KEY_CONFLICT" in str(conflict.value)

    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (False, ["CONTRACT_MISMATCH"]))
    with pytest.raises(Exception, match="FORMAL_DECISION_NOT_READY"):
        decision_router.create_decision_v2(request.model_copy(update={"idempotency_key": "blocked"}))
    assert [kind for kind, _ in calls] == ["freeze", "calculate"]


def test_formal_route_rejects_missing_idempotency_before_gate_or_runtime(monkeypatch):
    from app.routers import decision as decision_router

    calls = []

    class Runtime:
        def decide(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("formal runtime must not run without an idempotency key")

    monkeypatch.delenv("STOCK_AGENT_OFFLINE_MODE", raising=False)
    monkeypatch.setattr("app.dependencies.orchestrator", SimpleNamespace(runtime=Runtime()))
    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (_ for _ in ()).throw(AssertionError("gate must not run")))
    request = decision_router.DecisionV2Request(
        task_type="analysis", objective="missing key", as_of=datetime(2026, 1, 1, tzinfo=UTC),
        portfolio_id="p-missing",
    )
    with pytest.raises(Exception, match="IDEMPOTENCY_KEY_REQUIRED"):
        decision_router.create_decision_v2(request)
    assert calls == []


def test_persistent_outcome_lease_has_fencing_and_result_identity(isolated_database):
    first = OutcomeLeaseRepository(persistent=True)
    lease = first.claim("persistent-snapshot", "worker-a", lease_seconds=60)
    assert lease is not None
    second = OutcomeLeaseRepository(persistent=True)
    assert second.claim("persistent-snapshot", "worker-b", lease_seconds=60) is None
    assert first.complete(lease, {"market_result": "fixed"}) == {"market_result": "fixed"}
    assert second.result("persistent-snapshot") == {"market_result": "fixed"}
    with isolated_database.connect() as connection:
        row = connection.execute(text("SELECT owner_id, fencing_token, status, result_json FROM decision_outcome_runs WHERE decision_snapshot_id='persistent-snapshot'")).one()
    assert row[0] == "worker-a"
    assert row[1] == lease.fencing_token
    assert row[2] == "SUCCEEDED"
    assert '"market_result": "fixed"' in row[3]


def test_persistent_outcome_lease_expiry_allows_recovery_and_fences_stale_owner(isolated_database):
    from sqlalchemy import text

    repository = OutcomeLeaseRepository(persistent=True)
    old = repository.claim("recoverable-snapshot", "worker-a", lease_seconds=60)
    assert old is not None
    with isolated_database.begin() as connection:
        connection.execute(text("UPDATE decision_outcome_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE decision_snapshot_id='recoverable-snapshot'"))
    recovered = repository.claim("recoverable-snapshot", "worker-b", lease_seconds=60)
    assert recovered is not None
    assert recovered.fencing_token == old.fencing_token + 1
    with pytest.raises(RuntimeError, match="OUTCOME_LEASE_FENCED"):
        repository.complete(old, {"stale": True})


def test_persistent_outcome_claim_is_single_owner_under_threads(isolated_database):
    barrier = Barrier(2)
    calls = 0
    calls_lock = Lock()

    def worker(owner_id):
        nonlocal calls
        repository = OutcomeLeaseRepository(persistent=True)
        barrier.wait(timeout=5)
        lease = repository.claim("concurrent-outcome", owner_id, lease_seconds=60)
        if lease is None:
            return None
        with calls_lock:
            calls += 1
        return repository.complete(lease, {"market_result_snapshot": "fixed"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ("outcome-a", "outcome-b")))
    assert calls == 1
    assert sum(result is not None for result in results) == 1


def test_persistent_replay_claim_is_single_calculator_under_threads(isolated_database):
    snapshot = {"snapshot_version": "fixed-concurrent", "market": {"close": 12}}
    barrier = Barrier(2)
    calls = 0
    calls_lock = Lock()

    def worker(owner_suffix):
        nonlocal calls
        service = ReplayRunService(persistent=True)
        # Distinct service owners model distinct worker processes.
        service.owner_id = f"replay-{owner_suffix}"
        barrier.wait(timeout=5)
        def calculate(value):
            nonlocal calls
            with calls_lock:
                calls += 1
            return {"close": value["market"]["close"]}
        return service.run_exact(decision_snapshot_id="concurrent-replay", snapshot=snapshot, calculator=calculate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ("a", "b")))
    assert calls == 1
    assert all(result.get("status") in {"SUCCEEDED", "LEASE_BUSY"} for result in results)
    followup = ReplayRunService(persistent=True).run_exact(
        decision_snapshot_id="concurrent-replay", snapshot=snapshot,
        calculator=lambda _value: {"should_not": "run"},
    )
    assert followup["status"] == "SUCCEEDED"
    assert calls == 1


def test_persistent_exact_replay_keys_expected_hash_and_keeps_each_result(isolated_database):
    snapshot = {"snapshot_version": "fixed-v1", "market": {"close": 10}}
    first = ReplayRunService(persistent=True).run_exact(
        decision_snapshot_id="snapshot-replay", snapshot=snapshot,
        calculator=lambda fixed: {"action": "HOLD", "close": fixed["market"]["close"]},
        expected_output_hash="sha256:wrong",
    )
    second = ReplayRunService(persistent=True).run_exact(
        decision_snapshot_id="snapshot-replay", snapshot=snapshot,
        calculator=lambda _fixed: {"action": "BUY"}, expected_output_hash=None,
    )
    assert first["replay_run_id"] != second["replay_run_id"]
    assert first["discrepancy_artifact_id"] is not None
    assert second["discrepancy_artifact_id"] is None
    with isolated_database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM decision_replay_runs WHERE decision_snapshot_id='snapshot-replay'")).scalar_one() == 2


def test_persistent_replay_claim_precedes_calculator(isolated_database):
    snapshot = {"snapshot_version": "fixed-v2", "market": {"close": 11}}
    first = ReplayRunService(persistent=True)
    second = ReplayRunService(persistent=True)
    calls = []

    from app.application.replay.run_service import ReplayRun, _replay_identity

    # Hold the first owner's lease while the second worker attempts its claim.
    lease = first._claim_persistent(
        ReplayRun(
            "run",
            "claim-snapshot",
            _replay_identity(
                decision_snapshot_id="claim-snapshot",
                snapshot=snapshot,
                expected_output_hash=None,
            ),
            owner_id=first.owner_id,
        )
    )
    assert lease is not None
    # The active row proves the conditional claim implementation is persistent,
    # not process-local.
    result = second.run_exact(decision_snapshot_id="claim-snapshot", snapshot=snapshot, calculator=lambda value: calls.append(value) or {"ok": True})
    assert result["status"] == "LEASE_BUSY"
    assert calls == []


def test_formal_route_uses_finalizer_for_positive_approved_action(isolated_database, monkeypatch):
    from app.routers import decision as decision_router

    # The suite defaults to fail-closed offline mode.  This characterization
    # test intentionally exercises the positive formal path, so opt out of the
    # offline fixture explicitly rather than weakening production safeguards.
    monkeypatch.delenv("STOCK_AGENT_OFFLINE_MODE", raising=False)
    monkeypatch.delenv("STOCK_AGENT_DETERMINISTIC_FIXTURE", raising=False)

    class Runtime:
        def freeze_bundle(self, **kwargs):
            from contracts.decision_input import DecisionInputBundle
            return DecisionInputBundle(
                created_at=kwargs["as_of"], decision_time=kwargs["as_of"], task_type=kwargs["task_type"],
                objective=kwargs["objective"], subjects=kwargs.get("subjects") or [],
                query_context={"trace_context": {"trace_id": "trace-approved", "snapshot_id": "snapshot-approved"}},
            )

        def decide_from_frozen_bundle(self, **kwargs):
            return {
                "decision_id": kwargs["decision_id"], "snapshot_id": kwargs["snapshot_id"],
                "bundle_id": kwargs["bundle"].bundle_id, "bundle": {"id": kwargs["bundle"].bundle_id},
                "decision": {"investment_action": "BUY", "target_weight": 0.25},
                "trace_id": "trace-approved",
                "lineage": {"trace_id": "trace-approved", "market_snapshot_id": "m-approved", "factor_artifact_id": "f-approved", "content_snapshot_id": "c-approved", "policy_version": "p-approved", "producer_commit": "commit-approved"},
            }

    monkeypatch.setenv("FORMAL_IDEMPOTENCY_REQUIRED", "1")
    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (True, []))
    monkeypatch.setattr(
        decision_router.DecisionSnapshotRepository,
        "get_v3_for_decision",
        lambda self, _decision_id: SimpleNamespace(policy={"approved": True}, runtime={"trace_id": "trace-approved"}, lineage=[]),
    )
    monkeypatch.setattr("app.dependencies.orchestrator", SimpleNamespace(runtime=Runtime()))
    response = decision_router.create_decision_v2(decision_router.DecisionV2Request(
        task_type="analysis", objective="approved", as_of=datetime(2027, 1, 1, tzinfo=UTC),
        portfolio_id="p-approved", idempotency_key="approved-key",
    ))
    assert response["status"] == "APPROVED"
    assert response["execution_eligible"] is True
    assert response["authorization_envelope"] is not None
    with SessionLocal() as session:
        finalized = SessionDecisionRepository(session).get(response["decision_id"])
        assert finalized is not None
        assert finalized.state is DecisionRunState.FINALIZED
        assert finalized.final_response_json == response
        assert finalized.execution_authorization_json == response["authorization_envelope"]
        assert session.execute(
            text("SELECT COUNT(*) FROM outbox WHERE aggregate_id=:id"),
            {"id": response["decision_id"]},
        ).scalar_one() == 1


def test_runtime_frozen_entry_delegates_application_orchestrator():
    from app.application.decision.assembler import EvidenceBundleAssembler
    from app.decision_runtime import DecisionRuntime

    bundle = EvidenceBundleAssembler().freeze(market={"id": "m"}, factor={"id": "f"}, content={"id": "c"}, lineage={"trace_id": "t"})
    calls = []

    class Application:
        def calculate_from_frozen(self, **kwargs):
            calls.append(kwargs)
            return {"status": "HOLD", "bundle": kwargs["bundle"]}

    runtime = DecisionRuntime(application_service=Application())
    result = runtime.decide_from_frozen_bundle(
        bundle=bundle, task_type="daily-market-decision", objective="replay", decision_id="d", snapshot_id="s",
    )
    assert calls and calls[0]["bundle"] == bundle
    assert result["decision_id"] == "d"


@pytest.mark.parametrize("failure_point", ["lineage", "outbox"])
def test_formal_route_finalization_failures_preserve_frozen_run(isolated_database, monkeypatch, failure_point):
    import hashlib
    import json

    from app import dependencies
    from app.routers import decision as decision_router
    from contracts.decision_input import DecisionInputBundle

    calls = {"freeze": 0, "calculate": 0}

    class Runtime:
        def freeze_bundle(self, **kwargs):
            calls["freeze"] += 1
            return DecisionInputBundle(
                created_at=kwargs["as_of"], decision_time=kwargs["as_of"], task_type=kwargs["task_type"],
                objective=kwargs["objective"], query_context={"trace_context": {"trace_id": "trace-failure", "snapshot_id": "snapshot-failure"}},
            )

        def decide_from_frozen_bundle(self, **kwargs):
            calls["calculate"] += 1
            return {"decision_id": kwargs["decision_id"], "snapshot_id": "snapshot-failure", "bundle_id": kwargs["bundle"].bundle_id, "decision": {"investment_action": "HOLD"}}

    request = decision_router.DecisionV2Request(task_type="analysis", objective="failure injection", as_of=datetime(2027, 1, 1, tzinfo=UTC), portfolio_id="failure-portfolio", idempotency_key=f"failure-{failure_point}")
    lineage = {"trace_id": "trace-failure", "request_id": "request-failure", "decision_bundle_id": "bundle-failure", "decision_id": "decision-failure", "decision_snapshot_id": "snapshot-failure", "market_snapshot_id": "market-failure", "factor_artifact_id": "factor-failure", "content_snapshot_id": "content-failure", "portfolio_id": request.portfolio_id, "policy_version": "policy-failure", "producer_commit": "commit-failure"}

    def formalize(*, result, request, run):
        del result
        persisted_lineage = {**lineage, "decision_id": run.decision_id, "decision_bundle_id": run.bundle_id}
        return ({"contract": "formal-decision.v2", "authority": "FORMAL", "decision_id": run.decision_id, "decision_snapshot_id": "snapshot-failure", "decision_bundle_id": run.bundle_id, "portfolio_id": request.portfolio_id, "status": "HOLD", "execution_eligible": False, "valid_until": "2027-01-02T00:00:00Z", "lineage": persisted_lineage}, persisted_lineage, None)

    monkeypatch.setattr(decision_router, "_formalize_frozen_result", formalize)
    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (True, [], {"ready": True}))
    monkeypatch.setattr(dependencies, "orchestrator", SimpleNamespace(runtime=Runtime()))
    graph = dependencies.lineage_graph
    original_record = graph.record
    original_enqueue = SessionDecisionOutbox.enqueue
    failed = {"value": True}
    if failure_point == "lineage":
        def fail_record(*args, **kwargs):
            if failed["value"]:
                failed["value"] = False
                raise RuntimeError("LINEAGE_WRITE_FAILED")
            return original_record(*args, **kwargs)
        monkeypatch.setattr(graph, "record", fail_record)
    else:
        def fail_enqueue(self, **kwargs):
            if failed["value"]:
                failed["value"] = False
                raise RuntimeError("OUTBOX_WRITE_FAILED")
            return original_enqueue(self, **kwargs)
        monkeypatch.setattr(SessionDecisionOutbox, "enqueue", fail_enqueue)
    with pytest.raises(RuntimeError):
        decision_router.create_decision_v2(request)
    request_hash = hashlib.sha256(json.dumps(request.model_dump(mode="json", exclude={"idempotency_key"}), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    with SessionLocal() as session:
        frozen = SessionDecisionRepository(session).get_or_create(portfolio_id=request.portfolio_id, idempotency_key=request.idempotency_key, request_hash=request_hash)
        assert frozen.state is DecisionRunState.BUNDLE_FROZEN
        frozen_bundle_id, frozen_bundle_hash = frozen.bundle_id, frozen.bundle_hash
        assert frozen.final_response_json is None and frozen.lineage_json is None
        assert frozen.execution_authorization_json is None
        assert session.execute(text("SELECT COUNT(*) FROM outbox WHERE aggregate_id=:id"), {"id": frozen.decision_id}).scalar_one() == 0
        assert session.execute(text("SELECT COUNT(*) FROM decision_lineage WHERE decision_id=:id"), {"id": frozen.decision_id}).scalar_one() == 0
    result = decision_router.create_decision_v2(request)
    assert result["status"] == "HOLD"
    assert calls["freeze"] == 1
    assert calls["calculate"] == 2
    from app.routers.audit import get_lineage

    assert get_lineage(frozen.decision_id)["decision_id"] == frozen.decision_id
    with SessionLocal() as session:
        finalized = SessionDecisionRepository(session).get(frozen.decision_id)
        assert finalized is not None
        assert finalized.state is DecisionRunState.FINALIZED
        assert finalized.bundle_id == frozen_bundle_id
        assert finalized.bundle_hash == frozen_bundle_hash
        assert finalized.final_response_json is not None
        assert session.execute(text("SELECT COUNT(*) FROM outbox WHERE aggregate_id=:id"), {"id": frozen.decision_id}).scalar_one() == 1
        assert session.execute(text("SELECT COUNT(*) FROM decision_lineage WHERE decision_id=:id"), {"id": frozen.decision_id}).scalar_one() == 1


def test_fifty_concurrent_formal_creates_share_one_final_response(isolated_database, monkeypatch):
    import json

    from app import dependencies
    from app.routers import decision as decision_router
    from contracts.decision_input import DecisionInputBundle

    calls = {"freeze": 0, "calculate": 0}
    calls_lock = Lock()

    class Runtime:
        def freeze_bundle(self, **kwargs):
            with calls_lock:
                calls["freeze"] += 1
            return DecisionInputBundle(
                created_at=kwargs["as_of"], decision_time=kwargs["as_of"],
                task_type=kwargs["task_type"], objective=kwargs["objective"],
                query_context={"trace_context": {"trace_id": "trace-concurrent", "snapshot_id": "snapshot-concurrent"}},
            )

        def decide_from_frozen_bundle(self, **kwargs):
            with calls_lock:
                calls["calculate"] += 1
            return {"decision_id": kwargs["decision_id"], "snapshot_id": "snapshot-concurrent",
                    "bundle_id": kwargs["bundle"].bundle_id, "decision": {"investment_action": "HOLD"}}

    def formalize(*, result, request, run):
        del result
        lineage = {
            "trace_id": "trace-concurrent", "request_id": run.request_id,
            "decision_bundle_id": run.bundle_id, "decision_id": run.decision_id,
            "decision_snapshot_id": "snapshot-concurrent", "market_snapshot_id": "market-concurrent",
            "factor_artifact_id": "factor-concurrent", "content_snapshot_id": "content-concurrent",
            "portfolio_id": request.portfolio_id, "policy_version": "policy-concurrent",
            "producer_commit": "commit-concurrent",
        }
        response = {
            "contract": "formal-decision.v2", "authority": "FORMAL", "decision_id": run.decision_id,
            "decision_snapshot_id": "snapshot-concurrent", "decision_bundle_id": run.bundle_id,
            "portfolio_id": request.portfolio_id, "status": "HOLD", "execution_eligible": False,
            "valid_until": "2027-01-02T00:00:00Z", "lineage": lineage,
        }
        return response, lineage, None

    request = decision_router.DecisionV2Request(
        task_type="analysis", objective="concurrent formal", as_of=datetime(2027, 1, 1, tzinfo=UTC),
        portfolio_id="concurrent-formal-portfolio", idempotency_key="concurrent-formal-key",
    )
    monkeypatch.setattr(decision_router, "_formal_gate", lambda: (True, [], {"ready": True}))
    monkeypatch.setattr(decision_router, "_formalize_frozen_result", formalize)
    monkeypatch.setattr(dependencies, "orchestrator", SimpleNamespace(runtime=Runtime()))

    def create(_: int):
        return decision_router.create_decision_v2(request)

    with ThreadPoolExecutor(max_workers=50) as executor:
        responses = list(executor.map(create, range(50)))

    assert len({item["decision_id"] for item in responses}) == 1
    assert len({json.dumps(item, sort_keys=True) for item in responses}) == 1
    assert calls == {"freeze": 1, "calculate": 1}
    decision_id = responses[0]["decision_id"]
    with SessionLocal() as session:
        assert session.execute(text("SELECT COUNT(*) FROM decision_runs WHERE decision_id=:id"), {"id": decision_id}).scalar_one() == 1
        assert session.execute(text("SELECT COUNT(*) FROM outbox WHERE aggregate_id=:id"), {"id": decision_id}).scalar_one() == 1
        assert session.execute(text("SELECT COUNT(*) FROM decision_lineage WHERE decision_id=:id"), {"id": decision_id}).scalar_one() == 1


def test_fifty_concurrent_same_key_claims_one_decision(isolated_database):
    """The database claim, rather than request timing, owns idempotency."""
    portfolio_id = "concurrent-portfolio"
    idempotency_key = "concurrent-key"
    request_hash = "concurrent-request-hash"
    barrier = Barrier(50)

    def claim(_: int) -> str:
        barrier.wait(timeout=15)
        for attempt in range(40):
            try:
                with SessionLocal() as session:
                    repository = SessionDecisionRepository(session)
                    run = repository.get_or_create(
                        portfolio_id=portfolio_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                    session.commit()
                    return run.decision_id
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 39:
                    raise
                sleep(0.01)
        raise AssertionError("claim retry loop exhausted")

    with ThreadPoolExecutor(max_workers=50) as executor:
        decision_ids = list(executor.map(claim, range(50)))

    assert len(set(decision_ids)) == 1
    with SessionLocal() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM decision_requests WHERE portfolio_id=:portfolio_id AND idempotency_key=:idempotency_key"),
            {"portfolio_id": portfolio_id, "idempotency_key": idempotency_key},
        ).scalar_one() == 1
        assert session.execute(
            text("SELECT COUNT(*) FROM decision_runs WHERE decision_id=:decision_id"),
            {"decision_id": decision_ids[0]},
        ).scalar_one() == 1
