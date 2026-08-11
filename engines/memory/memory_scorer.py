from __future__ import annotations

from datetime import UTC, datetime

from engines.memory.evidence import load_memory_config


class MemoryScorer:
    def __init__(self, config: dict | None = None) -> None:
        self._bonus_config = (config if config is not None else load_memory_config()).get("scorer") or {}

    def score(self, context: dict, market_regime: str | None = None, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        record = context.get("record") or {}
        semantic = float(context.get("final_score") or context.get("rerank_score") or 0)
        importance = {"low": 0.25, "medium": 0.5, "high": 0.85}.get(str(record.get("importance", "medium")).lower(), 0.5)
        confidence = float(record.get("confidence") or 0.5)
        source_date = record.get("source_date") or context.get("source_date")
        recency = self._recency(source_date, now)
        regime_match = 1.0 if market_regime and record.get("related_regime") == market_regime else 0.5
        metadata = record.get("metadata_json") or {}
        outcome_relevance = float(metadata.get("outcome_relevance", 0.5))
        base = 0.30 * semantic + 0.20 * importance + 0.15 * confidence + 0.15 * recency + 0.10 * regime_match + 0.10 * outcome_relevance
        # Config-gated additive bonus: the lifecycle's evidence-weighted confidence.
        weighted = metadata.get("weighted_confidence")
        if weighted is not None and self._bonus_config.get("confidence_bonus_enabled"):
            base += float(self._bonus_config.get("confidence_bonus_weight", 0.05)) * float(weighted)
        return round(base, 6)

    def rank(self, contexts: list[dict], market_regime: str | None = None) -> list[dict]:
        for context in contexts:
            context["memory_score"] = self.score(context, market_regime)
        return sorted(contexts, key=lambda item: item["memory_score"], reverse=True)

    @staticmethod
    def _recency(value: object, now: datetime) -> float:
        if not value:
            return 0.5
        try:
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, 1.0 - (now - when).days / 365)
        except ValueError:
            return 0.5
