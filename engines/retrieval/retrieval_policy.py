from __future__ import annotations


class RetrievalPolicy:
    """Quality gates for video-derived knowledge by consumer intent."""

    SUPPORT_ORDER = ("UNSUPPORTED", "SOURCE_LOCATED", "NEEDS_REVIEW", "SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED", "EXTERNALLY_VERIFIED", "VALIDATED")

    @classmethod
    def filters_for(cls, task_type: str) -> dict:
        if task_type in {"factual_qa"}:
            return {"truth_status": "EXTERNALLY_VERIFIED", "minimum_support_status": "EXTERNALLY_VERIFIED", "minimum_support_probability": 0.9}
        if task_type in {"trading_decision", "current_state", "market_opportunity_scan", "strategy_question"}:
            return {"minimum_support_status": "SOURCE_SUPPORTED", "minimum_support_probability": 0.7}
        if task_type == "author_viewpoint":
            return {"minimum_support_status": "SOURCE_SUPPORTED", "minimum_support_probability": 0.6}
        return {"minimum_support_status": "SOURCE_SUPPORTED", "minimum_support_probability": 0.6}

    @classmethod
    def allowed_statuses(cls, minimum: str | None) -> list[str]:
        if not minimum:
            return []
        try:
            return list(cls.SUPPORT_ORDER[cls.SUPPORT_ORDER.index(str(minimum).upper()) :])
        except ValueError:
            return [str(minimum).upper()]
