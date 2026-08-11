from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever
from engines.retrieval.filters import normalize_retrieval_filters
from engines.memory.evidence import load_memory_config
from engines.memory.memory_scorer import MemoryScorer
from storage.repositories.vector_repository import MemoryRepository


def retrieve_memory(
    query: str,
    filters: dict | None = None,
    top_k: int = 5,
    memory_types: list[str] | None = None,
    market_regime: str | None = None,
    *,
    horizon_days: int | None = None,
    theme: str | None = None,
    market: str | None = None,
) -> dict:
    pushed_filters = normalize_retrieval_filters(filters)
    if memory_types:
        pushed_filters["memory_type"] = list(dict.fromkeys([*(pushed_filters.get("memory_type") or []), *memory_types]))
    # Type constraints are applied in both dense and sparse recall before the
    # candidate limit, so a crowded unrelated Top-N cannot hide typed memory.
    scope_context = {"regime": market_regime, "horizon_days": horizon_days, "theme": theme, "market": market}
    scope_active = any(value is not None for value in scope_context.values())
    fetch_k = top_k
    if scope_active:
        # Scope is a hard pre-ranking filter, so over-fetch to keep top_k filled
        # after non-applicable memories are dropped.
        factor = int(load_memory_config()["scope"].get("overfetch_factor") or 1)
        fetch_k = max(top_k, top_k * factor)
    result = HybridRetriever().retrieve(query=query, task_type="memory_lookup", filters=pushed_filters or None, top_k=fetch_k)
    contexts = [item for item in result.get("contexts", []) if item.get("record") and (not memory_types or item["record"].get("memory_type") in memory_types)]
    if scope_active:
        contexts = [item for item in contexts if _scope_applies(item.get("record") or {}, scope_context)]
    result["contexts"] = MemoryScorer().rank(contexts, market_regime)[:top_k]
    result["memories"] = result["contexts"]
    return result


def scope_matches(scope: dict, context: dict) -> bool:
    """Hard applicability gate: every non-null scope dimension must match the context.

    A null scope dimension matches everything (unrestricted memory). A missing
    context dimension also passes — there is no information to disqualify on.
    List dimensions (regimes/styles/themes) match on membership; scalar
    dimensions (market/horizon) match on equality.
    """
    regimes = scope.get("applicable_regimes")
    if regimes and context.get("regime") is not None and context["regime"] not in regimes:
        return False
    themes = scope.get("applicable_themes")
    if themes and context.get("theme") is not None and context["theme"] not in themes:
        return False
    market_scope = scope.get("applicable_market")
    if market_scope and context.get("market") is not None and str(context["market"]) != str(market_scope):
        return False
    horizon_scope = scope.get("applicable_horizon")
    if horizon_scope is not None and context.get("horizon_days") is not None and int(context["horizon_days"]) != int(horizon_scope):
        return False
    return True


def _scope_applies(record: dict, context: dict) -> bool:
    scope = _record_scope(record)
    if scope is None:
        return True
    return scope_matches(scope, context)


def _record_scope(record: dict) -> dict | None:
    """Load scope columns for a hydrated memory record; None = unrestricted/unknown."""
    record_id = record.get("id")
    try:
        memory_id = int(record_id)
    except (TypeError, ValueError):
        return None
    try:
        memory = MemoryRepository().get(memory_id)
    except Exception:  # noqa: BLE001 - fail open: no scope data, no filtering
        return None
    if memory is None:
        return None
    return {
        "applicable_market": memory.applicable_market,
        "applicable_regimes": memory.applicable_regimes,
        "applicable_styles": memory.applicable_styles,
        "applicable_horizon": memory.applicable_horizon,
        "applicable_themes": memory.applicable_themes,
    }
