from fastapi.testclient import TestClient

from app.api import app


client = TestClient(app)


def test_run_agent_fallback_without_key(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("ANALYSIS_MODEL_API_KEY", raising=False)
    response = client.post("/api/v1/agent/run", json={"query": "分析黄金主题"})
    assert response.status_code == 200
    body = response.json()
    assert body["orchestration"] == "local-fallback"
    assert "Claude-style Agent" in body["warning"]


def test_run_agent_stream_fallback_without_key(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("ANALYSIS_MODEL_API_KEY", raising=False)
    with client.stream("POST", "/api/v1/agent/run/stream", json={"query": "分析黄金主题"}) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: session" in body
    assert "event: warning" in body
    assert "event: done" in body


def test_agent_session_crud():
    created = client.post("/api/v1/agent/sessions", json={"title": "测试会话"})
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    fetched = client.get(f"/api/v1/agent/sessions/{session_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "测试会话"

    listed = client.get("/api/v1/agent/sessions")
    assert listed.status_code == 200
    assert any(item["session_id"] == session_id for item in listed.json()["items"])
