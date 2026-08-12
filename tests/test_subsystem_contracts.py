from contracts.content import CONTENT_FACTOR_SIGNAL_VERSION, ContentSignalRequest, ContentSignalResponse
from contracts.factor import AlphaScoreRequest, MiningJobRequest


def test_content_factor_signal_contract_is_versioned():
    payload = ContentSignalResponse().model_dump()
    assert payload["contract_version"] == CONTENT_FACTOR_SIGNAL_VERSION
    assert ContentSignalRequest(start="2026-01-01", end="2026-01-31").symbols == []


def test_factor_contract_accepts_empty_optional_symbol_sets():
    assert MiningJobRequest().symbols == []
    assert AlphaScoreRequest().symbols == []
