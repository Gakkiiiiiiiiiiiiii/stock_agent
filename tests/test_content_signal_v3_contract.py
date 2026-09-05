"""P0 A-07：content-factor-signal.v3 契约测试（main 主契约）。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import dependencies
from app.api import app
from app.decision_runtime import LEGACY_SIGNAL_CONTRACT_VERSION, DecisionRuntime
from contracts.content import (
    CONTENT_FACTOR_SIGNAL_LEGACY_VERSION,
    CONTENT_FACTOR_SIGNAL_VERSION,
    ContentSignalLegacyResponse,
    ContentSignalRequest,
    ContentSignalResponse,
)
from contracts.evidence import (
    DependencyStatus,
    DependencyStatusValue,
    Evidence,
    EvidenceQuality,
    EvidenceType,
    SourceSystem,
)
from storage.repositories.research_repository import DecisionSnapshotRepository

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contracts" / "content-factor-signal.v3"


def _v3_response_payload() -> dict:
    return json.loads((CONTRACT_DIR / "response.json").read_text(encoding="utf-8"))


def test_v3_response_parses():
    payload = _v3_response_payload()
    response = ContentSignalResponse.model_validate(payload)
    assert response.contract_version == CONTENT_FACTOR_SIGNAL_VERSION == "content-factor-signal.v3"
    assert response.items


def test_v3_lineage_fields_are_not_lost():
    payload = _v3_response_payload()
    item = ContentSignalResponse.model_validate(payload).items[0]
    assert item.content_snapshot_id == "cs-fixture", "content_snapshot_id 不得丢失"
    assert item.claim_id == "claim-fixture", "claim refs 不得丢失"
    assert item.evidence_refs == ["evidence-fixture"], "evidence refs 不得丢失"
    assert item.producer_version and item.signal_schema_version == CONTENT_FACTOR_SIGNAL_VERSION
    assert item.producer.get("model_id") and item.producer.get("prompt_version"), "model/prompt lineage 不得丢失"


def test_main_defaults_to_v3_and_v2_requires_explicit_legacy():
    assert ContentSignalResponse().contract_version == "content-factor-signal.v3"
    assert ContentSignalRequest(start="2026-01-01", end="2026-01-31").contract_version == "content-factor-signal.v3"
    # v2 只保留为显式 legacy compatibility（旧 Release lane）。
    assert ContentSignalLegacyResponse().contract_version == "content-factor-signal.v2"
    assert CONTENT_FACTOR_SIGNAL_LEGACY_VERSION == LEGACY_SIGNAL_CONTRACT_VERSION == "content-factor-signal.v2"


class _StubClaudeAgent:
    def configured(self) -> bool:
        return False

    def run(self, **kwargs):
        raise AssertionError("fallback 模式不得调用 LLM")


class _V3Fallback:
    def analyze_stock(self, symbol, as_of=None, patterns=None):
        return {
            "symbol": symbol,
            "orchestration": "local-fallback",
            "market_snapshot_id": "mds-agent-1",
            "market_data_version": "sha256:agent",
        }


class _FormalEvidenceGateway:
    """Offline test adapter for the formal bundle path; no producer calls."""

    def collect(self, requests, *, decision_time, trace=None):
        del requests, trace
        rows = [
            (EvidenceType.MARKET_SNAPSHOT, "market", {"snapshot_id": "mds-agent-1", "close": 123.0}),
            (EvidenceType.MARKET_REGIME, "market", {"regime": "neutral"}),
            (EvidenceType.SECTOR_STRENGTH, "market", {"items": [{"sector": "consumer", "strength": 0.2}]}),
            (EvidenceType.PORTFOLIO_POSITION, "portfolio", {"positions": [], "gross_exposure": 0.0, "net_exposure": 0.0}),
            (EvidenceType.PORTFOLIO_RISK, "portfolio", {"risk_score": 0.1, "veto": False}),
            (EvidenceType.FACTOR_SCORE, "factor-fixture", {"factor_artifact_id": "factor-fixture", "score": 0.2}),
            (EvidenceType.KNOWLEDGE_CLAIM, "CN.A.600519", {"content_snapshot_id": "cs-fixture", "claim_id": "claim-fixture", "evidence_refs": ["evidence-fixture"], "claim": "frozen content claim"}),
        ]
        evidence = [
            Evidence(
                evidence_type=evidence_type,
                source_system=(SourceSystem.CONTENT if evidence_type == EvidenceType.KNOWLEDGE_CLAIM else SourceSystem.FACTOR if evidence_type == EvidenceType.FACTOR_SCORE else SourceSystem.QUANT),
                source_ref=f"quant:test:{evidence_type.value.lower()}",
                subject_type="portfolio" if subject == "portfolio" else "market",
                subject_key=subject,
                as_of=decision_time,
                available_at=decision_time,
            snapshot_id=("cs-fixture" if evidence_type == EvidenceType.KNOWLEDGE_CLAIM else "factor-fixture" if evidence_type == EvidenceType.FACTOR_SCORE else ("mds-agent-1" if subject == "market" else "portfolio-1")),
            contract_version=("content-factor-signal.v3" if evidence_type == EvidenceType.KNOWLEDGE_CLAIM else "factor.v1" if evidence_type == EvidenceType.FACTOR_SCORE else "market-data.v1"),
                payload=payload,
                quality_status=EvidenceQuality.VERIFIED,
                confidence=1.0,
            )
            for evidence_type, subject, payload in rows
        ]
        status = DependencyStatus(
            system=SourceSystem.QUANT,
            status=DependencyStatusValue.OK,
            checked_at=decision_time,
            contract_version="market-data.v1",
            service_version="quant-test",
            snapshot_id="mds-agent-1",
        )
        content_status = DependencyStatus(
            system=SourceSystem.CONTENT,
            status=DependencyStatusValue.OK,
            checked_at=decision_time,
            contract_version="content-factor-signal.v3",
            service_version="content-test",
            snapshot_id="cs-fixture",
        )
        factor_status = DependencyStatus(
            system=SourceSystem.FACTOR,
            status=DependencyStatusValue.OK,
            checked_at=decision_time,
            contract_version="factor.v1",
            service_version="factor-test",
            snapshot_id="factor-fixture",
        )
        return evidence, [status, content_status, factor_status]


def _formal_fallback(**kwargs):
    del kwargs
    return {
        "proposal": {"symbol": "CN.A.600519", "action": "HOLD", "confidence": 0.7},
    }


def test_v3_signal_enters_decision_snapshot_lineage(isolated_database, monkeypatch):
    monkeypatch.setenv("STOCK_AGENT_OFFLINE_MODE", "1")
    monkeypatch.setenv("STOCK_AGENT_DETERMINISTIC_FIXTURE", "1")
    monkeypatch.setenv("AGENT_GIT_COMMIT", "fixture-commit")
    now = datetime.now(UTC).replace(microsecond=0)
    runtime = DecisionRuntime(
        claude_agent=_StubClaudeAgent(),
        fallback=_V3Fallback(),
        evidence_gateway=_FormalEvidenceGateway(),
        trusted_fallback=_formal_fallback,
        clock=lambda: now,
    )
    # Legacy v1 remains narrative-only and cannot create a formal decision.
    legacy = runtime.analyze_stock("CN.A.600519")
    assert legacy["actionable"] is False
    assert "decision_id" not in legacy

    class _Facade:
        def __init__(self, value):
            self.runtime = value

    # Exercise the public formal route rather than calling the runtime's
    # compatibility facade directly.
    original_orchestrator = dependencies.orchestrator
    dependencies.orchestrator = _Facade(runtime)
    try:
        response = TestClient(app).post(
            "/api/v2/decisions",
            json={
                "task_type": "daily-market-decision",
                "objective": "验证内容信号进入正式决策快照",
                "subjects": ["CN.A.600519"],
                "context": {"skill": "daily-market-decision"},
                "as_of": now.isoformat(),
                "portfolio_id": "content-fixture",
                "idempotency_key": "content-fixture-key",
            },
        )
    finally:
        dependencies.orchestrator = original_orchestrator
    assert response.status_code == 200, response.text
    result = response.json()

    snapshot = DecisionSnapshotRepository().get_v3_for_decision(result["decision_id"])
    assert snapshot is not None
    assert snapshot.schema_version == "decision.snapshot.v3"
    lineage = {(item["type"], item["id"]) for item in snapshot.lineage}
    assert ("CONTENT_SNAPSHOT", "cs-fixture") in lineage
    assert ("MARKET_SNAPSHOT", "mds-agent-1") in lineage
