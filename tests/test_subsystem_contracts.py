from contracts.content import (
    CONTENT_FACTOR_SIGNAL_VERSION,
    ContentSignal,
    ContentSignalRequest,
    ContentSignalResponse,
)
from contracts.factor import AlphaScoreRequest, MiningJobRequest


def test_content_factor_signal_contract_is_versioned():
    payload = ContentSignalResponse().model_dump()
    assert payload["contract_version"] == CONTENT_FACTOR_SIGNAL_VERSION
    assert ContentSignalRequest(start="2026-01-01", end="2026-01-31").symbols == []


def test_factor_contract_accepts_empty_optional_symbol_sets():
    assert MiningJobRequest().symbols == []
    assert AlphaScoreRequest().symbols == []


def test_content_factor_signal_contract_carries_canonical_semantics():
    item = ContentSignal(
        knowledge_uid="knowledge-1",
        subject_key="CN.A.600519",
        as_of_time="2026-08-15T09:00:00+08:00",
        available_from="2026-08-15T09:01:00+08:00",
        knowledge_kind="FACT",
        truth_status="NOT_CHECKED",
        support_status="CROSS_MODAL_SUPPORTED",
        review_status="UNREVIEWED",
        evidence_ids=["evidence-1"],
        content_attention_score=0.5,
        cross_video_consensus=0.2,
    )
    assert item.content_attention_score == 0.5
    assert item.evidence_ids == ["evidence-1"]
