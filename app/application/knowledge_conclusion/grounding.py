"""Pure validation of candidate findings against a frozen content bundle."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.application.knowledge_conclusion.bundle_validator import canonical_json
from app.domain.content_fact_tokens import extract_hard_facts, mapping_entities
from app.domain.knowledge_conclusion import Finding


class GroundingReason(StrEnum):
    CONCLUSION_UNGROUNDED = "CONCLUSION_UNGROUNDED"
    CITATION_INVALID = "CITATION_INVALID"
    CONFIDENCE_INVALID = "CONFIDENCE_INVALID"
    POLARITY_UNSUPPORTED = "POLARITY_UNSUPPORTED"


class GroundingError(ValueError):
    def __init__(self, reason: GroundingReason, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    knowledge_id: str
    text: str
    source_identity: str
    extraction_confidence: float
    verification_weight: float
    quality: float
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class KnowledgeRecord:
    knowledge_id: str
    text: str
    evidence: tuple[EvidenceRecord, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class GroundedFinding:
    finding: Finding
    knowledge: tuple[KnowledgeRecord, ...]
    evidence: tuple[EvidenceRecord, ...]
    polarity: int


@dataclass(frozen=True)
class CitationEdge:
    """One evidence-to-knowledge edge as owned by the frozen producer Bundle."""

    knowledge_id: str
    evidence_id: str
    quote_hash: str
    quote_hash_provenance: str


def _as_records(bundle: Mapping[str, Any], *names: str) -> Sequence[Mapping[str, Any]]:
    for name in names:
        value = bundle.get(name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _identifier(record: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _text(record: Mapping[str, Any]) -> str:
    # ``statement`` is the public atomic-claim text in the locked Bundle.
    # Older test vectors used ``text``; retain that projection only for replay
    # compatibility, never as an Agent-authored paraphrase.
    for name in ("statement", "text", "content", "quote", "body", "summary"):
        value = record.get(name)
        if isinstance(value, str):
            return value
    return ""


def _knowledge_text(record: Mapping[str, Any]) -> str:
    """Producer claim fields available to a cited finding, in source order."""
    values = [_text(record)]
    for name in ("condition", "invalidation"):
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            values.append(value)
    return "\n".join(dict.fromkeys(value for value in values if value))


def _number(record: Mapping[str, Any], names: tuple[str, ...], default: float) -> float:
    for name in names:
        value = record.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            return float(value)
    return default


def _source_identity(record: Mapping[str, Any], fallback: Mapping[str, Any] | None = None) -> str:
    for name in ("source_identity", "author", "source_id", "publisher", "source"):
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if fallback is not None:
        for name in ("source_identity", "source_identity_hash", "author", "source_id", "publisher"):
            value = fallback.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    # An absent source identity cannot be promoted into independent evidence.
    return "content-internal"


def bundle_graph(bundle: Mapping[str, Any]) -> tuple[KnowledgeRecord, ...]:
    """Normalize a small, internal fixture protocol into ownership records.

    The accepted fixture shape is ``knowledge[]`` with nested ``evidence[]``;
    flat ``evidence[]`` is accepted only when it names its ``knowledge_id``.
    """
    if not isinstance(bundle, Mapping):
        raise TypeError("frozen bundle must be a mapping")
    flat = _as_records(bundle, "evidence", "evidences")
    bundle_source = bundle.get("source") if isinstance(bundle.get("source"), Mapping) else None
    result: list[KnowledgeRecord] = []
    for knowledge in _as_records(bundle, "knowledge", "knowledge_items", "items"):
        knowledge_id = _identifier(knowledge, "knowledge_id", "id")
        if not knowledge_id:
            continue
        nested = _as_records(knowledge, "evidence", "evidences")
        evidence_rows = nested or tuple(row for row in flat if _identifier(row, "knowledge_id", "owner_knowledge_id") == knowledge_id)
        evidence: list[EvidenceRecord] = []
        for row in evidence_rows:
            evidence_id = _identifier(row, "evidence_id", "id")
            owner = _identifier(row, "knowledge_id", "owner_knowledge_id") or knowledge_id
            if evidence_id and owner == knowledge_id:
                evidence.append(EvidenceRecord(
                    evidence_id=evidence_id, knowledge_id=knowledge_id, text=_text(row),
                    source_identity=_source_identity(
                        row,
                        {**(bundle_source or {}), **knowledge},
                    ),
                    extraction_confidence=_number(row, ("extraction_confidence", "confidence"), 1.0),
                    verification_weight=_number(row, ("verification_weight", "verification"), 1.0),
                    quality=_number(row, ("quality", "quality_score"), 1.0), raw=row,
                ))
        result.append(KnowledgeRecord(knowledge_id, _knowledge_text(knowledge), tuple(evidence), knowledge))
    return tuple(result)


def _polarity(text: str) -> int:
    lowered = text.casefold()
    positive = ("增长", "上升", "改善", "提高", "扩大", "increase", "growth", "rise", "improve")
    negative = ("下降", "下滑", "减少", "收缩", "恶化", "decrease", "decline", "fall", "deteriorat")
    score = sum(word in lowered for word in positive) - sum(word in lowered for word in negative)
    return (score > 0) - (score < 0)


def ground_finding(bundle: Mapping[str, Any], finding: Finding) -> GroundedFinding:
    """Fail closed unless citations, facts, and stated direction are supported."""
    if not math.isfinite(finding.confidence) or not 0 <= finding.confidence <= 1:
        raise GroundingError(GroundingReason.CONFIDENCE_INVALID, "finding confidence must be finite and in range")
    graph = bundle_graph(bundle)
    by_knowledge = {row.knowledge_id: row for row in graph}
    if any(identifier not in by_knowledge for identifier in finding.knowledge_ids):
        raise GroundingError(GroundingReason.CITATION_INVALID, "cited knowledge is absent")
    cited_knowledge = tuple(by_knowledge[identifier] for identifier in finding.knowledge_ids)
    by_evidence = {row.evidence_id: row for knowledge in cited_knowledge for row in knowledge.evidence}
    if any(identifier not in by_evidence for identifier in finding.evidence_ids):
        raise GroundingError(GroundingReason.CITATION_INVALID, "evidence is absent or owned by uncited knowledge")
    cited_evidence = tuple(by_evidence[identifier] for identifier in finding.evidence_ids)
    if any(row.knowledge_id not in finding.knowledge_ids for row in cited_evidence):
        raise GroundingError(GroundingReason.CITATION_INVALID, "evidence ownership does not match a cited knowledge item")
    if {row.knowledge_id for row in cited_evidence} != set(finding.knowledge_ids):
        raise GroundingError(GroundingReason.CITATION_INVALID, "each cited knowledge item must own cited evidence")
    source_text = "\n".join((*(row.text for row in cited_knowledge), *(row.text for row in cited_evidence)))
    entities = tuple(entity for row in (*cited_knowledge, *cited_evidence) for entity in mapping_entities(row.raw))
    stated = _polarity(finding.text)
    supported = _polarity(source_text)
    if stated and supported and stated != supported:
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "finding direction conflicts with cited content")
    extra = extract_hard_facts(finding.text, entities=entities) - extract_hard_facts(source_text, entities=entities)
    if extra:
        raise GroundingError(GroundingReason.CONCLUSION_UNGROUNDED, "unsupported hard facts: " + ", ".join(sorted(extra)))
    return GroundedFinding(finding, cited_knowledge, cited_evidence, stated)


def ground_findings(bundle: Mapping[str, Any], findings: Sequence[Finding]) -> tuple[GroundedFinding, ...]:
    return tuple(ground_finding(bundle, finding) for finding in findings)


def validate_research_semantics(
    bundle: Mapping[str, Any],
    findings: Sequence[Finding],
    *,
    verdict: str,
    market_stance: str,
    minimum_independent_sources: int = 1,
) -> tuple[GroundedFinding, ...]:
    """Bind a model's directional labels to frozen, independently sourced facts.

    This deliberately knows nothing about orders or tradable instruments.  It
    only prevents a model from assigning a positive/negative research label
    that the cited content cannot bear.  Repeated clips from one author remain
    a single source identity.
    """
    if minimum_independent_sources < 1:
        raise ValueError("minimum independent sources must be positive")
    grounded = ground_findings(bundle, findings)
    sources = {evidence.source_identity for item in grounded for evidence in item.evidence}
    positive = sum(_evidence_weight(item) for item in grounded if item.polarity > 0)
    negative = sum(_evidence_weight(item) for item in grounded if item.polarity < 0)
    independent = len(sources) >= minimum_independent_sources
    if verdict == "SUPPORTED" and not (independent and positive > negative and positive > 0):
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "supported verdict lacks independent positive evidence")
    if verdict == "CONTRADICTED" and not (independent and negative > positive and negative > 0):
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "contradicted verdict lacks independent negative evidence")
    if verdict == "MIXED" and not (independent and positive > 0 and negative > 0):
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "mixed verdict lacks both directions")
    if verdict == "INSUFFICIENT_EVIDENCE" and market_stance in {"BULLISH", "BEARISH"}:
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "insufficient evidence cannot carry a directional stance")
    if market_stance == "BULLISH" and not (independent and positive > negative and positive > 0):
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "bullish stance lacks independent positive evidence")
    if market_stance == "BEARISH" and not (independent and negative > positive and negative > 0):
        raise GroundingError(GroundingReason.POLARITY_UNSUPPORTED, "bearish stance lacks independent negative evidence")
    return grounded


def _evidence_weight(item: GroundedFinding) -> float:
    # Deduplicate an evidence record once per finding.  Identity (rather than
    # count) is the independence gate above; weights only rank grounded facts.
    seen: set[str] = set()
    return sum(
        evidence.extraction_confidence * evidence.verification_weight * min(max(evidence.quality, 0.0), 1.0)
        for evidence in item.evidence
        if not (evidence.evidence_id in seen or seen.add(evidence.evidence_id))
    )


def citation_edges(bundle: Mapping[str, Any], finding: Finding) -> tuple[CitationEdge, ...]:
    """Derive explicit citation pairs and quote identity from frozen evidence.

    Findings name sets of IDs for the structured-model contract, but they do
    not authorize a Cartesian product. The producer's nested evidence record
    is the sole owner of a knowledge/evidence pair.
    """
    grounded = ground_finding(bundle, finding)
    return tuple(
        CitationEdge(
            knowledge_id=evidence.knowledge_id,
            evidence_id=evidence.evidence_id,
            quote_hash=_quote_hash(evidence),
            quote_hash_provenance=_quote_hash_provenance(evidence),
        )
        for evidence in sorted(grounded.evidence, key=lambda row: (row.knowledge_id, row.evidence_id))
    )


def _quote_hash(evidence: EvidenceRecord) -> str:
    """Use producer c14n for the public quote primitive, never finding text."""
    quote = _producer_quote(evidence)
    computed = hashlib.sha256(canonical_json(quote)).hexdigest()
    supplied = evidence.raw.get("quote_hash")
    if supplied is None:
        return computed
    if not isinstance(supplied, str) or supplied not in {computed, "sha256:" + computed}:
        raise GroundingError(GroundingReason.CITATION_INVALID, "producer quote hash does not match evidence quote")
    return supplied


def _quote_hash_provenance(evidence: EvidenceRecord) -> str:
    return "PRODUCER_EXPLICIT" if evidence.raw.get("quote_hash") is not None else "DERIVED_FROM_PRODUCER_QUOTE"


def _producer_quote(evidence: EvidenceRecord) -> str:
    # ``quote`` is the current Bundle projection. Historical v1 fixtures used
    # ``text`` for the same public evidence quote; accept it only as that
    # producer projection, never as Agent finding text.
    quote = evidence.raw.get("quote")
    if not isinstance(quote, str) or not quote:
        quote = evidence.raw.get("text")
    if not isinstance(quote, str) or not quote:
        raise GroundingError(GroundingReason.CITATION_INVALID, "producer evidence quote missing")
    return quote
