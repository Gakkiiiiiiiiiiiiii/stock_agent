"""Realtime overlay refresh; deliberately produces no orders or portfolio actions."""
from __future__ import annotations

from engines.opportunity.service import OpportunityRankingService


def refresh_opportunities(candidates: list[dict], realtime_by_symbol: dict[str, dict], context: dict | None = None) -> dict:
    """Re-rank daily candidates after attaching their persisted stream overlay."""
    enriched = []
    for candidate in candidates:
        item = dict(candidate)
        overlay = realtime_by_symbol.get(str(item.get("symbol")), {})
        item["realtime_overlay"] = dict(overlay)
        # This is a transparent overlay feature; the existing ranking service
        # remains the sole scorer and no execution object is created here.
        if overlay.get("return_5m") is not None:
            item["technical_score"] = float(item.get("technical_score", 0.0)) + float(overlay["return_5m"]) * 100.0
        enriched.append(item)
    result = OpportunityRankingService().rank(enriched, context or {})
    return {"ranked": result, "refresh_type": "REALTIME_OPPORTUNITY_ONLY"}
