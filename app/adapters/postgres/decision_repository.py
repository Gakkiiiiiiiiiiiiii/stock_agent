"""SQLAlchemy adapter for the decision work-unit tables.

The adapter contains persistence only; state transition validation remains in
``app.domain.decision.run``.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.domain.decision.run import DecisionRun, DecisionRunState
from app.ports.decision_repository import IdempotencyConflict
from storage.db import session_scope


class PostgresDecisionRepository:
    def get_or_create(self, *, portfolio_id: str, idempotency_key: str, request_hash: str) -> DecisionRun:
        request_id, decision_id = str(uuid.uuid4()), str(uuid.uuid4())
        try:
            with session_scope() as session:
                row = session.execute(text("""
                    INSERT INTO decision_requests(request_id, portfolio_id, idempotency_key, request_hash)
                    VALUES (:request_id, :portfolio_id, :idempotency_key, :request_hash)
                    ON CONFLICT (portfolio_id, idempotency_key) DO NOTHING
                    RETURNING request_id
                """), locals()).mappings().first()
                if row is None:
                    old = session.execute(text("SELECT request_id, request_hash FROM decision_requests WHERE portfolio_id=:portfolio_id AND idempotency_key=:idempotency_key"), locals()).mappings().one()
                    if old["request_hash"] != request_hash:
                        raise IdempotencyConflict("IDEMPOTENCY_KEY_CONFLICT")
                    request_id = str(old["request_id"])
                    existing = session.execute(text("SELECT * FROM decision_runs WHERE request_id=:request_id"), {"request_id": request_id}).mappings().first()
                    if existing:
                        return self._from_row(existing)
                session.execute(text("INSERT INTO decision_runs(decision_id, request_id, state) VALUES (:decision_id, :request_id, 'RECEIVED')"), locals())
                return DecisionRun(decision_id=decision_id, request_id=request_id)
        except IdempotencyConflict as exc:  # noqa: TRY203 - preserve typed conflict boundary
            raise exc  # noqa: TRY201
    def save(self, run: DecisionRun, *, expected_version: int | None = None) -> DecisionRun:
        values = {"decision_id": run.decision_id, "state": run.state.value, "bundle_id": run.bundle_id,
                  "bundle_hash": run.bundle_hash, "formal_result_hash": run.formal_result_hash,
                  "governance_hash": run.governance_hash,
                  "final_response_json": json.dumps(run.final_response_json, sort_keys=True, ensure_ascii=False) if run.final_response_json is not None else None,
                  "final_response_hash": run.final_response_hash,
                  "lineage_json": json.dumps(run.lineage_json, sort_keys=True, ensure_ascii=False) if run.lineage_json is not None else None,
                  "execution_authorization_json": json.dumps(run.execution_authorization_json, sort_keys=True, ensure_ascii=False) if run.execution_authorization_json is not None else None,
                  "snapshot_id": run.snapshot_id,
                  "readiness_snapshot_json": json.dumps(run.readiness_snapshot, sort_keys=True) if run.readiness_snapshot is not None else None,
                  "version": run.version, "last_error_code": run.last_error_code, "updated_at": run.updated_at}
        condition = "AND version=:expected_version" if expected_version is not None else ""
        if expected_version is not None:
            values["expected_version"] = expected_version
        with session_scope() as session:
            result = session.execute(text(f"""UPDATE decision_runs SET state=:state, decision_bundle_id=:bundle_id,
                bundle_hash=:bundle_hash, formal_result_hash=:formal_result_hash, governance_hash=:governance_hash,
                final_response_json=:final_response_json, final_response_hash=:final_response_hash, lineage_json=:lineage_json,
                execution_authorization_json=:execution_authorization_json,
                snapshot_id=:snapshot_id, readiness_snapshot_json=:readiness_snapshot_json, version=:version, last_error_code=:last_error_code, updated_at=:updated_at
                WHERE decision_id=:decision_id {condition}"""), values)
            if result.rowcount != 1:
                raise RuntimeError("DECISION_RUN_VERSION_CONFLICT")
        return run

    def get(self, decision_id: str) -> DecisionRun | None:
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM decision_runs WHERE decision_id=:decision_id"), {"decision_id": decision_id}).mappings().first()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: Any) -> DecisionRun:
        return DecisionRun(decision_id=str(row["decision_id"]), request_id=str(row["request_id"]),
                           state=DecisionRunState(row["state"]), version=int(row.get("version") or 0),
                           bundle_id=row.get("decision_bundle_id"), bundle_hash=row.get("bundle_hash"),
                           formal_result_hash=row.get("formal_result_hash"), governance_hash=row.get("governance_hash"),
                           final_response_json=json.loads(row["final_response_json"]) if row.get("final_response_json") else None,
                           final_response_hash=row.get("final_response_hash"),
                           lineage_json=json.loads(row["lineage_json"]) if row.get("lineage_json") else None,
                           execution_authorization_json=json.loads(row["execution_authorization_json"]) if row.get("execution_authorization_json") else None,
                           snapshot_id=row.get("snapshot_id"), last_error_code=row.get("last_error_code"),
                           updated_at=row.get("updated_at") or datetime.now(UTC))


class SessionDecisionRepository:
    """Session-bound variant used by formal request transactions."""
    def __init__(self, session: Any):
        self.session = session

    def get_or_create(self, *, portfolio_id: str, idempotency_key: str, request_hash: str) -> DecisionRun:
        request_id, decision_id = str(uuid.uuid4()), str(uuid.uuid4())
        row = self.session.execute(text("""INSERT INTO decision_requests(request_id, portfolio_id, idempotency_key, request_hash)
            VALUES (:request_id,:portfolio_id,:idempotency_key,:request_hash)
            ON CONFLICT (portfolio_id,idempotency_key) DO NOTHING RETURNING request_id"""), locals()).mappings().first()
        if row is None:
            old = self.session.execute(text("SELECT request_id,request_hash FROM decision_requests WHERE portfolio_id=:portfolio_id AND idempotency_key=:idempotency_key"), locals()).mappings().one()
            if old["request_hash"] != request_hash:
                raise IdempotencyConflict("IDEMPOTENCY_KEY_CONFLICT")
            existing = self.session.execute(text("SELECT * FROM decision_runs WHERE request_id=:request_id"), {"request_id": str(old["request_id"])}).mappings().first()
            if existing:
                return PostgresDecisionRepository._from_row(existing)
            request_id = str(old["request_id"])
        self.session.execute(text("INSERT INTO decision_runs(decision_id,request_id,state) VALUES (:decision_id,:request_id,'RECEIVED')"), locals())
        return DecisionRun(decision_id=decision_id, request_id=request_id)

    def save(self, run: DecisionRun, *, expected_version: int | None = None) -> DecisionRun:
        values = {"decision_id": run.decision_id, "state": run.state.value, "bundle_id": run.bundle_id,
                  "bundle_hash": run.bundle_hash, "formal_result_hash": run.formal_result_hash,
                  "governance_hash": run.governance_hash,
                  "final_response_json": json.dumps(run.final_response_json, sort_keys=True, ensure_ascii=False) if run.final_response_json is not None else None,
                  "final_response_hash": run.final_response_hash,
                  "lineage_json": json.dumps(run.lineage_json, sort_keys=True, ensure_ascii=False) if run.lineage_json is not None else None,
                  "execution_authorization_json": json.dumps(run.execution_authorization_json, sort_keys=True, ensure_ascii=False) if run.execution_authorization_json is not None else None,
                  "snapshot_id": run.snapshot_id,
                  "readiness_snapshot_json": json.dumps(run.readiness_snapshot, sort_keys=True) if run.readiness_snapshot is not None else None,
                  "version": run.version, "last_error_code": run.last_error_code, "updated_at": run.updated_at,
                  "expected_version": expected_version}
        predicate = " AND version=:expected_version" if expected_version is not None else ""
        result = self.session.execute(text(f"""UPDATE decision_runs SET state=:state,decision_bundle_id=:bundle_id,bundle_hash=:bundle_hash,
            formal_result_hash=:formal_result_hash,governance_hash=:governance_hash,final_response_json=:final_response_json,
            final_response_hash=:final_response_hash,lineage_json=:lineage_json,execution_authorization_json=:execution_authorization_json,snapshot_id=:snapshot_id,
            readiness_snapshot_json=:readiness_snapshot_json,version=:version,last_error_code=:last_error_code,updated_at=:updated_at
            WHERE decision_id=:decision_id{predicate}"""), values)
        if result.rowcount != 1:
            raise RuntimeError("DECISION_RUN_VERSION_CONFLICT")
        return run

    def get(self, decision_id: str) -> DecisionRun | None:
        row = self.session.execute(text("SELECT * FROM decision_runs WHERE decision_id=:decision_id"), {"decision_id": decision_id}).mappings().first()
        return PostgresDecisionRepository._from_row(row) if row else None

    def lock(self, decision_id: str) -> None:
        """Acquire the decision row lock and keep it until this session commits.

        PostgreSQL uses a row lock.  SQLite has no ``FOR UPDATE`` equivalent,
        so an immediate write transaction provides the same single-owner
        boundary for the short formal finalization transaction.
        """
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            self.session.execute(
                text("SELECT decision_id FROM decision_runs WHERE decision_id=:decision_id"),
                {"decision_id": decision_id},
            ).first()
            return
        self.session.execute(
            text("SELECT decision_id FROM decision_runs WHERE decision_id=:decision_id FOR UPDATE"),
            {"decision_id": decision_id},
        ).first()
