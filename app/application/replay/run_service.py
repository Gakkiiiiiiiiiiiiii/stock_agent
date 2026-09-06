"""Deterministic snapshot replay run service with idempotent results."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def _replay_identity(
    *,
    decision_snapshot_id: str,
    snapshot: dict[str, Any],
    expected_output_hash: str | None,
) -> str:
    """Return the immutable identity used for replay idempotency.

    ``snapshot`` is deliberately included as a whole rather than as a short
    allow-list.  It consequently binds the frozen input and every recorded
    profile, code, contract, and lock reference, including future reference
    fields.  The expected hash must also participate: a prior discrepancy for
    one expectation is not evidence for another expectation.
    """
    return _digest(
        {
            "decision_snapshot_id": decision_snapshot_id,
            "snapshot": snapshot,
            "expected_output_hash": expected_output_hash,
        }
    )


@dataclass(frozen=True)
class ReplayRun:
    replay_run_id: str
    decision_snapshot_id: str
    input_hash: str
    expected_output_hash: str | None = None
    actual_output_hash: str | None = None
    status: str = "RUNNING"
    discrepancy_artifact_id: str | None = None
    owner_id: str | None = None
    fencing_token: int = 0
    lease_expires_at: datetime | None = None


class ReplayRunService:
    def __init__(self, *, persistent: bool = False):
        self._runs: dict[tuple[str, str], ReplayRun] = {}
        self._lock = Lock()
        self.persistent = persistent
        self.owner_id = f"replay-{uuid4()}"

    def run_exact(self, *, decision_snapshot_id: str, snapshot: dict[str, Any], calculator: Callable[[dict[str, Any]], Any], expected_output_hash: str | None = None) -> dict[str, Any]:
        # The existing persistence key remains (snapshot_id, input_hash), but
        # input_hash now represents the complete immutable replay identity.
        input_hash = _replay_identity(
            decision_snapshot_id=decision_snapshot_id,
            snapshot=snapshot,
            expected_output_hash=expected_output_hash,
        )
        key = (decision_snapshot_id, input_hash)
        with self._lock:
            prior = self._runs.get(key)
            if prior is None:
                prior = self._load_persistent(
                    decision_snapshot_id,
                    input_hash,
                    expected_output_hash=expected_output_hash,
                    strict=self.persistent,
                )
                if prior is not None:
                    self._runs[key] = prior
            if prior and prior.status == "SUCCEEDED":
                return {"replay_run_id": prior.replay_run_id, "status": prior.status, "match": prior.discrepancy_artifact_id is None, "actual_output_hash": prior.actual_output_hash, "discrepancy_artifact_id": prior.discrepancy_artifact_id}
            run = prior or ReplayRun(str(uuid4()), decision_snapshot_id, input_hash, expected_output_hash, owner_id=self.owner_id, fencing_token=1)
            if self.persistent:
                run = replace(run, owner_id=self.owner_id)
                claimed = self._claim_persistent(run)
                if claimed is None:
                    return {"replay_run_id": run.replay_run_id, "status": "LEASE_BUSY", "match": False}
                run = claimed
            self._runs[key] = run
        # calculator receives only persisted snapshot; caller controls a pure
        # calculator and cannot accidentally fetch current upstream state.
        output = calculator(snapshot)
        actual = _digest(output)
        mismatch = expected_output_hash is not None and actual != expected_output_hash
        done = replace(run, actual_output_hash=actual, expected_output_hash=expected_output_hash,
                       status="SUCCEEDED", discrepancy_artifact_id=(str(uuid4()) if mismatch else None))
        # A fenced write is the authority for success.  In particular, do not
        # let a process-local cache turn an expired lease or failed transaction
        # into a success response on this process's retry path.
        if self.persistent:
            self._persist(done, strict=True)
        with self._lock:
            self._runs[key] = done
        return {"replay_run_id": done.replay_run_id, "status": done.status, "match": not mismatch, "actual_output_hash": actual, "discrepancy_artifact_id": done.discrepancy_artifact_id, "output": output}

    def run_exact_fixed(
        self,
        *,
        decision_snapshot_id: str,
        snapshot: dict[str, Any],
        expected_output_hash: str | None = None,
    ) -> dict[str, Any]:
        """Strict public EXACT entrypoint.

        Callers provide only the immutable snapshot.  The calculator is an
        internal deterministic projection, so a router cannot smuggle in a
        callable that reaches current upstream data or an LLM.
        """
        return self.run_exact(
            decision_snapshot_id=decision_snapshot_id,
            snapshot=snapshot,
            calculator=self._fixed_projection,
            expected_output_hash=expected_output_hash,
        )

    @staticmethod
    def _fixed_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "decision_id": snapshot.get("decision_id"),
            "snapshot_hash": _digest(snapshot),
            "calculation_version": "exact-replay.v1",
        }

    @staticmethod
    def _claim_persistent(run: ReplayRun) -> ReplayRun | None:
        """Portable conditional lease claim used before invoking calculator."""
        from sqlalchemy import text

        from storage.db import session_scope

        now = datetime.now(UTC)
        expires = now + timedelta(seconds=60)
        with session_scope() as session:
            updated = session.execute(text("""UPDATE decision_replay_runs SET status='RUNNING', owner_id=:owner,
                fencing_token=fencing_token+1, lease_expires_at=:expires
                WHERE decision_snapshot_id=:snapshot AND input_hash=:input AND status <> 'SUCCEEDED'
                  AND ((expected_output_hash = :expected) OR (expected_output_hash IS NULL AND :expected IS NULL))
                  AND (lease_expires_at IS NULL OR lease_expires_at <= :now OR owner_id=:owner)"""),
                {"snapshot": run.decision_snapshot_id, "input": run.input_hash, "expected": run.expected_output_hash,
                 "owner": run.owner_id, "expires": expires, "now": now})
            if getattr(updated, "rowcount", 0) == 1:
                token = int(session.execute(text("""SELECT fencing_token FROM decision_replay_runs
                    WHERE decision_snapshot_id=:snapshot AND input_hash=:input"""), {"snapshot": run.decision_snapshot_id, "input": run.input_hash}).scalar_one())
            else:
                inserted = session.execute(text("""INSERT INTO decision_replay_runs(replay_run_id,decision_snapshot_id,input_hash,
                    expected_output_hash,status,owner_id,fencing_token,lease_expires_at)
                    VALUES (:id,:snapshot,:input,:expected,'RUNNING',:owner,1,:expires)
                    ON CONFLICT(decision_snapshot_id,input_hash) DO NOTHING RETURNING fencing_token"""),
                    {"id": run.replay_run_id, "snapshot": run.decision_snapshot_id, "input": run.input_hash,
                     "expected": run.expected_output_hash, "owner": run.owner_id, "expires": expires}).scalar_one_or_none()
                if inserted is None:
                    stored_expected = session.execute(text("""SELECT expected_output_hash FROM decision_replay_runs
                        WHERE decision_snapshot_id=:snapshot AND input_hash=:input"""),
                        {"snapshot": run.decision_snapshot_id, "input": run.input_hash}).scalar_one_or_none()
                    if stored_expected != run.expected_output_hash:
                        raise RuntimeError("REPLAY_IDENTITY_CONFLICT")
                    return None
                token = int(inserted)
        return replace(run, fencing_token=token, lease_expires_at=expires, status="RUNNING")

    @staticmethod
    def _load_persistent(
        snapshot_id: str,
        input_hash: str,
        *,
        expected_output_hash: str | None,
        strict: bool = False,
    ) -> ReplayRun | None:
        try:
            from sqlalchemy import text

            from storage.db import session_scope
            with session_scope() as session:
                row = session.execute(text("SELECT * FROM decision_replay_runs WHERE decision_snapshot_id=:snapshot_id AND input_hash=:input_hash"), {"snapshot_id": snapshot_id, "input_hash": input_hash}).mappings().first()
            if row:
                if row.get("expected_output_hash") != expected_output_hash:
                    raise RuntimeError("REPLAY_IDENTITY_CONFLICT")
                return ReplayRun(str(row["replay_run_id"]), snapshot_id, input_hash, row.get("expected_output_hash"), row.get("actual_output_hash"), row["status"], row.get("discrepancy_artifact_id"), row.get("owner_id"), int(row.get("fencing_token") or 0), row.get("lease_expires_at"))
        except Exception:
            if strict:
                raise
            return None
        return None

    @staticmethod
    def _persist(run: ReplayRun, *, strict: bool = False) -> None:
        try:
            from sqlalchemy import text

            from storage.db import session_scope
            with session_scope() as session:
                persisted = session.execute(text("""INSERT INTO decision_replay_runs(replay_run_id,decision_snapshot_id,input_hash,expected_output_hash,actual_output_hash,status,discrepancy_artifact_id,owner_id,fencing_token,lease_expires_at)
                    VALUES (:id,:snapshot,:input,:expected,:actual,:status,:discrepancy,:owner,:token,:expires)
                    ON CONFLICT(decision_snapshot_id,input_hash) DO UPDATE SET actual_output_hash=:actual,status=:status,discrepancy_artifact_id=:discrepancy
                    WHERE decision_replay_runs.status <> 'SUCCEEDED'
                      AND decision_replay_runs.owner_id=:owner AND decision_replay_runs.fencing_token=:token
                      AND decision_replay_runs.lease_expires_at > CURRENT_TIMESTAMP"""), {"id": run.replay_run_id, "snapshot": run.decision_snapshot_id, "input": run.input_hash, "expected": run.expected_output_hash, "actual": run.actual_output_hash, "status": run.status, "discrepancy": run.discrepancy_artifact_id, "owner": run.owner_id, "token": run.fencing_token, "expires": run.lease_expires_at})
                if getattr(persisted, "rowcount", 0) != 1:
                    raise RuntimeError("REPLAY_RUN_FENCED")
        except Exception:
            if strict:
                raise
