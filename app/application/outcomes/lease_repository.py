from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class OutcomeLease:
    decision_snapshot_id: str
    owner_id: str
    fencing_token: int
    lease_expires_at: datetime


class OutcomeLeaseRepository:
    """CAS-like lease repository; only one current owner can complete a run."""
    def __init__(self, *, persistent: bool = False):
        self._leases: dict[str, OutcomeLease] = {}
        self._results: dict[str, dict] = {}
        self._lock = Lock()
        self.persistent = persistent

    def claim(self, snapshot_id: str, owner_id: str, *, lease_seconds: int = 60) -> OutcomeLease | None:
        if self.persistent:
            return self._claim_persistent(snapshot_id, owner_id, lease_seconds=lease_seconds)
        now = datetime.now(UTC)
        with self._lock:
            old = self._leases.get(snapshot_id)
            if old and old.lease_expires_at > now and old.owner_id != owner_id:
                return None
            lease = OutcomeLease(snapshot_id, owner_id, (old.fencing_token + 1 if old else 1), now + timedelta(seconds=max(1, lease_seconds)))
            self._leases[snapshot_id] = lease
            self._persist_lease(lease)
            return lease

    def complete(self, lease: OutcomeLease, result: dict) -> dict:
        if self.persistent:
            return self._complete_persistent(lease, result)
        with self._lock:
            current = self._leases.get(lease.decision_snapshot_id)
            if current != lease or current.lease_expires_at <= datetime.now(UTC):
                raise RuntimeError("OUTCOME_LEASE_FENCED")
            final = self._results.setdefault(lease.decision_snapshot_id, dict(result))
            self._persist_result(lease, final)
            return final

    def result(self, snapshot_id: str) -> dict | None:
        value = self._results.get(snapshot_id)
        if value is not None:
            return value
        try:
            import json

            from sqlalchemy import text

            from storage.db import session_scope
            with session_scope() as session:
                raw = session.execute(text("SELECT result_json FROM decision_outcome_runs WHERE decision_snapshot_id=:snapshot"), {"snapshot": snapshot_id}).scalar_one_or_none()
            if raw:
                value = json.loads(raw)
                self._results[snapshot_id] = value
                return value
        except Exception:
            if self.persistent:
                raise
            return None
        return None

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value))

    def _claim_persistent(self, snapshot_id: str, owner_id: str, *, lease_seconds: int) -> OutcomeLease | None:
        from sqlalchemy import text

        from storage.db import session_scope

        now = datetime.now(UTC)
        expires = now + timedelta(seconds=max(1, lease_seconds))
        with session_scope() as session:
            updated = session.execute(text("""UPDATE decision_outcome_runs
                SET status='RUNNING', owner_id=:owner, fencing_token=fencing_token+1,
                    lease_expires_at=:expires
                WHERE decision_snapshot_id=:snapshot AND status <> 'SUCCEEDED'
                  AND (lease_expires_at IS NULL OR lease_expires_at <= :now OR owner_id=:owner)"""),
                {"snapshot": snapshot_id, "owner": owner_id, "expires": expires, "now": now})
            if getattr(updated, "rowcount", 0) == 1:
                token = int(session.execute(text("SELECT fencing_token FROM decision_outcome_runs WHERE decision_snapshot_id=:snapshot"), {"snapshot": snapshot_id}).scalar_one())
            else:
                inserted = session.execute(text("""INSERT INTO decision_outcome_runs(outcome_run_id,decision_snapshot_id,status,
                    owner_id,fencing_token,lease_expires_at) VALUES (:id,:snapshot,'RUNNING',:owner,1,:expires)
                    ON CONFLICT(decision_snapshot_id) DO NOTHING RETURNING fencing_token"""),
                    {"id": f"outcome:{snapshot_id}", "snapshot": snapshot_id, "owner": owner_id, "expires": expires}).scalar_one_or_none()
                if inserted is None:
                    return None
                token = int(inserted)
        return OutcomeLease(snapshot_id, owner_id, token, expires)

    def _complete_persistent(self, lease: OutcomeLease, result: dict) -> dict:
        import json

        from sqlalchemy import text

        from storage.db import session_scope

        now = datetime.now(UTC)
        with session_scope() as session:
            encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
            updated = session.execute(text("""UPDATE decision_outcome_runs SET status='SUCCEEDED', result_hash=:hash,
                result_json=:result WHERE decision_snapshot_id=:snapshot AND status='RUNNING'
                AND owner_id=:owner AND fencing_token=:token AND lease_expires_at > :now"""),
                {"snapshot": lease.decision_snapshot_id, "owner": lease.owner_id, "token": lease.fencing_token,
                 "result": encoded, "hash": __import__("hashlib").sha256(encoded.encode()).hexdigest(), "now": now})
            if getattr(updated, "rowcount", 0) == 0:
                prior = session.execute(text("SELECT status,result_json FROM decision_outcome_runs WHERE decision_snapshot_id=:snapshot"), {"snapshot": lease.decision_snapshot_id}).mappings().first()
                if prior and prior["status"] == "SUCCEEDED" and prior["result_json"]:
                    return json.loads(prior["result_json"])
                raise RuntimeError("OUTCOME_LEASE_FENCED")
            return dict(result)

    @staticmethod
    def _persist_lease(lease: OutcomeLease) -> None:
        try:
            from sqlalchemy import text

            from storage.db import session_scope
            with session_scope() as session:
                session.execute(text("""INSERT INTO decision_outcome_runs(outcome_run_id,decision_snapshot_id,status,owner_id,fencing_token,lease_expires_at)
                    VALUES (:id,:snapshot,'RUNNING',:owner,:token,:expires)
                    ON CONFLICT(decision_snapshot_id) DO UPDATE SET owner_id=:owner,fencing_token=:token,lease_expires_at=:expires"""), {"id": f"outcome:{lease.decision_snapshot_id}", "snapshot": lease.decision_snapshot_id, "owner": lease.owner_id, "token": lease.fencing_token, "expires": lease.lease_expires_at})
        except Exception:  # noqa: BLE001 - deterministic in-memory fallback for unit-only callers
            return

    @staticmethod
    def _persist_result(lease: OutcomeLease, result: dict) -> None:
        try:
            import json

            from sqlalchemy import text

            from storage.db import session_scope
            with session_scope() as session:
                session.execute(text("UPDATE decision_outcome_runs SET status='SUCCEEDED',result_json=:result WHERE decision_snapshot_id=:snapshot AND owner_id=:owner AND fencing_token=:token"), {"result": json.dumps(result, sort_keys=True, ensure_ascii=False), "snapshot": lease.decision_snapshot_id, "owner": lease.owner_id, "token": lease.fencing_token})
        except Exception:  # noqa: BLE001 - pure unit fixtures have no DB
            return
