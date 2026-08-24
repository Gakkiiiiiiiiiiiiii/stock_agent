from datetime import UTC, datetime

from agent.evidence_synthesis import synthesize_evidence
from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem


def test_synthesis_is_deterministic_and_tracks_coverage():
    now = datetime.now(UTC)
    item = Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="v1", payload={}, quality_status=EvidenceQuality.VERIFIED)
    result = synthesize_evidence([item])
    assert result.coverage.market == 1
    assert result.synthesis_hash


def test_synthesis_excludes_memory_and_unverified_opposing_from_external_coverage():
    now = datetime.now(UTC)
    memory = Evidence(evidence_type=EvidenceType.DECISION_MEMORY, source_system=SourceSystem.STOCK_AGENT, subject_type="memory", subject_key="m", as_of=now, available_at=now, contract_version="agent.v1", payload={"stance": "bearish", "conflict_dimension": "regime"}, quality_status=EvidenceQuality.VERIFIED)
    uncertain = Evidence(evidence_type=EvidenceType.MARKET_REGIME, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="v1", payload={"stance": "opposing", "conflict_dimension": "regime"}, quality_status=EvidenceQuality.UNVERIFIED)
    result = synthesize_evidence([memory, uncertain])
    assert result.coverage.market == 0
    assert not result.opposing
    assert not result.key_conflicts
    assert result.uncertain
