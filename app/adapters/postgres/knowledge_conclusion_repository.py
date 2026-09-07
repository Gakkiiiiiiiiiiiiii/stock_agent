"""SQLAlchemy persistence adapter for the isolated knowledge conclusion run."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.domain.knowledge_conclusion import (
    KnowledgeConclusion,
    KnowledgeConclusionRequest,
)
from app.domain.knowledge_conclusion_lineage import (
    KnowledgeConclusionLineageAudit,
    LineageFinding,
)
from app.domain.knowledge_conclusion_run import (
    FrozenBundle,
    KnowledgeConclusionAuditMetadata,
    KnowledgeConclusionCitation,
    KnowledgeConclusionRun,
    KnowledgeConclusionRunState,
)
from app.ports.knowledge_conclusion_repository import (
    ABSENT_SEAL_DIGEST,
    KnowledgeConclusionFenced,
    KnowledgeConclusionIdempotencyConflict,
)
from storage.db import session_scope


def _json(value: Any | None) -> str | None:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str) if value is not None else None


def _seal_digest_json(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(
        json.dumps(json.loads(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class PostgresKnowledgeConclusionRepository:
    """Uses CAS version checks; it issues no schema DDL at runtime."""
    def reserve(self, run: KnowledgeConclusionRun) -> KnowledgeConclusionRun:
        values = self._values(run)
        with session_scope() as session:
            inserted = session.execute(text("""INSERT INTO knowledge_conclusion_run(
                conclusion_id,idempotency_key,request_hash,raw_request_json,raw_request_hash,effective_request_hash,request_json,policy_version,state,version,audit_metadata_json,created_at,updated_at)
                VALUES (:conclusion_id,:idempotency_key,:request_hash,:raw_request_json,:raw_request_hash,:effective_request_hash,:request_json,:policy_version,:state,:version,:audit_metadata_json,:created_at,:updated_at)
                ON CONFLICT(idempotency_key) DO NOTHING RETURNING conclusion_id"""), values).scalar_one_or_none()
            if inserted is not None:
                return run
            old = session.execute(text("SELECT * FROM knowledge_conclusion_run WHERE idempotency_key=:idempotency_key"), values).mappings().one()
            if old["raw_request_hash"] != run.raw_request_hash:
                raise KnowledgeConclusionIdempotencyConflict("IDEMPOTENCY_KEY_CONFLICT")
            return self._from_row(old)

    def get(self, conclusion_id: str) -> KnowledgeConclusionRun | None:
        with session_scope() as session:
            row = session.execute(text("SELECT * FROM knowledge_conclusion_run WHERE conclusion_id=:id"), {"id": conclusion_id}).mappings().first()
        return self._from_row(row) if row else None

    def save(self, run: KnowledgeConclusionRun, *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        values = self._values(run)
        values["expected_version"] = expected_version
        with session_scope() as session:
            self._assert_seal_digest(session, run.conclusion_id, expected_seal_digest)
            changed = session.execute(text("""UPDATE knowledge_conclusion_run SET
                state=:state,version=:version,frozen_bundle_id=:frozen_bundle_id,frozen_bundle_hash=:frozen_bundle_hash,
                frozen_bundle_json=:frozen_bundle_json,producer_sha=:producer_sha,contract_checksum=:contract_checksum,
                model_request_id=:model_request_id,model_provider_idempotency_key=:model_provider_idempotency_key,
                sealed_model_response_json=:sealed_model_response_json,audit_metadata_json=:audit_metadata_json,error_code=:error_code,updated_at=:updated_at
                WHERE conclusion_id=:conclusion_id AND version=:expected_version"""), values)
            if changed.rowcount != 1:
                raise KnowledgeConclusionFenced("KNOWLEDGE_CONCLUSION_RUN_FENCED")
        return run

    def commit_result(self, run: KnowledgeConclusionRun, citations: Sequence[KnowledgeConclusionCitation], *, expected_version: int, expected_seal_digest: str | None = None) -> KnowledgeConclusionRun:
        values = self._values(run)
        values["expected_version"] = expected_version
        with session_scope() as session:
            self._assert_seal_digest(session, run.conclusion_id, expected_seal_digest)
            changed = session.execute(text("""UPDATE knowledge_conclusion_run SET state=:state,version=:version,
                result_json=:result_json,result_hash=:result_hash,error_code=:error_code,updated_at=:updated_at
                WHERE conclusion_id=:conclusion_id AND version=:expected_version AND state='VALIDATING'"""), values)
            if changed.rowcount != 1:
                current = session.execute(text("SELECT * FROM knowledge_conclusion_run WHERE conclusion_id=:conclusion_id"), values).mappings().first()
                if current and current["state"] == KnowledgeConclusionRunState.SUCCEEDED.value:
                    return self._from_row(current)
                raise KnowledgeConclusionFenced("KNOWLEDGE_CONCLUSION_RUN_FENCED")
            for citation in citations:
                session.execute(text("""INSERT INTO knowledge_conclusion_citation(
                    conclusion_id,finding_index,knowledge_id,evidence_id,quote_hash,quote_hash_provenance)
                    VALUES (:conclusion_id,:finding_index,:knowledge_id,:evidence_id,:quote_hash,:quote_hash_provenance)"""), {
                    "conclusion_id": run.conclusion_id, "finding_index": citation.finding_index,
                    "knowledge_id": citation.knowledge_id, "evidence_id": citation.evidence_id,
                    "quote_hash": citation.quote_hash, "quote_hash_provenance": citation.quote_hash_provenance,
                })
        return run

    def citations(self, conclusion_id: str) -> tuple[KnowledgeConclusionCitation, ...]:
        with session_scope() as session:
            rows = session.execute(text("""SELECT finding_index,knowledge_id,evidence_id,quote_hash,quote_hash_provenance
                FROM knowledge_conclusion_citation WHERE conclusion_id=:id ORDER BY finding_index,knowledge_id,evidence_id"""), {"id": conclusion_id}).mappings()
            return tuple(KnowledgeConclusionCitation(int(row["finding_index"]), row["knowledge_id"], row["evidence_id"], row["quote_hash"], row["quote_hash_provenance"]) for row in rows)

    @staticmethod
    def _assert_seal_digest(session: Any, conclusion_id: str, expected_seal_digest: str | None) -> None:
        """Lock and compare the canonical opaque seal before a state/result CAS.

        The version alone does not protect a reader which decoded a seal before
        another writer replaced it.  PostgreSQL evaluates this while holding
        the row lock, so a foreign/replaced response cannot reach citations.
        """
        if expected_seal_digest is None:
            return
        lock = " FOR UPDATE" if session.bind is not None and session.bind.dialect.name == "postgresql" else ""
        row = session.execute(text("SELECT sealed_model_response_json FROM knowledge_conclusion_run "
            "WHERE conclusion_id=:conclusion_id" + lock), {"conclusion_id": conclusion_id}).mappings().first()
        actual = _seal_digest_json(row["sealed_model_response_json"]) if row is not None else None
        if row is None or (actual or ABSENT_SEAL_DIGEST) != expected_seal_digest:
            raise KnowledgeConclusionFenced("KNOWLEDGE_CONCLUSION_RUN_FENCED")

    def audit(self, conclusion_id: str, mode: str, detail: dict[str, object]) -> str:
        audit_id = str(uuid4())
        with session_scope() as session:
            session.execute(text("""INSERT INTO knowledge_conclusion_replay_audit(audit_id,conclusion_id,mode,detail_json)
                VALUES (:audit_id,:conclusion_id,:mode,:detail_json)"""), {"audit_id": audit_id, "conclusion_id": conclusion_id, "mode": mode, "detail_json": _json(detail)})
        return audit_id

    def audit_events(self, conclusion_id: str) -> tuple[tuple[str, dict[str, object]], ...]:
        with session_scope() as session:
            rows = session.execute(text("""SELECT mode,detail_json FROM knowledge_conclusion_replay_audit
                WHERE conclusion_id=:id ORDER BY created_at,audit_id"""), {"id": conclusion_id}).mappings()
            return tuple((row["mode"], json.loads(row["detail_json"])) for row in rows)

    def append_lineage_audit(self, audit: KnowledgeConclusionLineageAudit) -> KnowledgeConclusionLineageAudit:
        payload = {
            "raw_request_hash": audit.raw_request_hash,
            "effective_request_hash": audit.effective_request_hash,
            "bundle_hash": audit.bundle_hash, "result_hash": audit.result_hash,
            "model_mode": audit.model_mode, "model_provider": audit.model_provider, "model_name": audit.model_name,
            "prompt_version": audit.prompt_version, "fallback_reason": audit.fallback_reason,
            "profile": audit.profile, "trace_id": audit.trace_id, "producer_sha": audit.producer_sha,
            "contract_checksum": audit.contract_checksum, "consumer_sha": audit.consumer_sha,
            "citations": [row.__dict__ for row in audit.citations], "lineage_identity_hash": audit.lineage_identity_hash,
        }
        with session_scope() as session:
            session.execute(text("""INSERT INTO knowledge_conclusion_lineage_audit(
                audit_id,conclusion_id,audit_hash,payload_json,created_at)
                VALUES (:audit_id,:conclusion_id,:audit_hash,:payload_json,:created_at)"""), {
                "audit_id": audit.audit_id, "conclusion_id": audit.conclusion_id, "audit_hash": audit.audit_hash,
                "payload_json": _json(payload), "created_at": audit.recorded_at,
            })
        return audit

    def lineage_audits(self, conclusion_id: str) -> tuple[KnowledgeConclusionLineageAudit, ...]:
        with session_scope() as session:
            rows = session.execute(text("""SELECT audit_id,audit_hash,payload_json,created_at
                FROM knowledge_conclusion_lineage_audit WHERE conclusion_id=:id ORDER BY created_at,audit_id"""), {"id": conclusion_id}).mappings()
            audits = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                citations = tuple(LineageFinding(**citation) for citation in payload["citations"])
                # Pre-044 audit rows held one effective request hash only.
                # Keep them readable without pretending their raw caller form
                # was retained before the new persistence contract.
                raw_request_hash = payload.get("raw_request_hash") or payload.get("request_hash")
                effective_request_hash = payload.get("effective_request_hash") or payload.get("request_hash")
                if not isinstance(raw_request_hash, str) or not isinstance(effective_request_hash, str):
                    raise TypeError("LINEAGE_REQUEST_HASH_MISSING")
                audits.append(KnowledgeConclusionLineageAudit(
                    audit_id=row["audit_id"], conclusion_id=conclusion_id, audit_hash=row["audit_hash"],
                    recorded_at=row["created_at"], citations=citations,
                    lineage_identity_hash=payload.get("lineage_identity_hash"),
                    raw_request_hash=raw_request_hash, effective_request_hash=effective_request_hash,
                    **{key: payload[key] for key in (
                        "bundle_hash", "result_hash", "model_mode", "model_provider", "model_name",
                        "prompt_version", "fallback_reason", "profile", "trace_id", "producer_sha", "contract_checksum", "consumer_sha",
                    )},
                ))
            return tuple(audits)

    @staticmethod
    def _values(run: KnowledgeConclusionRun) -> dict[str, Any]:
        frozen = run.frozen_bundle
        return {
            "conclusion_id": run.conclusion_id, "idempotency_key": run.idempotency_key,
            # request_hash is retained only for backwards-compatible reads;
            # all new writes populate explicit raw/effective columns.
            "request_hash": run.effective_request_hash,
            "raw_request_json": _json(run.raw_request), "raw_request_hash": run.raw_request_hash,
            "effective_request_hash": run.effective_request_hash,
            "request_json": _json(run.request.model_dump(mode="json")), "policy_version": run.policy_version,
            "state": run.state.value, "version": run.version, "frozen_bundle_id": frozen.bundle_id if frozen else None,
            "frozen_bundle_hash": frozen.bundle_hash if frozen else None,
            "frozen_bundle_json": _json(frozen.payload) if frozen else None,
            "producer_sha": frozen.producer_sha if frozen else None, "contract_checksum": frozen.contract_checksum if frozen else None,
            "model_request_id": run.model_request_id, "model_provider_idempotency_key": run.model_provider_idempotency_key,
            "sealed_model_response_json": _json(run.sealed_model_response),
            "audit_metadata_json": _json(run.audit_metadata.__dict__) if run.audit_metadata else None,
            "result_json": _json(run.result.model_dump(mode="json")) if run.result else None,
            "result_hash": run.result_hash, "error_code": run.error_code,
            "created_at": run.created_at, "updated_at": run.updated_at,
        }

    @staticmethod
    def _from_row(row: Any) -> KnowledgeConclusionRun:
        request = KnowledgeConclusionRequest.model_validate(json.loads(row["request_json"]))
        raw_request_json = row.get("raw_request_json")
        raw_request_hash = row.get("raw_request_hash")
        effective_request_hash = row.get("effective_request_hash") or row["request_hash"]
        # Migration 044 deliberately leaves the caller form NULL for rows
        # written before raw identity existed. Treating request_json as that
        # raw form would silently change idempotency semantics on replay.
        if not raw_request_json or not raw_request_hash:
            raise ValueError("LEGACY_RAW_REQUEST_UNVERIFIABLE")
        frozen = None
        if row["frozen_bundle_id"] is not None:
            frozen = FrozenBundle(row["frozen_bundle_id"], row["frozen_bundle_hash"], json.loads(row["frozen_bundle_json"]), row["producer_sha"], row["contract_checksum"], request.content_snapshot_id)
        result = KnowledgeConclusion.model_validate(json.loads(row["result_json"])) if row["result_json"] else None
        return KnowledgeConclusionRun(
            conclusion_id=row["conclusion_id"], idempotency_key=row["idempotency_key"],
            raw_request=json.loads(raw_request_json), raw_request_hash=raw_request_hash,
            effective_request_hash=effective_request_hash,
            request=request, policy_version=row["policy_version"], state=KnowledgeConclusionRunState(row["state"]),
            version=int(row["version"]), frozen_bundle=frozen, model_request_id=row["model_request_id"],
            model_provider_idempotency_key=row["model_provider_idempotency_key"],
            sealed_model_response=json.loads(row["sealed_model_response_json"]) if row["sealed_model_response_json"] else None,
            result=result, result_hash=row["result_hash"], error_code=row["error_code"],
            audit_metadata=KnowledgeConclusionAuditMetadata(**json.loads(row["audit_metadata_json"])) if row.get("audit_metadata_json") else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
