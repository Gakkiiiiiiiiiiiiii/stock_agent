"""Deterministic construction seam; no network, model, or persistence calls."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol

from app.domain.knowledge_conclusion import (
    ConclusionVerdict,
    Finding,
    KnowledgeConclusion,
    KnowledgeConclusionRequest,
    ModelIdentity,
)


class TraceContext(Protocol):
    """Minimal trace shape without importing a gateway or infrastructure adapter."""

    trace_id: str


class KnowledgeConclusionService:
    """Construct a content-only conclusion from already validated findings."""

    @staticmethod
    def request_hash(request: KnowledgeConclusionRequest) -> str:
        """Hash only explicit request data; this pure layer never reads wall time."""
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def conclude(
        self,
        request: KnowledgeConclusionRequest,
        *,
        idempotency_key: str,
        trace: TraceContext,
        conclusion_id: str,
        content_bundle_id: str,
        verdict: ConclusionVerdict,
        market_stance: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"],
        summary: Finding,
        findings: Iterable[Finding],
        conditions: Iterable[Finding] = (),
        risks: Iterable[Finding] = (),
        contradictions: Iterable[Finding] = (),
        limitations: Iterable[Finding] = (),
        model: ModelIdentity,
        created_at: datetime,
    ) -> KnowledgeConclusion:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be nonblank")
        if not trace.trace_id.strip():
            raise ValueError("trace.trace_id must be nonblank")
        return KnowledgeConclusion.construct(
            request=request,
            conclusion_id=conclusion_id,
            content_bundle_id=content_bundle_id,
            verdict=verdict,
            market_stance=market_stance,
            summary=summary,
            findings=findings,
            conditions=conditions,
            risks=risks,
            contradictions=contradictions,
            limitations=limitations,
            model=model,
            created_at=created_at,
        )
