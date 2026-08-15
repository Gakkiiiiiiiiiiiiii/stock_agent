"""Business-level proof for the deployed Content -> Factor -> Paper -> Agent path."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import httpx

from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient
from contracts.content import ContentSignalRequest

CONTENT = os.getenv("CONTENT_SERVICE_URL", "http://stock-content-api:8100")
FACTOR = os.getenv("FACTOR_SERVICE_URL", "http://stock-factor-api:8200")
AGENT = os.getenv("AGENT_SERVICE_URL", "http://stock-agent-api:8000")


def _body(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    return (
        payload["data"]
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict)
        else payload
    )


def _wait_for_content(client: RemoteContentClient, task_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        task = client.get_task(task_id) or {}
        if task.get("status") == "SUCCEEDED":
            return task
        if task.get("status") == "FAILED":
            raise AssertionError(f"content task failed: {task.get('error')}")
        time.sleep(2)
    raise TimeoutError(f"content task did not complete: {task_id}")


def main() -> None:
    with httpx.Client(timeout=30) as client:
        for url in (f"{AGENT}/health/ready", f"{CONTENT}/healthz", f"{FACTOR}/healthz"):
            response = client.get(url)
            response.raise_for_status()

    trace_id = "three-repo-business-e2e"
    content = RemoteContentClient(CONTENT)
    factor = RemoteFactorClient(FACTOR)
    task = content.enqueue_bilibili(
        bv_id="BV1threeRepoFixture",
        metadata={"title": "三仓业务验收", "author": "integration"},
        transcript="宁德时代300750业绩增长，毛利率改善。风险在于价格战。",
        as_of=datetime.now(UTC).replace(microsecond=0).isoformat(),
        offline_fixture=True,
        trace_id=trace_id,
    )
    completed = _wait_for_content(content, task["task_id"])
    video_id = completed["result"]["video_id"]
    units = content.list_video_knowledge_units(video_id, limit=20)["items"]
    assert units and all(unit.get("available_from") for unit in units)

    end = datetime.now(UTC).replace(microsecond=0)
    signals = content.content_factor_signals(
        ContentSignalRequest(
            symbols=["300750"],
            start=(end - timedelta(minutes=1)).isoformat(),
            end=(end + timedelta(minutes=1)).isoformat(),
        )
    )
    assert signals["contract_version"] == "content-factor-signal.v2"
    assert signals["items"] and all(
        item.get("evidence_ids") is not None for item in signals["items"]
    )

    # Freeze on T-1 and execute on T.  This talks only to Factor's public
    # contract and verifies V2 paper state rather than an in-process service.
    signal_day = "2026-08-12"
    frozen = _body(
        httpx.post(
            f"{FACTOR}/api/v1/paper/orders/generate",
            json={
                "scores": [{"symbol": "300750", "score": 1.0}],
                "as_of": signal_day,
                "data_snapshot_id": "three-repo-snapshot",
                "top_k": 1,
            },
            timeout=30,
        )
    )
    assert frozen["orders"][0]["status"] == "FROZEN"
    paper = _body(
        httpx.post(
            f"{FACTOR}/api/v1/paper/run",
            json={
                "as_of": "2026-08-13",
                "data_snapshot_id": "three-repo-snapshot",
                "market_prices": {
                    "300750": {
                        "open": 100.0,
                        "close": 100.0,
                        "volume": 1_000_000,
                        "tradable": True,
                    }
                },
            },
            timeout=30,
        )
    )
    assert paper["filled_order_count"] == 1
    state = _body(httpx.get(f"{FACTOR}/api/v1/paper/state", timeout=30))
    assert state["positions"]["300750"]["quantity"] > 0
    # Agent boundary must still use the remote, versioned Factor contract.
    assert factor.list_factors(limit=1)["limit"] == 1


if __name__ == "__main__":
    main()
