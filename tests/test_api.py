from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app, orchestrator


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_health_reports_dependency_failure(monkeypatch):
    monkeypatch.setattr(api_module, "_ready_checks", lambda: {"api": "ok", "qdrant": "fail: unavailable"})
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_stock_analyze_api(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "analyze_stock",
        lambda symbol, as_of=None, patterns=None: {
            "symbol": symbol,
            "technical": {"close": 123.45, "signals": []},
            "summary": "mocked",
            "risk": {"warnings": []},
        },
    )
    response = client.post("/api/v1/analyze/stock", json={"symbol": "SAMPLE"})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SAMPLE"
    assert "technical" in body
