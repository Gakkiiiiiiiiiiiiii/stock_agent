from __future__ import annotations

from engines.retrieval.hybrid_retriever import HybridRetriever
from engines.retrieval.knowledge_access_policy import AccessRule, KnowledgeAccessPolicy, to_filters
from engines.retrieval.retrieval_policy import RetrievalPolicy, merge_policy_filters


def test_user_cannot_relax_minimum_support_status():
    policy = RetrievalPolicy.filters_for("current_state")
    merged = merge_policy_filters(policy, {"minimum_support_status": "SOURCE_LOCATED"})
    assert merged["minimum_support_status"] == "SOURCE_SUPPORTED"


def test_user_cannot_relax_minimum_support_score():
    policy = RetrievalPolicy.filters_for("current_state")
    merged = merge_policy_filters(policy, {"minimum_support_probability": 0.1})
    assert merged["minimum_support_probability"] == 0.7


def test_caller_can_tighten_minimum_support_status_and_score():
    policy = RetrievalPolicy.filters_for("general_research")
    merged = merge_policy_filters(
        policy,
        {"minimum_support_status": "EXTERNALLY_VERIFIED", "minimum_support_probability": 0.95},
    )
    assert merged["minimum_support_status"] == "EXTERNALLY_VERIFIED"
    assert merged["minimum_support_probability"] == 0.95


def test_policy_truth_status_cannot_be_removed_or_widened():
    policy = RetrievalPolicy.filters_for("factual_qa")
    assert policy["truth_status"] == "EXTERNALLY_VERIFIED"
    # caller tries to remove the truth requirement
    merged = merge_policy_filters(policy, {})
    assert merged["truth_status"] == "EXTERNALLY_VERIFIED"
    # caller tries to widen it
    merged = merge_policy_filters(policy, {"truth_status": "NOT_EXTERNALLY_VERIFIED"})
    assert merged["truth_status"] == "EXTERNALLY_VERIFIED"
    # caller may narrow within the policy-allowed set
    policy_set = dict(policy, truth_status=["EXTERNALLY_VERIFIED", "VALIDATED"])
    merged = merge_policy_filters(policy_set, {"truth_status": "EXTERNALLY_VERIFIED"})
    assert merged["truth_status"] == "EXTERNALLY_VERIFIED"


def test_policy_valid_only_cannot_be_turned_off():
    policy = RetrievalPolicy.filters_for("trading_decision")
    assert policy["valid_only"] is True
    merged = merge_policy_filters(policy, {"valid_only": False})
    assert merged["valid_only"] is True


def test_denied_review_status_is_merged_as_union():
    policy = RetrievalPolicy.filters_for("current_state")
    assert policy["denied_review_status"] == ["REJECTED"]
    merged = merge_policy_filters(policy, {"denied_review_status": ["NEEDS_MANUAL_REVIEW"]})
    assert merged["denied_review_status"] == ["NEEDS_MANUAL_REVIEW", "REJECTED"]


def test_caller_specific_keys_pass_through():
    policy = RetrievalPolicy.filters_for("general_research")
    merged = merge_policy_filters(policy, {"subject_key": "宁德时代"})
    assert merged["subject_key"] == "宁德时代"


def test_filters_for_matches_access_rule():
    assert RetrievalPolicy.filters_for("factual_qa") == to_filters(KnowledgeAccessPolicy.for_intent("factual_qa"))
    assert RetrievalPolicy.filters_for("author_viewpoint") == to_filters(KnowledgeAccessPolicy.for_intent("author_viewpoint"))


def test_access_policy_rules_by_intent():
    factual = KnowledgeAccessPolicy.for_intent("factual_qa")
    assert factual.min_source_support == "SOURCE_SUPPORTED"
    assert factual.min_support_score == 0.7
    assert factual.allowed_truth_status == frozenset({"EXTERNALLY_VERIFIED"})
    assert factual.denied_review_status == frozenset({"REJECTED"})
    assert factual.valid_only is True

    for intent in ("current_state", "trading_decision", "market_opportunity_scan", "strategy_question"):
        rule = KnowledgeAccessPolicy.for_intent(intent)
        assert rule.min_source_support == "SOURCE_SUPPORTED"
        assert rule.min_support_score == 0.7
        assert rule.allowed_truth_status is None
        assert rule.valid_only is True

    viewpoint = KnowledgeAccessPolicy.for_intent("author_viewpoint")
    assert viewpoint.min_support_score == 0.6
    assert viewpoint.allowed_truth_status is None
    assert viewpoint.valid_only is False

    assert KnowledgeAccessPolicy.for_intent("general_research") == viewpoint
    assert KnowledgeAccessPolicy.for_intent("something_unknown") == viewpoint


def test_access_rule_is_frozen():
    rule = KnowledgeAccessPolicy.for_intent("factual_qa")
    assert isinstance(rule, AccessRule)
    try:
        rule.min_support_score = 0.1  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("AccessRule must be immutable")


def _knowledge_candidate(chunk_id: str, **payload_overrides) -> dict:
    payload = {
        "postgres_table": "knowledge_unit",
        "support_status": "SOURCE_SUPPORTED",
        "support_probability": 0.9,
        "truth_status": "EXTERNALLY_VERIFIED",
    }
    payload.update(payload_overrides)
    return {"chunk_id": chunk_id, "payload": payload}


def test_quality_gate_drops_rejected_review_status():
    candidates = [
        _knowledge_candidate("ok"),
        _knowledge_candidate("rejected", review_status="REJECTED"),
        _knowledge_candidate("missing_review", review_status=None),
    ]
    gated = HybridRetriever._apply_quality_gate(candidates, RetrievalPolicy.filters_for("general_research"))
    assert [item["chunk_id"] for item in gated] == ["ok", "missing_review"]


def test_quality_gate_honors_denied_review_status_from_filters():
    candidates = [
        _knowledge_candidate("ok"),
        _knowledge_candidate("manual", review_status="NEEDS_MANUAL_REVIEW"),
    ]
    filters = merge_policy_filters(
        RetrievalPolicy.filters_for("general_research"),
        {"denied_review_status": ["NEEDS_MANUAL_REVIEW"]},
    )
    gated = HybridRetriever._apply_quality_gate(candidates, filters)
    assert [item["chunk_id"] for item in gated] == ["ok"]


def test_quality_gate_truth_status_works_with_merged_filters():
    filters = merge_policy_filters(RetrievalPolicy.filters_for("factual_qa"), {})
    candidates = [
        _knowledge_candidate("verified", truth_status="EXTERNALLY_VERIFIED"),
        _knowledge_candidate("unverified", truth_status="NOT_EXTERNALLY_VERIFIED"),
        _knowledge_candidate("no_truth", truth_status=None),
    ]
    gated = HybridRetriever._apply_quality_gate(candidates, filters)
    assert [item["chunk_id"] for item in gated] == ["verified"]
