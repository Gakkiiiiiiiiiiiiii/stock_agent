from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from storage.db import session_scope
from storage.models.knowledge import (
    KnowledgeEntityRelation,
    KnowledgeEvidence,
    KnowledgeExtractionRun,
    KnowledgeUnit,
    KnowledgeUnitRelation,
    VideoAnalysisDocument,
    VideoChapter,
)
from storage.repositories.vector_repository import VectorTaskRepository


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, default: object) -> object:
    if not value:
        return default
    return json.loads(value)


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

    def list_units_for_video(self, video_id: int) -> list[dict]:
        with session_scope() as session:
            rows = list(session.execute(select(KnowledgeUnit).where(KnowledgeUnit.source_video_id == video_id).order_by(KnowledgeUnit.id.asc())).scalars())
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
            return [self._serialize_unit(row, evidence_by_unit.get(row.id, []), entity_by_unit.get(row.id, [])) for row in rows]

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
            return self._serialize_unit(row, evidence, entities)

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
    def _serialize_unit(row: KnowledgeUnit, evidence: list[dict], entities: list[dict]) -> dict:
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
            "content_hash": row.content_hash,
            "semantic_hash": row.semantic_hash,
            "attributes": _loads(row.attributes_json, {}),
            "evidence": evidence,
            "entities": entities,
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

    def finish(self, run_id: int, *, status: str, stage: str, chapter_count: int, knowledge_unit_count: int, degraded: bool = False, error_message: str | None = None) -> None:
        with session_scope() as session:
            row = session.get(KnowledgeExtractionRun, run_id)
            if row is None:
                return
            row.status = status
            row.stage = stage
            row.chapter_count = chapter_count
            row.knowledge_unit_count = knowledge_unit_count
            row.degraded = degraded
            row.error_message = error_message
            row.completed_at = datetime.now(UTC)
            session.add(row)


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

    @classmethod
    def route_collection(cls, unit: dict[str, Any]) -> str:
        if unit.get("knowledge_kind") == "ACTION":
            return cls.COLLECTIONS["ACTION"]
        if unit.get("temporal_class") == "DURABLE":
            return cls.COLLECTIONS["DURABLE"]
        return cls.COLLECTIONS["TIMED"]
