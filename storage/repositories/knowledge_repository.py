from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select

from storage.db import session_scope
from storage.models.knowledge import (
    KnowledgeEntityRelation,
    KnowledgeEvidence,
    KnowledgeExtractionRun,
    KnowledgeLifecycleAudit,
    KnowledgeUnit,
    KnowledgeUnitRelation,
    VideoAnalysisDocument,
    VideoChapter,
)
from storage.repositories.vector_repository import VectorTaskRepository
from storage.models.vector import VectorIndexMapping, VectorIndexTask


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, default: object) -> object:
    if not value:
        return default
    return json.loads(value)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class KnowledgeRepository:
    def replace_video_knowledge(
        self,
        *,
        video_id: int,
        chapters: list[dict],
        units: list[dict],
        relations: list[dict] | None = None,
    ) -> dict:
        relations = relations or []
        with session_scope() as session:
            existing_units = session.execute(select(KnowledgeUnit.id).where(KnowledgeUnit.source_video_id == video_id)).scalars().all()
            if existing_units:
                session.execute(delete(KnowledgeUnitRelation).where(KnowledgeUnitRelation.source_unit_id.in_(existing_units)))
                session.execute(delete(KnowledgeUnitRelation).where(KnowledgeUnitRelation.target_unit_id.in_(existing_units)))
                session.execute(delete(KnowledgeEntityRelation).where(KnowledgeEntityRelation.knowledge_unit_id.in_(existing_units)))
                session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_unit_id.in_(existing_units)))
                session.execute(delete(KnowledgeUnit).where(KnowledgeUnit.id.in_(existing_units)))
            existing_chapters = session.execute(select(VideoChapter.id).where(VideoChapter.video_id == video_id)).scalars().all()
            if existing_chapters:
                session.execute(delete(VideoChapter).where(VideoChapter.id.in_(existing_chapters)))

            chapter_id_by_index: dict[int, int] = {}
            for chapter in chapters:
                row = VideoChapter(
                    video_id=video_id,
                    chapter_index=int(chapter.get("chapter_index") or 0),
                    parent_chapter_id=chapter.get("parent_chapter_id"),
                    start_ms=int(chapter.get("start_ms") or 0),
                    end_ms=int(chapter.get("end_ms") or 0),
                    title=str(chapter.get("title") or "未命名章节")[:512],
                    chapter_type=str(chapter.get("chapter_type") or "OTHER")[:64],
                    primary_domain=str(chapter.get("primary_domain") or "GENERAL")[:32],
                    secondary_domains_json=_dumps(chapter.get("secondary_domains") or []),
                    summary=chapter.get("summary"),
                    entities_json=_dumps(chapter.get("entities") or []),
                    boundary_source=chapter.get("boundary_source"),
                    boundary_score=chapter.get("boundary_score"),
                    confidence_score=chapter.get("confidence_score"),
                    content_hash=str(chapter.get("content_hash") or ""),
                    parser_version=str(chapter.get("parser_version") or "v3.0-rule"),
                )
                session.add(row)
                session.flush()
                chapter_id_by_index[row.chapter_index] = row.id

            unit_id_by_uid: dict[str, int] = {}
            for unit in units:
                chapter_index = int(unit.get("chapter_index") or 0)
                chapter_id = chapter_id_by_index.get(chapter_index)
                if chapter_id is None:
                    continue
                row = KnowledgeUnit(
                    knowledge_uid=str(unit.get("knowledge_uid") or ""),
                    source_video_id=video_id,
                    source_chapter_id=chapter_id,
                    primary_domain=str(unit.get("primary_domain") or "GENERAL"),
                    secondary_domains_json=_dumps(unit.get("secondary_domains") or []),
                    knowledge_kind=str(unit.get("knowledge_kind") or "STATE"),
                    temporal_class=str(unit.get("temporal_class") or "SNAPSHOT"),
                    expression_type=str(unit.get("expression_type") or "AUTHOR_EXPLICIT"),
                    subject_type=unit.get("subject_type"),
                    subject_key=unit.get("subject_key"),
                    subject_name=unit.get("subject_name"),
                    predicate_key=unit.get("predicate_key"),
                    statement=str(unit.get("statement") or ""),
                    canonical_statement=str(unit.get("canonical_statement") or unit.get("statement") or ""),
                    claim_type=unit.get("claim_type"),
                    sentiment=unit.get("sentiment"),
                    certainty_score=unit.get("certainty_score"),
                    extraction_confidence=unit.get("extraction_confidence"),
                    as_of_time=unit.get("as_of_time"),
                    valid_from=unit.get("valid_from"),
                    valid_to=unit.get("valid_to"),
                    time_horizon=unit.get("time_horizon"),
                    timeframe=unit.get("timeframe"),
                    decay_half_life_days=unit.get("decay_half_life_days"),
                    condition_text=unit.get("condition_text"),
                    invalidation_text=unit.get("invalidation_text"),
                    lifecycle_status=str(unit.get("lifecycle_status") or "ACTIVE"),
                    verification_status=str(unit.get("verification_status") or "SOURCE_CONFIRMED"),
                    scope_type=unit.get("scope_type"),
                    scope_key=unit.get("scope_key"),
                    conflict_key=unit.get("conflict_key"),
                    conflict_group_id=unit.get("conflict_group_id"),
                    superseded_by_unit_id=unit.get("superseded_by_unit_id"),
                    content_hash=str(unit.get("content_hash") or ""),
                    semantic_hash=unit.get("semantic_hash"),
                    attributes_json=_dumps(unit.get("attributes") or {}),
                    extractor_provider=unit.get("extractor_provider"),
                    extractor_model=unit.get("extractor_model"),
                    extractor_version=str(unit.get("extractor_version") or "v3.0-rule"),
                    schema_version=str(unit.get("schema_version") or "v1"),
                )
                session.add(row)
                session.flush()
                unit_id_by_uid[row.knowledge_uid] = row.id
                for evidence in unit.get("evidence") or []:
                    session.add(
                        KnowledgeEvidence(
                            knowledge_unit_id=row.id,
                            source_type=str(evidence.get("source_type") or "ASR"),
                            source_ref=evidence.get("source_ref"),
                            evidence_text=str(evidence.get("evidence_text") or evidence.get("text") or ""),
                            start_ms=evidence.get("start_ms"),
                            end_ms=evidence.get("end_ms"),
                            frame_id=evidence.get("frame_id"),
                            confidence_score=evidence.get("confidence_score"),
                            is_primary=bool(evidence.get("is_primary")),
                        )
                    )
                for entity in unit.get("entities") or []:
                    session.add(
                        KnowledgeEntityRelation(
                            knowledge_unit_id=row.id,
                            entity_type=str(entity.get("entity_type") or "UNKNOWN"),
                            entity_key=entity.get("entity_key") or entity.get("ticker") or entity.get("name"),
                            entity_name=str(entity.get("entity_name") or entity.get("name") or entity.get("ticker") or "UNKNOWN"),
                            ticker=entity.get("ticker"),
                            relation_role=str(entity.get("relation_role") or "RELATED"),
                            confidence_score=entity.get("confidence_score"),
                        )
                    )

            for relation in relations:
                source_id = unit_id_by_uid.get(str(relation.get("source_uid") or ""))
                target_id = unit_id_by_uid.get(str(relation.get("target_uid") or ""))
                if not source_id or not target_id or source_id == target_id:
                    continue
                session.add(
                    KnowledgeUnitRelation(
                        source_unit_id=source_id,
                        target_unit_id=target_id,
                        relation_type=str(relation.get("relation_type") or "SUPPORTS"),
                        confidence_score=relation.get("confidence_score"),
                        attributes_json=_dumps(relation.get("attributes") or {}),
                    )
                )
                if str(relation.get("relation_type") or "").upper() == "SUPERSEDES":
                    target = session.get(KnowledgeUnit, target_id)
                    if target is not None:
                        target.superseded_by_unit_id = source_id
                        session.add(target)

            return {
                "video_id": video_id,
                "chapter_count": len(chapter_id_by_index),
                "knowledge_unit_count": len(unit_id_by_uid),
                "knowledge_unit_ids": list(unit_id_by_uid.values()),
            }

    def list_chapters(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = session.execute(select(VideoChapter).where(VideoChapter.video_id == video_id).order_by(VideoChapter.chapter_index.asc())).scalars()
            return [self._serialize_chapter(row) for row in rows]

    def get_chapter(self, video_id: int, chapter_id: int) -> dict | None:
        with session_scope() as session:
            row = session.get(VideoChapter, chapter_id)
            if row is None or row.video_id != video_id:
                return None
            payload = self._serialize_chapter(row)
            payload["knowledge_units"] = self.list_units_for_video(
                video_id,
                filters={"source_chapter_id": chapter_id},
            )
            return payload

    def list_units_for_video(self, video_id: int, filters: dict | None = None, limit: int | None = None) -> list[dict]:
        with session_scope() as session:
            statement = select(KnowledgeUnit).where(KnowledgeUnit.source_video_id == video_id)
            statement = self._apply_unit_filters(statement, filters)
            statement = statement.order_by(KnowledgeUnit.id.asc())
            if limit is not None:
                statement = statement.limit(int(limit))
            rows = list(session.execute(statement).scalars())
            if not rows:
                return []
            unit_ids = [row.id for row in rows]
            evidence_rows = list(session.execute(select(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_unit_id.in_(unit_ids))).scalars())
            entity_rows = list(session.execute(select(KnowledgeEntityRelation).where(KnowledgeEntityRelation.knowledge_unit_id.in_(unit_ids))).scalars())
            evidence_by_unit: dict[int, list[dict]] = {}
            entity_by_unit: dict[int, list[dict]] = {}
            for evidence in evidence_rows:
                evidence_by_unit.setdefault(evidence.knowledge_unit_id, []).append(
                    {
                        "source_type": evidence.source_type,
                        "source_ref": evidence.source_ref,
                        "evidence_text": evidence.evidence_text,
                        "start_ms": evidence.start_ms,
                        "end_ms": evidence.end_ms,
                        "frame_id": evidence.frame_id,
                        "confidence_score": evidence.confidence_score,
                        "is_primary": evidence.is_primary,
                    }
                )
            for entity in entity_rows:
                entity_by_unit.setdefault(entity.knowledge_unit_id, []).append(
                    {
                        "entity_type": entity.entity_type,
                        "entity_key": entity.entity_key,
                        "entity_name": entity.entity_name,
                        "ticker": entity.ticker,
                        "relation_role": entity.relation_role,
                        "confidence_score": entity.confidence_score,
                    }
                )
            vector_status = self._vector_status_for_units(session, unit_ids)
            return [
                self._serialize_unit(
                    row,
                    evidence_by_unit.get(row.id, []),
                    entity_by_unit.get(row.id, []),
                    vector_status.get(row.id, {}),
                )
                for row in rows
            ]

    def search_units(self, query: str, filters: dict | None = None, limit: int = 20) -> list[dict]:
        query = str(query or "").strip()
        with session_scope() as session:
            statement = select(KnowledgeUnit)
            statement = self._apply_unit_filters(statement, filters)
            if query:
                like_query = f"%{query.lower()}%"
                statement = statement.where(
                    or_(
                        func.lower(KnowledgeUnit.canonical_statement).like(like_query),
                        func.lower(KnowledgeUnit.statement).like(like_query),
                        func.lower(KnowledgeUnit.subject_name).like(like_query),
                        func.lower(KnowledgeUnit.subject_key).like(like_query),
                    )
                )
            rows = list(session.execute(statement.order_by(KnowledgeUnit.as_of_time.desc(), KnowledgeUnit.id.desc()).limit(limit)).scalars())
            return self._serialize_units_with_children(session, rows)

    def get_unit(self, unit_id: int) -> dict | None:
        with session_scope() as session:
            row = session.get(KnowledgeUnit, unit_id)
            if row is None:
                return None
            evidence_rows = list(session.execute(select(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_unit_id == unit_id)).scalars())
            entity_rows = list(session.execute(select(KnowledgeEntityRelation).where(KnowledgeEntityRelation.knowledge_unit_id == unit_id)).scalars())
            evidence = [
                {
                    "source_type": evidence.source_type,
                    "source_ref": evidence.source_ref,
                    "evidence_text": evidence.evidence_text,
                    "start_ms": evidence.start_ms,
                    "end_ms": evidence.end_ms,
                    "frame_id": evidence.frame_id,
                    "confidence_score": evidence.confidence_score,
                    "is_primary": evidence.is_primary,
                }
                for evidence in evidence_rows
            ]
            entities = [
                {
                    "entity_type": entity.entity_type,
                    "entity_key": entity.entity_key,
                    "entity_name": entity.entity_name,
                    "ticker": entity.ticker,
                    "relation_role": entity.relation_role,
                    "confidence_score": entity.confidence_score,
                }
                for entity in entity_rows
            ]
            vector_status = self._vector_status_for_units(session, [unit_id]).get(unit_id, {})
            payload = self._serialize_unit(row, evidence, entities, vector_status)
            payload["relations"] = self._relations_for_unit(session, unit_id)
            return payload

    def update_unit_lifecycle(
        self,
        unit_id: int,
        *,
        lifecycle_status: str | None = None,
        verification_status: str | None = None,
        valid_to: datetime | None = None,
        attributes_patch: dict | None = None,
        reason: str | None = None,
        operator: str | None = None,
    ) -> dict | None:
        with session_scope() as session:
            row = session.get(KnowledgeUnit, unit_id)
            if row is None:
                return None
            before_lifecycle = row.lifecycle_status
            before_verification = row.verification_status
            before_valid_to = row.valid_to
            if lifecycle_status:
                row.lifecycle_status = lifecycle_status
            if verification_status:
                row.verification_status = verification_status
            if valid_to is not None:
                row.valid_to = valid_to
            if attributes_patch:
                attrs = _loads(row.attributes_json, {})
                attrs.update(attributes_patch)
                row.attributes_json = _dumps(attrs)
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.flush()
            audit = KnowledgeLifecycleAudit(
                knowledge_unit_id=row.id,
                from_lifecycle_status=before_lifecycle,
                to_lifecycle_status=row.lifecycle_status,
                from_verification_status=before_verification,
                to_verification_status=row.verification_status,
                valid_to_before=before_valid_to,
                valid_to_after=row.valid_to,
                reason=reason,
                operator=operator,
            )
            session.add(audit)
            session.flush()
            payload = self._serialize_units_with_children(session, [row])[0]
            payload["lifecycle_audit"] = self._serialize_lifecycle_audit(audit)
            return payload

    def record_lifecycle_vector_tasks(self, audit_id: int, vector_tasks: list[dict]) -> None:
        with session_scope() as session:
            row = session.get(KnowledgeLifecycleAudit, audit_id)
            if row is None:
                return
            row.vector_task_ids_json = _dumps([task.get("task_id") for task in vector_tasks if task.get("task_id")])
            session.add(row)

    def expire_due_units(self, now: datetime | None = None, limit: int = 500, *, reason: str = "valid_to elapsed", operator: str = "system") -> list[dict]:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        terminal_statuses = {"EXPIRED", "REJECTED", "RETIRED"}
        with session_scope() as session:
            rows = list(
                session.execute(
                    select(KnowledgeUnit)
                    .where(
                        KnowledgeUnit.valid_to.is_not(None),
                        KnowledgeUnit.valid_to < now,
                        KnowledgeUnit.lifecycle_status.not_in(terminal_statuses),
                    )
                    .order_by(KnowledgeUnit.valid_to.asc(), KnowledgeUnit.id.asc())
                    .limit(limit)
                ).scalars()
            )
            audits_by_unit: dict[int, KnowledgeLifecycleAudit] = {}
            for row in rows:
                audit = KnowledgeLifecycleAudit(
                    knowledge_unit_id=row.id,
                    from_lifecycle_status=row.lifecycle_status,
                    to_lifecycle_status="EXPIRED",
                    from_verification_status=row.verification_status,
                    to_verification_status=row.verification_status,
                    valid_to_before=row.valid_to,
                    valid_to_after=row.valid_to,
                    reason=reason,
                    operator=operator,
                )
                row.lifecycle_status = "EXPIRED"
                row.updated_at = datetime.now(UTC)
                session.add(row)
                session.add(audit)
                session.flush()
                audits_by_unit[row.id] = audit
            payloads = self._serialize_units_with_children(session, rows)
            for payload in payloads:
                audit = audits_by_unit.get(int(payload["id"]))
                if audit is not None:
                    payload["lifecycle_audit"] = self._serialize_lifecycle_audit(audit)
            return payloads

    def list_unit_lifecycle_audits(self, unit_id: int, limit: int = 50) -> list[dict]:
        with session_scope() as session:
            rows = list(
                session.execute(
                    select(KnowledgeLifecycleAudit)
                    .where(KnowledgeLifecycleAudit.knowledge_unit_id == unit_id)
                    .order_by(KnowledgeLifecycleAudit.created_at.desc(), KnowledgeLifecycleAudit.id.desc())
                    .limit(limit)
                ).scalars()
            )
            return [self._serialize_lifecycle_audit(row) for row in rows]

    def list_conflicts(self, subject_key: str | None = None, limit: int = 50) -> list[dict]:
        with session_scope() as session:
            statement = (
                select(KnowledgeUnit.conflict_group_id)
                .where(KnowledgeUnit.conflict_group_id.is_not(None))
                .group_by(KnowledgeUnit.conflict_group_id)
                .having(func.count(KnowledgeUnit.id) > 1)
                .order_by(func.max(KnowledgeUnit.as_of_time).desc())
                .limit(limit)
            )
            if subject_key:
                statement = statement.where(KnowledgeUnit.subject_key == subject_key)
            group_ids = [row[0] for row in session.execute(statement).all()]
            if not group_ids:
                return []
            rows = list(
                session.execute(
                    select(KnowledgeUnit)
                    .where(KnowledgeUnit.conflict_group_id.in_(group_ids))
                    .order_by(KnowledgeUnit.conflict_group_id.asc(), KnowledgeUnit.as_of_time.desc(), KnowledgeUnit.id.desc())
                ).scalars()
            )
            units = self._serialize_units_with_children(session, rows)
        by_group: dict[str, list[dict]] = {}
        for unit in units:
            by_group.setdefault(str(unit.get("conflict_group_id") or ""), []).append(unit)
        return [
            {
                "conflict_group_id": group_id,
                "conflict_key": values[0].get("conflict_key") if values else None,
                "subject_key": values[0].get("subject_key") if values else None,
                "recommended_action": self._recommended_conflict_action(values),
                "units": values,
            }
            for group_id, values in by_group.items()
        ]

    @staticmethod
    def _recommended_conflict_action(units: list[dict]) -> str:
        kinds = {str(unit.get("knowledge_kind") or "") for unit in units}
        if "ACTION" in kinds:
            return "review_or_retire_stale_action"
        if "FORECAST" in kinds:
            return "keep_forecast_history"
        if kinds & {"METHOD", "CONCEPT"}:
            return "manual_review_before_supersede"
        if "FACT" in kinds:
            return "require_evidence_verification"
        return "keep_latest_as_current"

    def get_current_subject_state(self, subject_key: str, domain: str | None = None, limit: int = 20) -> dict:
        filters = {
            "subject_key": subject_key,
            "lifecycle_status": ["ACTIVE", "VALIDATED"],
        }
        if domain:
            filters["primary_domain"] = domain
        units = self.search_units("", filters=filters, limit=limit)
        current = [
            unit
            for unit in units
            if unit.get("valid_to") is None or _parse_dt(unit.get("valid_to")) is None or _parse_dt(unit.get("valid_to")) >= datetime.now(UTC)
        ]
        return {"subject_key": subject_key, "domain": domain, "items": current[:limit]}

    def get_subject_history(self, subject_key: str, domain: str | None = None, limit: int = 50) -> dict:
        filters = {"subject_key": subject_key}
        if domain:
            filters["primary_domain"] = domain
        return {"subject_key": subject_key, "domain": domain, "items": self.search_units("", filters=filters, limit=limit)}

    @classmethod
    def _apply_unit_filters(cls, statement, filters: dict | None):
        allowed = {
            "source_chapter_id": KnowledgeUnit.source_chapter_id,
            "primary_domain": KnowledgeUnit.primary_domain,
            "knowledge_kind": KnowledgeUnit.knowledge_kind,
            "temporal_class": KnowledgeUnit.temporal_class,
            "lifecycle_status": KnowledgeUnit.lifecycle_status,
            "verification_status": KnowledgeUnit.verification_status,
            "subject_key": KnowledgeUnit.subject_key,
            "subject_type": KnowledgeUnit.subject_type,
            "predicate_key": KnowledgeUnit.predicate_key,
            "scope_key": KnowledgeUnit.scope_key,
            "conflict_group_id": KnowledgeUnit.conflict_group_id,
        }
        for key, value in (filters or {}).items():
            column = allowed.get(key)
            if column is None or value in (None, ""):
                continue
            if isinstance(value, list):
                if value:
                    statement = statement.where(column.in_(value))
            else:
                statement = statement.where(column == value)
        if (filters or {}).get("valid_only"):
            now = datetime.now(UTC)
            statement = statement.where(or_(KnowledgeUnit.valid_to.is_(None), KnowledgeUnit.valid_to >= now))
        return statement

    def _serialize_units_with_children(self, session, rows: list[KnowledgeUnit]) -> list[dict]:
        if not rows:
            return []
        unit_ids = [row.id for row in rows]
        evidence_rows = list(session.execute(select(KnowledgeEvidence).where(KnowledgeEvidence.knowledge_unit_id.in_(unit_ids))).scalars())
        entity_rows = list(session.execute(select(KnowledgeEntityRelation).where(KnowledgeEntityRelation.knowledge_unit_id.in_(unit_ids))).scalars())
        evidence_by_unit: dict[int, list[dict]] = {}
        entity_by_unit: dict[int, list[dict]] = {}
        for evidence in evidence_rows:
            evidence_by_unit.setdefault(evidence.knowledge_unit_id, []).append(self._serialize_evidence(evidence))
        for entity in entity_rows:
            entity_by_unit.setdefault(entity.knowledge_unit_id, []).append(self._serialize_entity(entity))
        vector_status = self._vector_status_for_units(session, unit_ids)
        return [self._serialize_unit(row, evidence_by_unit.get(row.id, []), entity_by_unit.get(row.id, []), vector_status.get(row.id, {})) for row in rows]

    @staticmethod
    def _serialize_evidence(evidence: KnowledgeEvidence) -> dict:
        return {
            "source_type": evidence.source_type,
            "source_ref": evidence.source_ref,
            "evidence_text": evidence.evidence_text,
            "start_ms": evidence.start_ms,
            "end_ms": evidence.end_ms,
            "frame_id": evidence.frame_id,
            "confidence_score": evidence.confidence_score,
            "is_primary": evidence.is_primary,
        }

    @staticmethod
    def _serialize_entity(entity: KnowledgeEntityRelation) -> dict:
        return {
            "entity_type": entity.entity_type,
            "entity_key": entity.entity_key,
            "entity_name": entity.entity_name,
            "ticker": entity.ticker,
            "relation_role": entity.relation_role,
            "confidence_score": entity.confidence_score,
        }

    @staticmethod
    def _vector_status_for_units(session, unit_ids: list[int]) -> dict[int, dict]:
        if not unit_ids:
            return {}
        mappings = list(
            session.execute(
                select(VectorIndexMapping).where(
                    VectorIndexMapping.postgres_table == "knowledge_unit",
                    VectorIndexMapping.postgres_id.in_(unit_ids),
                )
            ).scalars()
        )
        tasks = list(
            session.execute(
                select(VectorIndexTask).where(
                    VectorIndexTask.postgres_table == "knowledge_unit",
                    VectorIndexTask.postgres_id.in_(unit_ids),
                )
            ).scalars()
        )
        result: dict[int, dict] = {unit_id: {"indexed_collections": [], "pending_tasks": [], "last_indexed_at": None} for unit_id in unit_ids}
        for mapping in mappings:
            payload = result.setdefault(mapping.postgres_id, {"indexed_collections": [], "pending_tasks": [], "last_indexed_at": None})
            payload["indexed_collections"].append(mapping.qdrant_collection)
            if mapping.last_indexed_at:
                payload["last_indexed_at"] = mapping.last_indexed_at.isoformat()
        for task in tasks:
            if task.status in {"success"}:
                continue
            payload = result.setdefault(task.postgres_id, {"indexed_collections": [], "pending_tasks": [], "last_indexed_at": None})
            payload["pending_tasks"].append({"task_id": task.id, "status": task.status, "target_collection": task.target_collection})
        return result

    @staticmethod
    def _relations_for_unit(session, unit_id: int) -> list[dict]:
        rows = list(
            session.execute(
                select(KnowledgeUnitRelation).where(
                    or_(KnowledgeUnitRelation.source_unit_id == unit_id, KnowledgeUnitRelation.target_unit_id == unit_id)
                )
            ).scalars()
        )
        return [
            {
                "id": row.id,
                "source_unit_id": row.source_unit_id,
                "target_unit_id": row.target_unit_id,
                "relation_type": row.relation_type,
                "confidence_score": row.confidence_score,
                "attributes": _loads(row.attributes_json, {}),
            }
            for row in rows
        ]

    @staticmethod
    def _serialize_chapter(row: VideoChapter) -> dict:
        return {
            "id": row.id,
            "video_id": row.video_id,
            "chapter_index": row.chapter_index,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
            "title": row.title,
            "chapter_type": row.chapter_type,
            "primary_domain": row.primary_domain,
            "secondary_domains": _loads(row.secondary_domains_json, []),
            "summary": row.summary,
            "entities": _loads(row.entities_json, []),
            "boundary_source": row.boundary_source,
            "boundary_score": row.boundary_score,
            "confidence_score": row.confidence_score,
        }

    @staticmethod
    def _serialize_lifecycle_audit(row: KnowledgeLifecycleAudit) -> dict:
        return {
            "id": row.id,
            "knowledge_unit_id": row.knowledge_unit_id,
            "from_lifecycle_status": row.from_lifecycle_status,
            "to_lifecycle_status": row.to_lifecycle_status,
            "from_verification_status": row.from_verification_status,
            "to_verification_status": row.to_verification_status,
            "valid_to_before": row.valid_to_before.isoformat() if row.valid_to_before else None,
            "valid_to_after": row.valid_to_after.isoformat() if row.valid_to_after else None,
            "reason": row.reason,
            "operator": row.operator,
            "vector_task_ids": _loads(row.vector_task_ids_json, []),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    @staticmethod
    def _serialize_unit(row: KnowledgeUnit, evidence: list[dict], entities: list[dict], vector_status: dict | None = None) -> dict:
        return {
            "id": row.id,
            "knowledge_uid": row.knowledge_uid,
            "source_video_id": row.source_video_id,
            "source_chapter_id": row.source_chapter_id,
            "primary_domain": row.primary_domain,
            "secondary_domains": _loads(row.secondary_domains_json, []),
            "knowledge_kind": row.knowledge_kind,
            "temporal_class": row.temporal_class,
            "expression_type": row.expression_type,
            "subject_type": row.subject_type,
            "subject_key": row.subject_key,
            "subject_name": row.subject_name,
            "predicate_key": row.predicate_key,
            "statement": row.statement,
            "canonical_statement": row.canonical_statement,
            "claim_type": row.claim_type,
            "sentiment": row.sentiment,
            "certainty_score": row.certainty_score,
            "extraction_confidence": row.extraction_confidence,
            "as_of_time": row.as_of_time.isoformat() if row.as_of_time else None,
            "valid_from": row.valid_from.isoformat() if row.valid_from else None,
            "valid_to": row.valid_to.isoformat() if row.valid_to else None,
            "time_horizon": row.time_horizon,
            "timeframe": row.timeframe,
            "decay_half_life_days": row.decay_half_life_days,
            "condition_text": row.condition_text,
            "invalidation_text": row.invalidation_text,
            "lifecycle_status": row.lifecycle_status,
            "verification_status": row.verification_status,
            "scope_type": row.scope_type,
            "scope_key": row.scope_key,
            "conflict_key": row.conflict_key,
            "conflict_group_id": row.conflict_group_id,
            "superseded_by_unit_id": row.superseded_by_unit_id,
            "content_hash": row.content_hash,
            "semantic_hash": row.semantic_hash,
            "attributes": _loads(row.attributes_json, {}),
            "evidence": evidence,
            "entities": entities,
            "vector_status": vector_status or {},
        }


class VideoAnalysisDocumentRepository:
    def upsert(self, video_id: int, payload: dict) -> VideoAnalysisDocument:
        with session_scope() as session:
            row = session.execute(select(VideoAnalysisDocument).where(VideoAnalysisDocument.video_id == video_id)).scalars().first()
            if row is None:
                row = VideoAnalysisDocument(video_id=video_id)
            row.document_markdown = payload.get("document_markdown") or ""
            row.core_summary = payload.get("core_summary") or ""
            row.video_type = payload.get("video_type") or "GENERAL_FINANCE"
            row.primary_domains_json = _dumps(payload.get("primary_domains") or [])
            row.chapter_count = int(payload.get("chapter_count") or 0)
            row.knowledge_unit_count = int(payload.get("knowledge_unit_count") or 0)
            row.method_count = int(payload.get("method_count") or 0)
            row.fact_count = int(payload.get("fact_count") or 0)
            row.state_count = int(payload.get("state_count") or 0)
            row.thesis_count = int(payload.get("thesis_count") or 0)
            row.forecast_count = int(payload.get("forecast_count") or 0)
            row.action_count = int(payload.get("action_count") or 0)
            row.risk_count = int(payload.get("risk_count") or 0)
            row.confidence_score = payload.get("confidence_score")
            row.generator_provider = payload.get("generator_provider")
            row.generator_model = payload.get("generator_model")
            row.generator_version = payload.get("generator_version") or "v3.0-rule"
            row.schema_version = payload.get("schema_version") or "v1"
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get_for_video(self, video_id: int) -> dict | None:
        with session_scope() as session:
            row = session.execute(select(VideoAnalysisDocument).where(VideoAnalysisDocument.video_id == video_id)).scalars().first()
            if row is None:
                return None
            return {
                "id": row.id,
                "video_id": row.video_id,
                "document_markdown": row.document_markdown,
                "core_summary": row.core_summary,
                "video_type": row.video_type,
                "primary_domains": _loads(row.primary_domains_json, []),
                "chapter_count": row.chapter_count,
                "knowledge_unit_count": row.knowledge_unit_count,
                "confidence_score": row.confidence_score,
                "generator_provider": row.generator_provider,
                "generator_model": row.generator_model,
                "generator_version": row.generator_version,
                "schema_version": row.schema_version,
            }


class KnowledgeExtractionRunRepository:
    def start(self, *, video_id: int, source_hash: str, parser_version: str, extractor_version: str, schema_version: str) -> KnowledgeExtractionRun:
        with session_scope() as session:
            row = KnowledgeExtractionRun(
                run_uid=f"krun_{video_id}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
                video_id=video_id,
                source_hash=source_hash,
                parser_version=parser_version,
                extractor_version=extractor_version,
                schema_version=schema_version,
                status="running",
                stage="started",
                started_at=datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def finish(
        self,
        run_id: int,
        *,
        status: str,
        stage: str,
        chapter_count: int,
        knowledge_unit_count: int,
        degraded: bool = False,
        error_message: str | None = None,
        metrics: dict | None = None,
    ) -> None:
        with session_scope() as session:
            row = session.get(KnowledgeExtractionRun, run_id)
            if row is None:
                return
            row.status = status
            row.stage = stage
            row.chapter_count = chapter_count
            row.knowledge_unit_count = knowledge_unit_count
            row.degraded = degraded
            row.metrics_json = _dumps(metrics or {})
            row.error_message = error_message
            row.completed_at = datetime.now(UTC)
            session.add(row)

    def latest_for_video(self, video_id: int) -> dict | None:
        with session_scope() as session:
            row = session.execute(
                select(KnowledgeExtractionRun)
                .where(KnowledgeExtractionRun.video_id == video_id)
                .order_by(KnowledgeExtractionRun.started_at.desc(), KnowledgeExtractionRun.id.desc())
                .limit(1)
            ).scalars().first()
            return self._serialize(row) if row else None

    @staticmethod
    def _serialize(row: KnowledgeExtractionRun) -> dict:
        return {
            "id": row.id,
            "run_uid": row.run_uid,
            "video_id": row.video_id,
            "status": row.status,
            "stage": row.stage,
            "chapter_count": row.chapter_count,
            "knowledge_unit_count": row.knowledge_unit_count,
            "degraded": row.degraded,
            "metrics": _loads(row.metrics_json, {}),
            "error_message": row.error_message,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }


class KnowledgeVectorTaskService:
    COLLECTIONS = {
        "DURABLE": "financial_video_durable_v1_bge_m3",
        "ACTION": "financial_video_action_v1_bge_m3",
        "TIMED": "financial_video_timed_v1_bge_m3",
    }

    def enqueue_units(self, units: list[dict]) -> list[dict]:
        tasks = []
        repo = VectorTaskRepository()
        for unit in units:
            unit_id = unit.get("id")
            if not unit_id or unit.get("lifecycle_status") in {"REJECTED", "RETIRED"}:
                continue
            collection = self.route_collection(unit)
            task = repo.enqueue("knowledge_unit", int(unit_id), collection)
            tasks.append({"knowledge_unit_id": unit_id, "task_id": task.id, "target_collection": collection})
        return tasks

    def enqueue_unit_sync(self, unit: dict, *, delete: bool = False) -> list[dict]:
        unit_id = unit.get("id")
        if not unit_id:
            return []
        vector_status = unit.get("vector_status") or {}
        indexed_collections = vector_status.get("indexed_collections") or []
        routed_collection = self.route_collection(unit)
        if delete or unit.get("lifecycle_status") in {"REJECTED", "RETIRED"}:
            collections = list(dict.fromkeys([*indexed_collections, routed_collection]))
            task_type = "delete"
        else:
            collections = [routed_collection]
            task_type = "upsert"
        repo = VectorTaskRepository()
        tasks = []
        for collection in collections:
            task = repo.enqueue("knowledge_unit", int(unit_id), collection, task_type=task_type)
            tasks.append(
                {
                    "knowledge_unit_id": unit_id,
                    "task_id": task.id,
                    "task_type": task_type,
                    "target_collection": collection,
                }
            )
        return tasks

    @classmethod
    def route_collection(cls, unit: dict[str, Any]) -> str:
        if unit.get("knowledge_kind") == "ACTION":
            return cls.COLLECTIONS["ACTION"]
        if unit.get("temporal_class") == "DURABLE":
            return cls.COLLECTIONS["DURABLE"]
        return cls.COLLECTIONS["TIMED"]
