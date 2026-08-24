from __future__ import annotations

from datetime import UTC
from sqlalchemy import select

from contracts.decision_input import DecisionInputBundle, DecisionInputBundlePatch
from contracts.evidence import Evidence, evidence_hash_material
from storage.db import session_scope
from storage.models.decision_input import DecisionInputBundlePatchRecord, DecisionInputBundleRecord, EvidenceRefRecord


class DecisionInputBundleRepository:
    """Persist immutable bundle/evidence snapshots; conflicting hashes fail."""

    def save(self, bundle: DecisionInputBundle) -> DecisionInputBundleRecord:
        with session_scope() as session:
            existing = session.get(DecisionInputBundleRecord, bundle.bundle_id)
            if existing is not None:
                if existing.bundle_hash != bundle.bundle_hash or existing.payload_json != bundle.model_dump(mode="json"):
                    raise ValueError("immutable bundle hash conflict")
                return existing
            row = DecisionInputBundleRecord(bundle_id=bundle.bundle_id, schema_version=bundle.schema_version, decision_time=bundle.decision_time, task_type=bundle.task_type, objective=bundle.objective, subjects_json=bundle.subjects, bundle_hash=bundle.bundle_hash, payload_json=bundle.model_dump(mode="json"))
            session.add(row)
            for item in bundle.evidence:
                self._save_evidence(session, bundle.bundle_id, item)
            session.flush()
            session.refresh(row)
            return row

    def get(self, bundle_id: str) -> DecisionInputBundleRecord | None:
        with session_scope() as session:
            return session.get(DecisionInputBundleRecord, bundle_id)

    def get_bundle(self, bundle_id: str) -> DecisionInputBundle | None:
        with session_scope() as session:
            row = session.get(DecisionInputBundleRecord, bundle_id)
            return DecisionInputBundle.model_validate(row.payload_json) if row is not None else None

    def save_patched(self, bundle: DecisionInputBundle, patch: DecisionInputBundlePatch | None = None) -> DecisionInputBundleRecord:
        if patch is None:
            raise ValueError("patched bundle requires persisted patch lineage")
        return self.apply_patch(bundle, patch)

    def save_patch(self, bundle: DecisionInputBundle, patch: DecisionInputBundlePatch) -> DecisionInputBundlePatchRecord:
        from contracts.decision_input import apply_bundle_patch
        expected, expected_patch = apply_bundle_patch(bundle, reason=patch.reason, evidence=list(patch.evidence), created_at=patch.created_at)
        if expected_patch.previous_hash != patch.previous_hash or expected.bundle_hash != patch.new_hash:
            raise ValueError("patch hash lineage is forged")
        with session_scope() as session:
            if session.get(DecisionInputBundleRecord, bundle.bundle_id) is None:
                raise ValueError("cannot save patch without its bundle")
            existing = session.get(DecisionInputBundlePatchRecord, patch.patch_id)
            if existing is not None:
                if not self._same_patch(existing, bundle.bundle_id, patch):
                    raise ValueError("immutable bundle patch conflict")
                return existing
            if patch.previous_hash != bundle.bundle_hash or patch.new_hash == patch.previous_hash:
                raise ValueError("patch lineage does not match bundle hash")
            row = DecisionInputBundlePatchRecord(
                patch_id=patch.patch_id,
                bundle_id=bundle.bundle_id,
                reason=patch.reason,
                created_at=patch.created_at,
                previous_hash=patch.previous_hash,
                new_hash=patch.new_hash,
                evidence_json=[item.model_dump(mode="json") for item in patch.evidence],
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def apply_patch(self, bundle: DecisionInputBundle, patch: DecisionInputBundlePatch) -> DecisionInputBundleRecord:
        """Atomically validate, append patch lineage, and materialize its new hash."""
        bundle = DecisionInputBundle.model_validate(bundle.model_dump(mode="python"))
        with session_scope() as session:
            row = session.get(DecisionInputBundleRecord, bundle.bundle_id)
            if row is None:
                raise ValueError("cannot apply patch without its bundle")
            existing = session.get(DecisionInputBundlePatchRecord, patch.patch_id)
            if existing is not None and self._same_patch(existing, bundle.bundle_id, patch):
                if row.bundle_hash == patch.new_hash and row.payload_json == bundle.model_dump(mode="json"):
                    return row
                if row.bundle_hash != patch.previous_hash:
                    raise ValueError("patch was persisted but final materialization is inconsistent")
            stored_payload = DecisionInputBundle.model_validate(row.payload_json)
            if row.bundle_hash != stored_payload.bundle_hash or row.bundle_hash != patch.previous_hash:
                raise ValueError("patch lineage does not match bundle")
            if patch.new_hash == patch.previous_hash or patch.new_hash != bundle.bundle_hash:
                raise ValueError("patch must materialize a different validated bundle hash")
            from contracts.decision_input import apply_bundle_patch
            expected, expected_patch = apply_bundle_patch(stored_payload, reason=patch.reason, evidence=list(patch.evidence), created_at=patch.created_at)
            if expected.bundle_hash != bundle.bundle_hash or expected.model_dump(mode="json") != bundle.model_dump(mode="json"):
                raise ValueError("patched bundle does not match patch materialization")
            if expected_patch.previous_hash != patch.previous_hash or expected_patch.new_hash != patch.new_hash:
                raise ValueError("patch hash lineage is forged")
            if existing is None:
                session.add(DecisionInputBundlePatchRecord(
                    patch_id=patch.patch_id, bundle_id=bundle.bundle_id, reason=patch.reason,
                    created_at=patch.created_at, previous_hash=patch.previous_hash,
                    new_hash=patch.new_hash, evidence_json=[item.model_dump(mode="json") for item in patch.evidence],
                ))
            elif not self._same_patch(existing, bundle.bundle_id, patch):
                raise ValueError("immutable bundle patch conflict")
            row.bundle_hash = bundle.bundle_hash
            row.payload_json = bundle.model_dump(mode="json")
            for item in bundle.evidence:
                self._save_evidence(session, bundle.bundle_id, item)
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def _same_patch(row: DecisionInputBundlePatchRecord, bundle_id: str, patch: DecisionInputBundlePatch) -> bool:
        stored_created = row.created_at if row.created_at.tzinfo is not None else row.created_at.replace(tzinfo=UTC)
        return (
            row.bundle_id == bundle_id
            and row.reason == patch.reason
            and stored_created == patch.created_at
            and row.previous_hash == patch.previous_hash
            and row.new_hash == patch.new_hash
            and row.evidence_json == [item.model_dump(mode="json") for item in patch.evidence]
        )

    def list_patches(self, bundle_id: str) -> list[DecisionInputBundlePatchRecord]:
        with session_scope() as session:
            return list(session.execute(select(DecisionInputBundlePatchRecord).where(DecisionInputBundlePatchRecord.bundle_id == bundle_id).order_by(DecisionInputBundlePatchRecord.created_at, DecisionInputBundlePatchRecord.patch_id)).scalars())

    def get_patch(self, patch_id: str) -> DecisionInputBundlePatchRecord | None:
        with session_scope() as session:
            return session.get(DecisionInputBundlePatchRecord, patch_id)

    @staticmethod
    def _save_evidence(session, bundle_id: str, item: Evidence) -> EvidenceRefRecord:
        payload_hash = evidence_hash_material(source_system=item.source_system, source_ref=item.source_ref, snapshot_id=item.snapshot_id, subject_key=item.subject_key, payload=item.payload)
        existing = session.execute(select(EvidenceRefRecord).where(EvidenceRefRecord.bundle_id == bundle_id, EvidenceRefRecord.evidence_id == item.evidence_id)).scalars().first()
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ValueError("immutable evidence payload hash conflict")
            return existing
        row = EvidenceRefRecord(bundle_id=bundle_id, evidence_id=item.evidence_id, evidence_type=item.evidence_type.value, source_system=item.source_system.value, source_ref=item.source_ref, snapshot_id=item.snapshot_id, contract_version=item.contract_version, as_of=item.as_of, available_at=item.available_at, quality_status=item.quality_status.value, payload_hash=payload_hash, payload_json=item.payload)
        session.add(row)
        return row
