from __future__ import annotations

from engines.retrieval.knowledge_access_policy import KnowledgeAccessPolicy, to_filters


class RetrievalPolicy:
    """Quality gates for video-derived knowledge by consumer intent."""

    SUPPORT_ORDER = ("UNSUPPORTED", "SOURCE_LOCATED", "NEEDS_REVIEW", "SOURCE_SUPPORTED", "CROSS_MODAL_SUPPORTED", "EXTERNALLY_VERIFIED", "VALIDATED")

    @classmethod
    def filters_for(cls, task_type: str) -> dict:
        return to_filters(KnowledgeAccessPolicy.for_intent(task_type))

    @classmethod
    def allowed_statuses(cls, minimum: str | None) -> list[str]:
        if not minimum:
            return []
        try:
            return list(cls.SUPPORT_ORDER[cls.SUPPORT_ORDER.index(str(minimum).upper()) :])
        except ValueError:
            return [str(minimum).upper()]


def merge_policy_filters(policy: dict, requested: dict) -> dict:
    """Strictest merge of policy filters with caller-requested filters (§28).

    The caller may only tighten the policy, never relax it:
    - minimum_support_status: higher level in SUPPORT_ORDER wins
    - minimum_support_probability: max wins
    - truth_status: a policy requirement cannot be removed or widened
    - valid_only: policy=True cannot be turned False
    - denied_review_status: union of policy and caller denied sets
    - any other key is caller-controlled
    """
    policy = policy or {}
    merged = dict(requested or {})

    minimum = _stricter_support_status(policy.get("minimum_support_status"), merged.get("minimum_support_status"))
    if minimum:
        merged["minimum_support_status"] = minimum
    elif "minimum_support_status" in merged:
        del merged["minimum_support_status"]

    probabilities = [value for value in (policy.get("minimum_support_probability"), merged.get("minimum_support_probability")) if value is not None]
    if probabilities:
        merged["minimum_support_probability"] = max(float(value) for value in probabilities)
    elif "minimum_support_probability" in merged:
        del merged["minimum_support_probability"]

    policy_truth = policy.get("truth_status")
    if policy_truth is not None:
        merged["truth_status"] = _merge_truth_status(policy_truth, merged.get("truth_status"))

    if policy.get("valid_only"):
        merged["valid_only"] = True

    denied = {str(item).upper() for item in _as_list(policy.get("denied_review_status"))}
    denied |= {str(item).upper() for item in _as_list(merged.get("denied_review_status"))}
    if denied:
        merged["denied_review_status"] = sorted(denied)
    elif "denied_review_status" in merged:
        del merged["denied_review_status"]

    return merged


def _stricter_support_status(policy_minimum: str | None, requested_minimum: str | None) -> str | None:
    policy_rank = _support_rank(policy_minimum)
    requested_rank = _support_rank(requested_minimum)
    if policy_rank is None and requested_rank is None:
        return None
    if policy_rank is None:
        return str(requested_minimum).upper()
    if requested_rank is None:
        return str(policy_minimum).upper()
    return str(policy_minimum if policy_rank >= requested_rank else requested_minimum).upper()


def _support_rank(status: str | None) -> int | None:
    if not status:
        return None
    normalized = str(status).upper()
    try:
        return RetrievalPolicy.SUPPORT_ORDER.index(normalized)
    except ValueError:
        return None


def _merge_truth_status(policy_truth, requested_truth):
    """A policy truth requirement cannot be removed or widened by the caller.

    The caller may only narrow it to a subset of the policy-allowed values.
    """
    policy_allowed = set(_as_list(policy_truth))
    requested_allowed = set(_as_list(requested_truth))
    if requested_allowed and requested_allowed <= policy_allowed:
        return requested_truth
    return policy_truth


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]
