"""四仓业务 E2E（收尾文档 §41-§43 + P0 X-03~X-06）。

流程：Quant 不可变 Snapshot -> Content KnowledgeUnit/事实验证/Signal(v3) ->
      Factor Alpha Score/Factor Evidence -> Agent Portfolio/DecisionSnapshot ->
      Quant Backtest -> Replay lineage/version/snapshot 验证 ->
      单一决策主链路（actionable decision）-> Risk VETO -> EXACT_REPLAY。

§43：本 E2E 的 Compose 不启动旧 Agent Market Data Service / 旧 Agent Backtest
Runtime / 旧 Factor Paper Authority；如果仍有代码依赖旧路径，CI 直接失败。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clients.content_client import RemoteContentClient
from contracts.content import ContentSignalRequest

QUANT = os.getenv("QUANT_SERVICE_URL", "http://localhost:8011")
CONTENT = os.getenv("CONTENT_SERVICE_URL", "http://localhost:8100")
FACTOR = os.getenv("FACTOR_SERVICE_URL", "http://localhost:8200")
AGENT = os.getenv("AGENT_SERVICE_URL", "http://localhost:8000")

SYMBOLS = ["600519.SH", "000001.SZ"]
END = "2026-08-14"
# 与 factor alpha_score 的默认窗口保持一致（as_of - 730d），保证命中同一内容寻址快照。
START = (datetime.fromisoformat(END) - timedelta(days=730)).date().isoformat()


def _step(name: str) -> None:
    print(f"[four-repo-e2e] {name}", flush=True)


def _data(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _wait_healthy(client: httpx.Client, url: str) -> None:
    deadline = time.monotonic() + 300
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if client.get(url).status_code < 500:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(2)
    raise TimeoutError(f"service not healthy: {url} ({last_error})")


def _wait_content_task(content: RemoteContentClient, task_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        task = content.get_task(task_id) or {}
        if task.get("status") == "SUCCEEDED":
            return task
        if task.get("status") == "FAILED":
            raise AssertionError(f"content task failed: {task.get('error')}")
        time.sleep(2)
    raise TimeoutError(f"content task did not complete: {task_id}")


def _wait_backtest(client: httpx.Client, backtest_id: str) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        job = _data(client.get(f"{QUANT}/api/v1/backtests/{backtest_id}"))
        if job["status"] == "COMPLETED":
            return job
        if job["status"] == "FAILED":
            raise AssertionError(f"backtest failed: {job.get('error') or job}")
        time.sleep(2)
    raise TimeoutError(f"backtest did not complete: {backtest_id}")


def _run_inprocess_scenarios() -> None:
    """X-05 Risk VETO + X-06 EXACT_REPLAY（固定 fixture/stub specialist，隔离临时库）。"""
    tmp = Path(tempfile.mkdtemp(prefix="four-repo-e2e-agent-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'agent.db').as_posix()}"

    from storage.bootstrap import create_all
    from storage.db import SessionLocal, get_engine

    get_engine.cache_clear()
    SessionLocal.configure(bind=get_engine())
    create_all()

    from app.decision_runtime import DecisionRuntime
    from engines.decision.replay import DecisionReplayService
    from storage.repositories.research_repository import DecisionSnapshotRepository
    from storage.repositories.tool_result_repository import ToolResultRepository

    # ------------------------------------------------------------- X-05 Risk VETO
    _step("STEP 11: Risk VETO (fixed fixture: proposal=BUY, risk=VETO)")

    class _VetoFallback:
        def analyze_stock(self, symbol, as_of=None, patterns=None):
            return {
                "symbol": symbol,
                "orchestration": "local-fallback",
                "proposal": {"symbol": symbol, "action": "BUY", "proposed_weight": 1.0, "confidence": 0.9, "evidence_count": 5},
                "risk": {"veto": True, "reason": "FOUR_REPO_E2E_VETO"},
            }

    class _NoModel:
        def configured(self) -> bool:
            return False

        def run(self, **kwargs):
            raise AssertionError("VETO 场景不得调用 LLM")

    veto_runtime = DecisionRuntime(claude_agent=_NoModel(), fallback=_VetoFallback())
    veto_result = veto_runtime.analyze_stock("600519.SH")
    assert veto_result["final_decision"]["action"] == "VETO", "Risk VETO 必须覆盖 LLM BUY"
    assert veto_result["final_decision"]["approved"] is False
    veto_snapshot = DecisionSnapshotRepository().get_for_decision(veto_result["decision_id"])
    assert veto_snapshot.proposal["action"] == "BUY", "snapshot 必须保留 LLM 原始 proposal"
    assert veto_snapshot.policy["risk_veto"] is True, "policy 段必须记录 Risk VETO 结果"
    assert veto_snapshot.output["final_decision"] == "VETO", "output.final_decision == VETO"
    _step("  LLM Proposal(BUY) != Final Decision Authority(VETO) OK")

    # ----------------------------------------------------------- X-06 EXACT_REPLAY
    _step("STEP 12: EXACT_REPLAY (no live tool calls, reuse ToolResultSnapshot)")

    adapter_calls = {"count": 0}

    class _ReplayFallback:
        def analyze_stock(self, symbol, as_of=None, patterns=None):
            adapter_calls["count"] += 1
            return {
                "symbol": symbol,
                "orchestration": "local-fallback",
                "market_snapshot_id": "mds-e2e-exact-1",
                "content_signal_response": {
                    "contract_version": "content-factor-signal.v3",
                    "items": [{"content_snapshot_id": "cs-e2e-exact-1", "claim_id": "claim-e2e", "evidence_refs": ["ev-e2e"]}],
                },
                "proposal": {"symbol": symbol, "action": "BUY", "proposed_weight": 0.05, "confidence": 0.8, "evidence_count": 3},
            }

    replay_runtime = DecisionRuntime(claude_agent=_NoModel(), fallback=_ReplayFallback())
    first = replay_runtime.analyze_stock("600519.SH")
    assert adapter_calls["count"] == 1
    assert first["decision_snapshot_id"], "normal run must produce decision_snapshot_id"
    tool_repo = ToolResultRepository()
    objective = f"分析股票 600519.SH 当前是否存在技术机会，并给出风险和操作条件。"
    tool_snapshot = tool_repo.find_by_request("decision_runtime.analyze_stock", {"objective": objective})
    assert tool_snapshot is not None, "ToolResultSnapshot must be persisted on normal run"

    replayed = DecisionReplayService().replay(first["decision_id"], mode="EXACT_REPLAY")
    assert not replayed.get("error"), f"EXACT_REPLAY failed: {replayed.get('error')}"
    assert replayed["mode"] == "EXACT_REPLAY"
    stored = replayed["decision_snapshot"] or {}
    assert stored["market"]["snapshot_id"] == "mds-e2e-exact-1", "replay must reuse original market snapshot id"
    assert stored["content"]["snapshot_id"] == "cs-e2e-exact-1", "replay must reuse original content snapshot id"
    assert adapter_calls["count"] == 1, "EXACT_REPLAY must not re-invoke live execution adapters"
    replayed_tool = tool_repo.find_by_request("decision_runtime.analyze_stock", {"objective": objective})
    assert replayed_tool is not None and replayed_tool.tool_result_id == tool_snapshot.tool_result_id, (
        "EXACT_REPLAY must reuse the persisted ToolResultSnapshot"
    )
    _step("  EXACT_REPLAY reuses snapshots/tool results without live calls OK")


def main() -> None:
    with httpx.Client(timeout=300) as client:
        _step("STEP 0: health checks")
        _wait_healthy(client, f"{QUANT}/health")
        _wait_healthy(client, f"{CONTENT}/healthz")
        _wait_healthy(client, f"{FACTOR}/healthz")
        _wait_healthy(client, f"{AGENT}/health/ready")

        # ------------------------------------------------------------------
        _step("STEP 1: Quant fixture -> immutable Market Snapshot")
        batch = _data(client.post(f"{QUANT}/api/v1/market/bars/batch", json={"symbols": SYMBOLS, "start": START, "end": END}))
        assert batch.get("data_snapshot_id"), "bars_batch must expose data_snapshot_id"
        snapshot = _data(client.post(f"{QUANT}/api/v1/market/snapshots", json={"symbols": SYMBOLS, "start": START, "end": END}))
        snapshot_id = snapshot["snapshot_id"]
        assert snapshot_id, "snapshot_id required (§42)"
        assert snapshot.get("data_version"), "data_version required (§42/X-03)"
        # §42：snapshot is immutable —— 两次读取 manifest hash 必须一致。
        first = _data(client.get(f"{QUANT}/api/v1/market/snapshots/{snapshot_id}"))
        second = _data(client.get(f"{QUANT}/api/v1/market/snapshots/{snapshot_id}"))
        assert first["manifest_hash"] and first["manifest_hash"] == second["manifest_hash"]
        _step(f"  snapshot={snapshot_id} data_version={snapshot['data_version']}")

        # ------------------------------------------------------------------
        _step("STEP 2-4: Content ingest -> KnowledgeUnit -> Fact Verification -> Signal(v3)")
        content = RemoteContentClient(CONTENT)
        task = content.enqueue_bilibili(
            bv_id="BV1fourRepoFixture",
            metadata={"title": "四仓业务验收", "author": "integration"},
            transcript="贵州茅台600519业绩增长，毛利率改善。风险在于消费疲软。",
            as_of=datetime.now(UTC).replace(microsecond=0).isoformat(),
            offline_fixture=True,
            trace_id="four-repo-business-e2e",
        )
        completed = _wait_content_task(content, task["task_id"])
        video_id = completed["result"]["video_id"]
        units = content.list_video_knowledge_units(video_id, limit=20)["items"]
        assert units, "knowledge_units > 0 (§42)"
        assert all(unit.get("available_from") for unit in units), "available_from required (§42)"
        window_end = datetime.now(UTC).replace(microsecond=0)
        signals = content.content_factor_signals(
            ContentSignalRequest(
                symbols=["600519.SH"],
                start=(window_end - timedelta(minutes=1)).isoformat(),
                end=(window_end + timedelta(minutes=1)).isoformat(),
            )
        )
        # P0 X-03：main 主契约必须是 v3，且 content_snapshot_id / claim/evidence lineage 非空。
        assert signals["contract_version"] == "content-factor-signal.v3", "signal.contract_version must be v3 on main"
        items = signals.get("items") or []
        assert items, "content signals must not be empty"
        content_snapshot_ids = sorted({str(item.get("content_snapshot_id")) for item in items if item.get("content_snapshot_id")})
        assert content_snapshot_ids, "v3 signals must carry content_snapshot_id (no silent snapshot failure)"
        content_snapshot_id = content_snapshot_ids[0]
        assert any(item.get("claim_id") or item.get("evidence_refs") for item in items), (
            "claim/evidence lineage must not be empty for claim fixture"
        )
        _step(f"  content_snapshot_id={content_snapshot_id} signal_contract={signals['contract_version']}")

        # ------------------------------------------------------------------
        _step("STEP 5: Factor Alpha Score / Factor Evidence from Quant Discovery Snapshot")
        alpha = _data(client.post(f"{FACTOR}/api/v1/alpha/score", json={"symbols": SYMBOLS, "as_of": END}))
        factor_set_version = alpha.get("factor_set_version")
        assert factor_set_version, "factor_set identity required (X-03: not just HTTP 200)"
        assert alpha.get("market_snapshot_id") == snapshot_id, "factor market_snapshot_id == quant snapshot (§42)"
        # 用确定性公式在 quant 快照上产出 Factor Evidence（非 fallback 数据源）。
        evaluation = _data(
            client.post(
                f"{FACTOR}/api/v1/factors/evaluate",
                json={"rpn": ["close", "close", "ts_delay_5", "div"], "symbols": SYMBOLS, "start": START, "end": END, "horizon": 5},
            )
        )
        assert evaluation.get("metrics"), "factor evidence metrics required (§42)"
        assert evaluation.get("data_snapshot_id") == snapshot_id, "factor evidence must come from quant snapshot"
        research_experiment_id = alpha.get("research_experiment_id") or evaluation.get("research_experiment_id")

        # ------------------------------------------------------------------
        _step("STEP 6: Agent portfolio risk boundary")
        risk = client.post(
            f"{AGENT}/api/v1/risk/portfolio",
            json=[
                {"symbol": "600519.SH", "name": "贵州茅台", "market_value": 60_000.0},
                {"symbol": "000001.SZ", "name": "平安银行", "market_value": 40_000.0},
            ],
        )
        risk.raise_for_status()
        review = risk.json()
        assert review.get("total_market_value"), "portfolio risk review required (§41 STEP 6)"

        # ------------------------------------------------------------------
        _step("STEP 7-8: Agent TargetPortfolio -> Quant Backtest (snapshot mode)")
        backtest = _data(
            client.post(
                f"{QUANT}/api/v1/backtests",
                json={
                    "market_snapshot_id": snapshot_id,
                    "strategy": {"type": "equal_weight", "version": "four-repo-e2e", "rebalance_every_days": 20},
                    "start": START,
                    "end": END,
                    "initial_cash": 1_000_000,
                },
                headers={"idempotency-key": "four-repo-e2e-backtest"},
            )
        )
        completed_backtest = _wait_backtest(client, backtest["backtest_id"])
        assert completed_backtest["status"] == "COMPLETED", "backtest status == COMPLETED (§42)"
        assert completed_backtest.get("market_snapshot_id", snapshot_id) == snapshot_id, "backtest bound to same market snapshot"
        metrics = _data(client.get(f"{QUANT}/api/v1/backtests/{backtest['backtest_id']}/metrics"))
        assert "quality_flags" in metrics, "quality_flags must be explicitly returned (§42)"

        # ------------------------------------------------------------------
        _step("STEP 9: Agent DecisionSnapshot v2 with full lineage (§38/X-03)")
        decision = client.post(
            f"{AGENT}/api/v1/decisions",
            json={
                "query": "four-repo-business-e2e decision",
                "candidates": [
                    {"symbol": "600519.SH", "theme": "消费", "sector": "食品饮料", "theme_score": 70.0, "technical_score": 60.0, "risk_score": 20.0, "liquidity_score": 80.0, "confidence": 0.6},
                ],
                "market_regime": "rotation_market",
                "themes": ["消费"],
                "sector": "食品饮料",
                "market_features": {"data_snapshot_id": snapshot_id, "data_version": snapshot["data_version"], "source": "quant"},
                "decision_snapshot": {
                    "market": {"snapshot_id": snapshot_id, "data_version": snapshot["data_version"], "source": "quant"},
                    "content": {"signal_contract": signals["contract_version"], "snapshot_id": content_snapshot_id},
                    "factor": {
                        "factor_set_version": factor_set_version,
                        "alpha_score_contract": "factor.v1",
                        "research_experiment_id": research_experiment_id,
                    },
                    "strategy": {"strategy_id": "four-repo-e2e", "backtest_id": backtest["backtest_id"]},
                    "model": {"provider": "fixture", "model": "four-repo-e2e", "model_version": "v1", "prompt_version": "v1"},
                    "runtime": {"runtime_mode": "DETERMINISTIC_FALLBACK", "fallback_used": True, "fallback_reason": "MANUAL", "supervisor_version": "four-repo-e2e"},
                    "proposal": {"candidates": ["600519.SH"], "action": "BUY"},
                    "policy": {"policy_version": "policy.v1", "approved": True, "final_action": "BUY"},
                    "inputs": {
                        "market_snapshot_ids": [snapshot_id],
                        "content_snapshot_ids": [content_snapshot_id],
                        "research_experiment_ids": [research_experiment_id] if research_experiment_id else [],
                        "factor_set_ids": [factor_set_version],
                    },
                    "output": {"final_decision": "BUY"},
                },
                "decision_quality": "HIGH",
            },
        )
        decision.raise_for_status()
        decision_payload = decision.json()
        decision_id = decision_payload["decision_id"]
        assert decision_payload.get("decision_snapshot_id"), "decision_snapshot required (§42)"

        # X-03：完整 DecisionSnapshot v2 快照读取（schema/runtime/proposal/policy/output + lineage）。
        stored_snapshot = _data(client.get(f"{AGENT}/api/v1/decisions/{decision_id}/snapshot"))
        assert stored_snapshot["schema_version"] == "decision.snapshot.v2", "DecisionSnapshot schema_version v2 required"
        assert stored_snapshot["runtime"].get("runtime_mode"), "runtime.runtime_mode required"
        assert stored_snapshot["proposal"], "proposal segment required"
        assert stored_snapshot["policy"], "policy segment required (本次真实执行结果)"
        assert stored_snapshot["output"].get("final_decision"), "output/final_decision required"
        lineage_pairs = {(item.get("type"), item.get("id")) for item in stored_snapshot.get("lineage") or []}
        assert ("MARKET_SNAPSHOT", snapshot_id) in lineage_pairs, "MARKET_SNAPSHOT lineage required"
        assert ("CONTENT_SNAPSHOT", content_snapshot_id) in lineage_pairs, "CONTENT_SNAPSHOT lineage required"
        assert ("FACTOR_SET", factor_set_version) in lineage_pairs, "FACTOR_SET lineage required"
        assert ("BACKTEST", backtest["backtest_id"]) in lineage_pairs, "BACKTEST lineage required"
        assert stored_snapshot["inputs"]["content_snapshot_ids"] == [content_snapshot_id]

        # ------------------------------------------------------------------
        _step("STEP 9b: Single decision path - actionable analyze_stock (X-04)")
        actionable = client.post(f"{AGENT}/api/v1/analyze/stock", json={"symbol": "600519.SH"})
        actionable.raise_for_status()
        actionable_payload = actionable.json()
        assert actionable_payload.get("decision_id"), "actionable decision must carry decision_id"
        assert actionable_payload.get("decision_snapshot_id"), "actionable decision must carry decision_snapshot_id"
        assert actionable_payload.get("runtime_mode"), "runtime_mode must be explicit"
        assert actionable_payload.get("policy") is not None, "policy governance result required"
        assert actionable_payload.get("final_decision"), "final_decision required"
        actionable_snapshot = _data(client.get(f"{AGENT}/api/v1/decisions/{actionable_payload['decision_id']}/snapshot"))
        assert actionable_snapshot["schema_version"] == "decision.snapshot.v2"
        assert actionable_snapshot["runtime"]["runtime_mode"] == actionable_payload["runtime_mode"]
        _step(f"  actionable decision={actionable_payload['decision_id']} mode={actionable_payload['runtime_mode']}")

        # ------------------------------------------------------------------
        _step("STEP 10: Replay -> verify lineage/version/snapshot (§39)")
        replay = client.post(f"{AGENT}/api/v1/decisions/{decision_id}/replay", json={"mode": "original"})
        replay.raise_for_status()
        replay_payload = replay.json()
        stored = replay_payload.get("decision_snapshot") or {}
        assert stored, "replay must return decision_snapshot (§42)"
        assert stored["market"]["snapshot_id"] == snapshot_id, "agent snapshot anchored to quant snapshot (§42)"
        assert stored["content"].get("snapshot_id") == content_snapshot_id, "content snapshot preserved in replay"
        assert stored.get("runtime", {}).get("runtime_mode"), "runtime segment preserved in replay"
        assert stored.get("proposal") and stored.get("policy"), "proposal/policy segments preserved in replay"
        lineage = stored.get("lineage") or []
        assert {"type": "MARKET_SNAPSHOT", "id": snapshot_id} in [
            {"type": item.get("type"), "id": item.get("id")} for item in lineage
        ], "market snapshot lineage preserved (§42)"
        assert any(item.get("type") == "BACKTEST" and item.get("id") == backtest["backtest_id"] for item in lineage)

        # ------------------------------------------------------------------
        _run_inprocess_scenarios()

        _step("ALL FOUR-REPO BUSINESS ASSERTIONS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"[four-repo-e2e] FAILED: {exc}", file=sys.stderr)
        raise
