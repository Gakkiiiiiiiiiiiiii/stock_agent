"""ToolResult Snapshot 测试（详细修改方案 §13）。"""
from __future__ import annotations

from storage.repositories.tool_result_repository import ToolResultRepository, hash_payload


def test_hash_payload_is_deterministic_and_order_insensitive():
    assert hash_payload({"a": 1, "b": 2}) == hash_payload({"b": 2, "a": 1})
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


def test_record_and_reuse_by_request(isolated_database):
    repo = ToolResultRepository()
    request = {"symbol": "600519.SH", "fields": ["close", "volume"]}
    response = {"close": 1700.5, "volume": 12345}

    snapshot = repo.record(
        tool_id="market_quote",
        request=request,
        response=response,
        tool_version="market_quote.v1",
        decision_id="dec-1",
        agent_run_id="run-1",
        snapshot_refs=["snap-market-1"],
        latency_ms=12.5,
    )
    assert snapshot.tool_result_id
    assert snapshot.request_hash == hash_payload(request)
    assert snapshot.response_hash == hash_payload(response)
    assert snapshot.snapshot_refs == ["snap-market-1"]
    assert snapshot.status == "OK"

    # EXACT_REPLAY：相同 tool_id + request 命中历史结果，不重新联网
    reused = repo.find_by_request("market_quote", request)
    assert reused is not None
    assert reused.tool_result_id == snapshot.tool_result_id
    assert reused.response_payload == response

    # 请求不同则不复用
    assert repo.find_by_request("market_quote", {"symbol": "000001.SZ"}) is None
    assert repo.find_by_request("other_tool", request) is None


def test_list_for_decision_orders_by_created_at(isolated_database):
    repo = ToolResultRepository()
    repo.record(tool_id="t1", request={"q": 1}, response={"v": 1}, decision_id="dec-9")
    repo.record(tool_id="t2", request={"q": 2}, response={"v": 2}, decision_id="dec-9")
    repo.record(tool_id="t3", request={"q": 3}, response={"v": 3}, decision_id="other")

    rows = repo.list_for_decision("dec-9")
    assert [row.tool_id for row in rows] == ["t1", "t2"]
