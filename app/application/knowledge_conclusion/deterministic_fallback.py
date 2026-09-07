"""No-model, deterministic conclusion construction from grounded findings."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from app.application.knowledge_conclusion.grounding import (
    GroundedFinding,
    bundle_graph,
    ground_findings,
)
from app.domain.knowledge_conclusion import (
    ConclusionVerdict,
    Finding,
    KnowledgeConclusion,
    KnowledgeConclusionRequest,
    ModelIdentity,
)


def aggregate_bundle_findings(bundle: dict[str, object]) -> tuple[Finding, ...]:
    """Project frozen atomic claims into cited research findings.

    The producer owns both the claim text and evidence pairing.  This adapter
    copies neither a model interpretation nor an investment instruction: it
    only makes validated Bundle knowledge available to the deterministic path
    when a structured model is unavailable.
    """
    findings: list[Finding] = []
    for knowledge in bundle_graph(bundle):
        if not knowledge.evidence or not knowledge.text.strip():
            continue
        try:
            findings.append(Finding(
                text=knowledge.text.split("\n", 1)[0],
                knowledge_ids=(knowledge.knowledge_id,),
                evidence_ids=tuple(item.evidence_id for item in knowledge.evidence),
                confidence=min(1.0, max(0.0, _raw_confidence(knowledge))),
            ))
        except ValueError:
            # A producer statement containing action language is not safe for
            # public research output.  It remains frozen provenance, but is
            # not silently rewritten into an Agent conclusion.
            continue
    return tuple(findings)


def _raw_confidence(knowledge: GroundedFinding | object) -> float:
    evidence = getattr(knowledge, "evidence", ())
    if not evidence:
        return 0.0
    return sum(item.extraction_confidence * item.verification_weight * min(max(item.quality, 0.0), 1.0) for item in evidence) / len(evidence)


def _weight(item: GroundedFinding) -> float:
    evidence = item.evidence
    if not evidence:
        return 0.0
    sources = {row.source_identity for row in evidence}
    # Multiple segments from one author are content-internal corroboration, not
    # independent verification.  A second source gives only a bounded boost.
    independence = 1.0 if len(sources) == 1 else 1.25
    return sum(row.extraction_confidence * row.verification_weight * min(max(row.quality, 0.0), 1.0) for row in evidence) * independence


def _ordered(items: Sequence[GroundedFinding]) -> tuple[GroundedFinding, ...]:
    return tuple(sorted(items, key=lambda item: (item.finding.knowledge_ids, item.finding.evidence_ids, item.finding.text)))


def _selection(items: Sequence[GroundedFinding]) -> tuple[Finding, ...]:
    return tuple(item.finding for item in _ordered(items))


def _verdict(items: Sequence[GroundedFinding]) -> ConclusionVerdict:
    if not items or (len(items) == 1 and _weight(items[0]) < 0.5):
        return ConclusionVerdict.INSUFFICIENT_EVIDENCE
    positive = _deduplicated_weight(items, 1)
    negative = _deduplicated_weight(items, -1)
    if not positive and not negative:
        return ConclusionVerdict.INSUFFICIENT_EVIDENCE
    if negative and negative > positive * 1.5:
        return ConclusionVerdict.CONTRADICTED
    if positive and negative and min(positive, negative) / max(positive, negative) >= 0.67:
        return ConclusionVerdict.MIXED
    return ConclusionVerdict.SUPPORTED


def _deduplicated_weight(items: Sequence[GroundedFinding], polarity: int) -> float:
    """Count a repeated evidence ID only once for one directional group."""
    seen: set[str] = set()
    total = 0.0
    for item in _ordered(items):
        if item.polarity != polarity:
            continue
        unseen = tuple(row for row in item.evidence if row.evidence_id not in seen)
        seen.update(row.evidence_id for row in unseen)
        if unseen:
            total += _weight(GroundedFinding(item.finding, item.knowledge, unseen, item.polarity))
    return total


def fallback_conclusion(
    *, request: KnowledgeConclusionRequest, bundle: dict[str, object], findings: Sequence[Finding],
    content_bundle_id: str, conclusion_id: str | None, created_at: datetime,
) -> KnowledgeConclusion:
    """Return a cited template selection; it never invokes a model or a tool."""
    # Actual production fallbacks receive no model findings.  Project the
    # frozen Bundle first so useful grounded research is retained rather than
    # collapsing every successful ingestion into an empty insufficiency.
    source_findings = tuple(findings) or aggregate_bundle_findings(bundle)
    grounded = ground_findings(bundle, source_findings)
    had_grounded_item = bool(grounded)
    if not grounded:
        first = next((knowledge for knowledge in bundle_graph(bundle) if knowledge.evidence), None)
        if first is None:
            raise ValueError("fallback requires at least one cited evidence record")
        empty = Finding(
            text="The cited content is insufficient for a content-only research conclusion.",
            knowledge_ids=(first.knowledge_id,),
            evidence_ids=(first.evidence[0].evidence_id,),
            confidence=0.0,
        )
        grounded = ground_findings(bundle, (empty,))
    verdict = _verdict(grounded) if had_grounded_item else ConclusionVerdict.INSUFFICIENT_EVIDENCE
    selected = _selection(grounded)
    summary = selected[0]
    identity = conclusion_id or hashlib.sha256((content_bundle_id + request.content_snapshot_id).encode()).hexdigest()[:24]
    stance: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"] = "UNCERTAIN"
    return KnowledgeConclusion.construct(
        request=request, conclusion_id=identity, content_bundle_id=content_bundle_id, verdict=verdict,
        market_stance=stance, summary=summary, findings=selected,
        conditions=(), risks=(), contradictions=selected[1:] if verdict in (ConclusionVerdict.MIXED, ConclusionVerdict.CONTRADICTED) else (),
        limitations=selected if verdict == ConclusionVerdict.INSUFFICIENT_EVIDENCE else (),
        model=ModelIdentity(mode="FALLBACK", provider="content-internal", model="deterministic-grounding-v1"), created_at=created_at,
    )
