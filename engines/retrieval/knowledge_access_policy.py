from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_DENIED_REVIEW_STATUS = frozenset({"REJECTED"})

_STATE_INTENTS = frozenset({"current_state", "trading_decision", "market_opportunity_scan", "strategy_question"})


@dataclass(frozen=True)
class AccessRule:
    """Central knowledge access rule for one consumer intent (design doc §82-83).

    Axes:
    - source support: minimum support_status level (see RetrievalPolicy.SUPPORT_ORDER)
    - support score: minimum support_probability (score semantics until calibration lands)
    - truth: allowed truth_status values, None means no truth requirement
    - review: review_status values that must never be returned
    - valid_only: expired knowledge (valid_to in the past) is excluded
    """

    min_source_support: str
    min_support_score: float | None
    allowed_truth_status: frozenset[str] | None
    denied_review_status: frozenset[str]
    valid_only: bool


class KnowledgeAccessPolicy:
    """Intent -> AccessRule. Single source of truth for quality gates (§33)."""

    @classmethod
    def for_intent(cls, intent: str) -> AccessRule:
        normalized = (intent or "").strip().lower()
        if normalized == "factual_qa":
            return AccessRule(
                min_source_support="SOURCE_SUPPORTED",
                min_support_score=0.7,
                allowed_truth_status=frozenset({"EXTERNALLY_VERIFIED"}),
                denied_review_status=_DEFAULT_DENIED_REVIEW_STATUS,
                valid_only=True,
            )
        if normalized in _STATE_INTENTS:
            return AccessRule(
                min_source_support="SOURCE_SUPPORTED",
                min_support_score=0.7,
                allowed_truth_status=None,
                denied_review_status=_DEFAULT_DENIED_REVIEW_STATUS,
                valid_only=True,
            )
        # author_viewpoint / general_research / unknown intents (§83)
        return AccessRule(
            min_source_support="SOURCE_SUPPORTED",
            min_support_score=0.6,
            allowed_truth_status=None,
            denied_review_status=_DEFAULT_DENIED_REVIEW_STATUS,
            valid_only=False,
        )


def to_filters(rule: AccessRule) -> dict:
    """Convert an AccessRule into the retrieval filter dict shared by
    HybridRetriever, the repository layer and MCP-facing services.

    Keys: minimum_support_status / minimum_support_probability / truth_status /
    denied_review_status / valid_only.
    """
    filters: dict = {
        "minimum_support_status": rule.min_source_support,
        "denied_review_status": sorted(rule.denied_review_status),
        "valid_only": rule.valid_only,
    }
    if rule.min_support_score is not None:
        filters["minimum_support_probability"] = rule.min_support_score
    if rule.allowed_truth_status:
        if len(rule.allowed_truth_status) == 1:
            filters["truth_status"] = next(iter(rule.allowed_truth_status))
        else:
            filters["truth_status"] = sorted(rule.allowed_truth_status)
    return filters
