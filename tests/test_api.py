from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app, orchestrator

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_version_advertises_current_decision_contracts():
    response = client.get("/health/version")
    assert response.status_code == 200
    contracts = set(response.json()["contract_versions"])
    assert {
        "evidence.v1",
        "decision-input.v1",
        "specialist-artifact.v2",
        "evidence-synthesis.v1",
        "decision-quality.v2",
        "replay.v2",
        "decision.snapshot.v3",
        "investment-proposal.v2",
        "investment-decision.v2",
        "decision-memory.v1",
    } <= contracts


def test_ready_health_reports_dependency_failure(monkeypatch):
    monkeypatch.setattr(api_module, "_ready_checks", lambda: {"api": "ok", "qdrant": "failed"})
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["qdrant"] == "failed"


def test_ready_health_allows_optional_skipped(monkeypatch):
    monkeypatch.setenv("READY_REQUIRED_CHECKS", "api")
    monkeypatch.setattr(api_module, "_ready_checks", lambda: {"api": "ok", "redis": "skipped"})
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "skipped"


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
