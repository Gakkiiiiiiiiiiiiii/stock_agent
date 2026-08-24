import pytest

from clients.content_client import RemoteContentClient
from clients.factor_client import RemoteFactorClient
from app.tool_registry import ClaudeToolRegistry


def test_content_client_is_read_only_and_maps_content_v1_reads(monkeypatch):
    calls = []

    def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/v1/videos":
            return {"items": [{"video_id": "v1"}]}
        if path.endswith("/summary"):
            return {"data": {"video_id": "v1", "core_summary": "summary"}}
        raise AssertionError(f"unexpected content read path: {path}")

    monkeypatch.setattr(RemoteContentClient, "request", fake_request)
    client = RemoteContentClient("http://content.example")
    with pytest.raises(AttributeError):
        client.enqueue_bilibili(bv_id="BV1", summary_mode="investment")
    assert client.list_videos()[0]["video_id"] == "v1"
    assert client.get_video_summary("v1")["core_summary"] == "summary"
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert [call[1] for call in calls] == ["/api/v1/videos", "/api/v1/videos/v1/summary"]


def test_factor_client_retires_mining_and_keeps_factor_v1_reads(monkeypatch):
    calls = []

    def fake_request(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/v1/factors":
            return {"contract_version": "factor.v1", "items": [{"factor_id": "f1"}]}
        return {"contract_version": "factor.v1", "data": {"factor_id": "f1", "evidence": ["e1"]}}

    monkeypatch.setattr(RemoteFactorClient, "request", fake_request)
    client = RemoteFactorClient("http://factor.example")
    with pytest.raises(AttributeError):
        client.create_mining_job(symbols=["600000.SH"])
    assert client.list_factors() == {"items": [{"factor_id": "f1"}], "limit": 20}
    assert client.get_factor_evidence("f1")["evidence"] == ["e1"]
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert [call[1] for call in calls] == ["/api/v1/factors", "/api/v1/factors/f1/evidence"]


def test_registry_exposes_read_compute_evidence_tools_with_matching_schemas(monkeypatch):
    observed = {}

    def fake_score(self, request, **kwargs):
        observed["factor"] = request.model_dump()
        return {"scores": []}

    def fake_subject(self, query, **kwargs):
        observed["content"] = {"query": query, **kwargs}
        return {"items": []}

    monkeypatch.setattr(RemoteFactorClient, "score_alpha", fake_score)
    monkeypatch.setattr(RemoteContentClient, "search_video_knowledge", fake_subject)
    registry = ClaudeToolRegistry()
    names = {item["name"] for item in registry.anthropic_tools()}
    assert {"get_factor_scores", "get_subject_state", "get_decision_history"} <= names
    assert not names.intersection({"enqueue_bilibili", "create_mining_job", "save_investment_decision", "record_decision_outcome"})
    factor_schema = next(item["input_schema"] for item in registry.anthropic_tools() if item["name"] == "get_factor_scores")
    subject_schema = next(item["input_schema"] for item in registry.anthropic_tools() if item["name"] == "get_subject_state")
    assert "symbols" in factor_schema["required"]
    assert "subject_key" in subject_schema["required"]
    assert registry.execute("get_factor_scores", {"symbols": ["600000.SH"]}) == {"scores": []}
    assert registry.execute("get_subject_state", {"subject_key": "券商", "as_of": "2026-08-24T00:00:00Z"}) == {"items": []}
    assert observed["factor"]["symbols"] == ["600000.SH"]
    assert observed["content"]["filters"] == {"subject_key": "券商", "as_of": "2026-08-24T00:00:00Z"}
