"""四仓业务 E2E（收尾文档 §41-§43）。

流程：Quant 不可变 Snapshot -> Content KnowledgeUnit/事实验证/Signal ->
      Factor Alpha Score/Factor Evidence -> Agent Portfolio/DecisionSnapshot ->
      Quant Backtest -> Replay lineage/version/snapshot 验证。

§43：本 E2E 的 Compose 不启动旧 Agent Market Data Service / 旧 Agent Backtest
Runtime / 旧 Factor Paper Authority；如果仍有代码依赖旧路径，CI 直接失败。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

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


def main() -> None:
    with httpx.Client(timeout=60) as client:
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
        assert snapshot.get("data_version"), "data_version required (§42)"
        # §42：snapshot is immutable —— 两次读取 manifest hash 必须一致。
        first = _data(client.get(f"{QUANT}/api/v1/market/snapshots/{snapshot_id}"))
        second = _data(client.get(f"{QUANT}/api/v1/market/snapshots/{snapshot_id}"))
        assert first["manifest_hash"] and first["manifest_hash"] == second["manifest_hash"]
        _step(f"  snapshot={snapshot_id} data_version={snapshot['data_version']}")

        # ------------------------------------------------------------------
        _step("STEP 2-4: Content ingest -> KnowledgeUnit -> Fact Verification -> Signal")
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
        assert signals["contract_version"] == "content-factor-signal.v2", "signal.contract_version (§42)"

        # ------------------------------------------------------------------
        _step("STEP 5: Factor Alpha Score / Factor Evidence from Quant Discovery Snapshot")
        alpha = _data(client.post(f"{FACTOR}/api/v1/alpha/score", json={"symbols": SYMBOLS, "as_of": END}))
        assert alpha.get("factor_set_version"), "factor_set_version required (§42)"
        assert alpha.get("market_snapshot_id") == snapshot_id, "factor market_snapshot_id == quant snapshot (§42)"
        # 用确定性公式在 quant 快照上产出 Factor Evidence（非 fallback 数据源）。
        evaluation = _data(
            client.post(
                f"{FACTOR}/api/v1/factors/evaluate",
                json={"rpn": ["close", "ts_delay_5", "div"], "symbols": SYMBOLS, "start": START, "end": END, "horizon": 5},
            )
        )
        assert evaluation.get("metrics"), "factor evidence metrics required (§42)"
        assert evaluation.get("data_snapshot_id") == snapshot_id, "factor evidence must come from quant snapshot"

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
        metrics = _data(client.get(f"{QUANT}/api/v1/backtests/{backtest['backtest_id']}/metrics"))
        assert "quality_flags" in metrics, "quality_flags must be explicitly returned (§42)"

        # ------------------------------------------------------------------
        _step("STEP 9: Agent DecisionSnapshot (§38)")
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
                    "content": {"signal_contract": signals["contract_version"]},
                    "factor": {"factor_set_version": alpha["factor_set_version"], "alpha_score_contract": "factor.v1"},
                    "strategy": {"strategy_id": "four-repo-e2e", "backtest_id": backtest["backtest_id"]},
                    "model": {"provider": "fixture", "model": "four-repo-e2e", "model_version": "v1", "prompt_version": "v1"},
                },
                "decision_quality": "HIGH",
            },
        )
        decision.raise_for_status()
        decision_payload = decision.json()
        decision_id = decision_payload["decision_id"]
        assert decision_payload.get("decision_snapshot_id"), "decision_snapshot required (§42)"

        # ------------------------------------------------------------------
        _step("STEP 10: Replay -> verify lineage/version/snapshot (§39)")
        replay = client.post(f"{AGENT}/api/v1/decisions/{decision_id}/replay", json={"mode": "original"})
        replay.raise_for_status()
        replay_payload = replay.json()
        stored = replay_payload.get("decision_snapshot") or {}
        assert stored, "replay must return decision_snapshot (§42)"
        assert stored["market"]["snapshot_id"] == snapshot_id, "agent snapshot anchored to quant snapshot (§42)"
        lineage = stored.get("lineage") or []
        assert {"type": "MARKET_SNAPSHOT", "id": snapshot_id} in [
            {"type": item.get("type"), "id": item.get("id")} for item in lineage
        ], "market snapshot lineage preserved (§42)"
        assert any(item.get("type") == "BACKTEST" and item.get("id") == backtest["backtest_id"] for item in lineage)

        _step("ALL FOUR-REPO BUSINESS ASSERTIONS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"[four-repo-e2e] FAILED: {exc}", file=sys.stderr)
        raise
