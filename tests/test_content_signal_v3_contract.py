"""P0 A-07：content-factor-signal.v3 契约测试（main 主契约）。"""
from __future__ import annotations

import json
from pathlib import Path

from app.decision_runtime import LEGACY_SIGNAL_CONTRACT_VERSION, DecisionRuntime
from contracts.content import (
    CONTENT_FACTOR_SIGNAL_LEGACY_VERSION,
    CONTENT_FACTOR_SIGNAL_VERSION,
    ContentSignalRequest,
    ContentSignalResponse,
    ContentSignalLegacyResponse,
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
            "content_signal_response": _v3_response_payload(),
        }


def test_v3_signal_enters_decision_snapshot_lineage(isolated_database):
    runtime = DecisionRuntime(claude_agent=_StubClaudeAgent(), fallback=_V3Fallback())

    result = runtime.analyze_stock("CN.A.600519")

    snapshot = DecisionSnapshotRepository().get_for_decision(result["decision_id"])
    assert snapshot.content["signal_contract"] == "content-factor-signal.v3"
    assert snapshot.content["snapshot_id"] == "cs-fixture"
    assert snapshot.inputs["content_snapshot_ids"] == ["cs-fixture"], "v3 content snapshot 必须真实进入 content_snapshot_ids"
    assert snapshot.inputs["market_snapshot_ids"] == ["mds-agent-1"]
    lineage = {(item["type"], item["id"]) for item in snapshot.lineage}
    assert ("CONTENT_SNAPSHOT", "cs-fixture") in lineage
    assert ("MARKET_SNAPSHOT", "mds-agent-1") in lineage
