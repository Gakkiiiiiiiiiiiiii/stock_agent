"""PostgreSQL evidence for formal decision and replay safety invariants.

These tests deliberately do not invoke production migrations.  Each test run
gets a private schema with the narrow table set used by the persistence ports,
then drops only that schema during fixture teardown.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, close_all_sessions

from app.adapters.postgres.decision_repository import SessionDecisionRepository
from app.adapters.postgres.session_outbox import SessionDecisionOutbox
from app.application.decision.unit_of_work import DecisionUnitOfWork
from app.application.outcomes.lease_repository import OutcomeLeaseRepository
from app.application.replay.run_service import (
    ReplayRun,
    ReplayRunService,
    _replay_identity,
)
from app.domain.decision.run import DecisionRunState
from storage.db import SessionLocal

POSTGRES_DSN = "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/stock_agent"
pytestmark = pytest.mark.integration

_TABLES = """
CREATE TABLE decision_requests (
  request_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (portfolio_id, idempotency_key)
);
CREATE TABLE decision_runs (
  decision_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES decision_requests(request_id),
  state TEXT NOT NULL,
  decision_bundle_id TEXT,
  bundle_hash TEXT,
  formal_result_hash TEXT,
  governance_hash TEXT,
  final_response_json TEXT,
  final_response_hash TEXT,
  lineage_json TEXT,
  execution_authorization_json TEXT,
  snapshot_id TEXT,
  readiness_snapshot_json TEXT,
  version INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE outbox (
  event_id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  published_at TIMESTAMP
);
CREATE TABLE decision_replay_runs (
  replay_run_id TEXT PRIMARY KEY,
  decision_snapshot_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  expected_output_hash TEXT,
  actual_output_hash TEXT,
  status TEXT NOT NULL,
  discrepancy_artifact_id TEXT,
  owner_id TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TIMESTAMP,
  UNIQUE (decision_snapshot_id, input_hash)
);
CREATE TABLE decision_outcome_runs (
  outcome_run_id TEXT PRIMARY KEY,
  decision_snapshot_id TEXT NOT NULL,
  result_hash TEXT,
  result_json TEXT,
  status TEXT NOT NULL,
  owner_id TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  lease_expires_at TIMESTAMP,
  UNIQUE (decision_snapshot_id)
);
"""


@pytest.fixture
def postgres_safety_schema() -> Engine:
    """Bind persistence ports to a random, disposable PostgreSQL schema."""
    schema = f"sa_safe_05_{uuid4().hex}"
    original_bind = SessionLocal.kw.get("bind")
    admin_engine = create_engine(POSTGRES_DSN, future=True, connect_args={"connect_timeout": 5})
    try:
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL safety evidence unavailable: {exc}")
    test_engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for statement in _TABLES.split(";"):
                if statement.strip():
                    connection.execute(text(statement))
        test_engine = create_engine(
            POSTGRES_DSN,
            future=True,
            connect_args={"connect_timeout": 5, "options": f"-csearch_path={schema}"},
        )
        SessionLocal.configure(bind=test_engine)
        yield test_engine
    finally:
        close_all_sessions()
        SessionLocal.configure(bind=original_bind)
        if test_engine is not None:
            test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _governed_uow(session: Session, *, suffix: str, outbox: SessionDecisionOutbox | None = None) -> DecisionUnitOfWork:
    uow = DecisionUnitOfWork(SessionDecisionRepository(session), outbox or SessionDecisionOutbox(session))
    uow.receive(portfolio_id=f"portfolio-{suffix}", idempotency_key=f"key-{suffix}", request_hash="request-hash")
    uow.freeze_bundle({"snapshot": suffix}, bundle_id=f"bundle-{suffix}", bundle_hash="bundle-hash")
    for state in (
        DecisionRunState.SPECIALISTS_COMPLETED,
        DecisionRunState.FORMAL_CALCULATED,
        DecisionRunState.GOVERNED,
    ):
        uow.advance(state)
    return uow


def test_postgres_decision_uow_and_outbox_commit_or_rollback_together(postgres_safety_schema: Engine) -> None:
    with SessionLocal.begin() as session:
        uow = _governed_uow(session, suffix="commit")
        finalized = uow.finalize(
            final_result={"decision_id": uow.run.decision_id},
            snapshot_id="snapshot-commit",
            event_payload={"decision_id": uow.run.decision_id},
        )

    with postgres_safety_schema.connect() as connection:
        assert connection.execute(
            text("SELECT state FROM decision_runs WHERE decision_id=:id"), {"id": finalized.decision_id}
        ).scalar_one() == "FINALIZED"
        assert connection.execute(
            text("SELECT COUNT(*) FROM outbox WHERE aggregate_id=:id"), {"id": finalized.decision_id}
        ).scalar_one() == 1

    class FailingOutbox(SessionDecisionOutbox):
        def enqueue(self, **kwargs: object) -> None:
            super().enqueue(**kwargs)
            raise RuntimeError("OUTBOX_WRITE_FAILED")

    with pytest.raises(RuntimeError, match="OUTBOX_WRITE_FAILED"), SessionLocal.begin() as session:
        _governed_uow(session, suffix="rollback", outbox=FailingOutbox(session)).finalize(
            final_result={"status": "final"}, snapshot_id="snapshot-rollback", event_payload={}
        )
    with postgres_safety_schema.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM decision_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one() == 1


def test_postgres_outcome_payload_conflict_and_fencing(postgres_safety_schema: Engine) -> None:
    repository = OutcomeLeaseRepository(persistent=True)
    lease = repository.claim("outcome-conflict", "worker-a")
    assert lease is not None
    assert repository.complete(lease, {"return_pct": 0.1}) == {"return_pct": 0.1}
    with pytest.raises(RuntimeError, match="OUTCOME_EVENT_PAYLOAD_CONFLICT"):
        repository.complete(lease, {"return_pct": 0.2})

    stale = repository.claim("outcome-fence", "worker-a")
    assert stale is not None
    with postgres_safety_schema.begin() as connection:
        connection.execute(
            text("UPDATE decision_outcome_runs SET lease_expires_at=:expired WHERE decision_snapshot_id='outcome-fence'"),
            {"expired": datetime(2000, 1, 1, tzinfo=UTC)},
        )
    recovered = OutcomeLeaseRepository(persistent=True).claim("outcome-fence", "worker-b")
    assert recovered is not None
    assert recovered.fencing_token == stale.fencing_token + 1
    with pytest.raises(RuntimeError, match="OUTCOME_LEASE_FENCED"):
        repository.complete(stale, {"stale": True})


def test_postgres_replay_identity_lease_and_commit_before_cache(postgres_safety_schema: Engine) -> None:
    base = {"snapshot_id": "identity", "input": {"close": 10}, "profile_ref": "profile@one"}
    service = ReplayRunService(persistent=True)
    run_ids = {
        service.run_exact(
            decision_snapshot_id="identity",
            snapshot={**base, field: value},
            calculator=lambda fixed: {"identity": fixed},
        )["replay_run_id"]
        for field, value in (("input", {"close": 11}), ("profile_ref", "profile@two"))
    }
    assert len(run_ids) == 2

    identity = _replay_identity(
        decision_snapshot_id="identity-conflict", snapshot=base, expected_output_hash="sha256:first"
    )
    claimed = service._claim_persistent(
        ReplayRun("identity-first", "identity-conflict", identity, "sha256:first", owner_id=service.owner_id)
    )
    assert claimed is not None
    with pytest.raises(RuntimeError, match="REPLAY_IDENTITY_CONFLICT"):
        service._claim_persistent(
            ReplayRun("identity-second", "identity-conflict", identity, "sha256:other", owner_id=service.owner_id)
        )

    snapshot = {"snapshot_version": "expire-before-commit"}

    def expire_lease(_fixed: dict) -> dict:
        input_hash = _replay_identity(
            decision_snapshot_id="expire-before-commit", snapshot=snapshot, expected_output_hash=None
        )
        with postgres_safety_schema.begin() as connection:
            connection.execute(
                text("UPDATE decision_replay_runs SET lease_expires_at=:expired WHERE decision_snapshot_id=:id AND input_hash=:hash"),
                {"expired": datetime(2000, 1, 1, tzinfo=UTC), "id": "expire-before-commit", "hash": input_hash},
            )
        return {"computed": True}

    with pytest.raises(RuntimeError, match="REPLAY_RUN_FENCED"):
        service.run_exact(
            decision_snapshot_id="expire-before-commit", snapshot=snapshot, calculator=expire_lease
        )
    expire_identity = _replay_identity(
        decision_snapshot_id="expire-before-commit", snapshot=snapshot, expected_output_hash=None
    )
    assert service._runs[("expire-before-commit", expire_identity)].status != "SUCCEEDED"
    retried = service.run_exact(
        decision_snapshot_id="expire-before-commit", snapshot=snapshot, calculator=lambda _fixed: {"retried": True}
    )
    assert retried["status"] == "SUCCEEDED"
