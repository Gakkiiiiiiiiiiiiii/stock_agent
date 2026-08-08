from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from engines.content.financial_entity_normalizer import FinancialEntityNormalizer
from engines.content.claim_evidence_verifier import ClaimEvidenceVerifier


class KnowledgeUnitNormalizer:
    def __init__(self, entity_normalizer: FinancialEntityNormalizer | None = None, verifier: ClaimEvidenceVerifier | None = None) -> None:
        self.entity_normalizer = entity_normalizer or FinancialEntityNormalizer()
        self.verifier = verifier or ClaimEvidenceVerifier()

    def normalize(self, units: list[dict], metadata: dict) -> list[dict]:
        source_date = self.parse_source_datetime(metadata.get("publish_time"))
        normalized: list[dict] = []
        for index, unit in enumerate(units, start=1):
            statement = self._clean_statement(unit.get("statement") or "")
            if not statement or not unit.get("evidence"):
                continue
            canonical = self._canonicalize(statement)
            entities = self._normalize_entities(unit, metadata)
            subject = self._infer_subject(unit, entities)
            subject_key = unit.get("subject_key") or subject.get("subject_key")
            if not subject_key:
                continue
            subject = {
                "subject_type": unit.get("subject_type") or subject.get("subject_type"),
                "subject_key": subject_key,
                "subject_name": unit.get("subject_name") or subject.get("subject_name"),
            }
            content_basis = "|".join(
                [
                    str(unit.get("chapter_index") or 0),
                    str(unit.get("primary_domain") or "GENERAL"),
                    str(unit.get("knowledge_kind") or "STATE"),
                    str(subject_key),
                    canonical,
                    str(unit.get("condition_text") or ""),
                    str(unit.get("invalidation_text") or ""),
                ]
            )
            content_hash = hashlib.sha256(content_basis.encode("utf-8")).hexdigest()
            semantic_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            uid_prefix = str(metadata.get("bvid") or metadata.get("platform_video_id") or metadata.get("platform") or "video")
            item = dict(unit)
            verification_status = unit.get("verification_status") or "SOURCE_LOCATED"
            verification = self.verifier.verify(unit)
            support_status = verification["support_status"]
            if self._low_evidence_quality(unit.get("evidence") or []):
                verification_status = "NEEDS_REVIEW"
                support_status = "NEEDS_REVIEW"
            elif support_status == "SOURCE_SUPPORTED":
                verification_status = "SOURCE_SUPPORTED"
            item.update(
                {
                    "knowledge_uid": f"ku_{uid_prefix}_{index:04d}_{content_hash[:10]}",
                    "statement": statement,
                    "canonical_statement": unit.get("canonical_statement") or canonical,
                    "entities": entities,
                    "subject_type": unit.get("subject_type") or subject.get("subject_type"),
                    "subject_key": unit.get("subject_key") or subject.get("subject_key"),
                    "subject_name": unit.get("subject_name") or subject.get("subject_name"),
                    "predicate_key": unit.get("predicate_key") or self._predicate_key(unit),
                    "content_hash": content_hash,
                    "semantic_hash": semantic_hash,
                    "conflict_key": self._conflict_key(unit, subject),
                    "scope_type": unit.get("scope_type") or subject.get("subject_type"),
                    "scope_key": unit.get("scope_key") or subject.get("subject_key"),
                    "verification_status": verification_status,
                    "support_status": support_status,
                    "support_probability": verification["support_probability"],
                    "truth_status": unit.get("truth_status") or "NOT_EXTERNALLY_VERIFIED",
                    "external_verification_status": unit.get("external_verification_status") or "NOT_RUN",
                    "speaker_id": unit.get("speaker_id") or self._speaker_id(unit.get("evidence") or []),
                    "speaker_name": unit.get("speaker_name"),
                    "attribution_confidence": unit.get("attribution_confidence"),
                    "attributes": (unit.get("attributes") or {}) | {"verification": verification},
                    "extractor_version": unit.get("extractor_version") or "v3.2-k3-json-mode",
                    "schema_version": "v1",
                    "as_of_time": unit.get("as_of_time") or source_date,
                }
            )
            normalized.append(item)
        return normalized

    @staticmethod
    def _speaker_id(evidence: list[dict]) -> str | None:
        return next((str(item.get("speaker_id")) for item in evidence if item.get("speaker_id")), None)

    @staticmethod
    def _low_evidence_quality(evidence: list[dict]) -> bool:
        if not evidence:
            return True
        primary = evidence[0]
        has_time_range = primary.get("start_ms") is not None and primary.get("end_ms") is not None
        confidence = primary.get("confidence_score")
        try:
            low_confidence = confidence is not None and float(confidence) < 0.45
        except (TypeError, ValueError):
            low_confidence = False
        return (not has_time_range) or low_confidence

    def _normalize_entities(self, unit: dict, metadata: dict) -> list[dict]:
        text = f"{unit.get('statement') or ''} {unit.get('evidence_text') or ''}"
        raw_entities = list(unit.get("entities") or [])
        extracted = self.entity_normalizer.extract_entities(text, "", metadata.get("title") or "")
        for entity in extracted:
            raw_entities.append(
                {
                    "entity_type": entity.get("entity_type") or "UNKNOWN",
                    "entity_key": entity.get("ticker") or entity.get("name"),
                    "entity_name": entity.get("name") or entity.get("ticker") or "UNKNOWN",
                    "ticker": entity.get("ticker"),
                    "relation_role": "SUBJECT",
                    "confidence_score": entity.get("confidence_score") or 0.7,
                }
            )
        deduped: dict[tuple[str, str], dict] = {}
        for entity in raw_entities:
            name = str(entity.get("entity_name") or entity.get("name") or entity.get("ticker") or "").strip()
            if not name:
                continue
            entity_type = str(entity.get("entity_type") or "UNKNOWN")
            key = str(entity.get("entity_key") or entity.get("ticker") or name)
            deduped[(entity_type, key)] = {
                "entity_type": entity_type,
                "entity_key": key,
                "entity_name": name,
                "ticker": entity.get("ticker"),
                "relation_role": entity.get("relation_role") or "RELATED",
                "confidence_score": entity.get("confidence_score") or 0.7,
            }
        return list(deduped.values())[:12]

    @staticmethod
    def parse_source_datetime(raw_value: str | None) -> datetime | None:
        text = str(raw_value or "").strip()
        if len(text) == 8 and text.isdigit():
            try:
                return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                return None
        return None

    @staticmethod
    def _clean_statement(value: object) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:1000]

    @staticmethod
    def _canonicalize(value: str) -> str:
        text = re.sub(r"\s+", "", value.strip())
        return text[:1000]

    @staticmethod
    def _infer_subject(unit: dict, entities: list[dict]) -> dict:
        for entity in entities:
            role = str(entity.get("relation_role") or "")
            if role == "SUBJECT" or entity.get("ticker"):
                return {
                    "subject_type": entity.get("entity_type"),
                    "subject_key": entity.get("entity_key") or entity.get("ticker") or entity.get("entity_name"),
                    "subject_name": entity.get("entity_name"),
                }
        return {}

    @staticmethod
    def _predicate_key(unit: dict) -> str:
        kind = str(unit.get("knowledge_kind") or "STATE").lower()
        statement = str(unit.get("statement") or "")
        if "支撑" in statement:
            return "support_level"
        if "压力" in statement or "阻力" in statement:
            return "resistance_level"
        if "减仓" in statement:
            return "reduce_position"
        if "加仓" in statement:
            return "increase_position"
        return kind

    @staticmethod
    def _conflict_key(unit: dict, subject: dict) -> str:
        return "|".join(
            [
                str(unit.get("primary_domain") or "GENERAL"),
                str(unit.get("knowledge_kind") or "STATE"),
                str(subject.get("subject_key") or "UNKNOWN"),
                KnowledgeUnitNormalizer._predicate_key(unit),
                str(unit.get("timeframe") or ""),
            ]
        )
