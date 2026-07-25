from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app, orchestrator


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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


def test_ready_health_skips_optional_qmt_by_default(monkeypatch):
    monkeypatch.delenv("READY_CHECK_OPTIONAL_QMT", raising=False)
    monkeypatch.setattr(api_module, "_check_postgres", lambda: "ok")
    monkeypatch.setattr(api_module, "_check_http", lambda *a, **k: "ok")
    monkeypatch.setattr(api_module, "_check_redis", lambda *a, **k: "skipped")
    monkeypatch.setattr(api_module, "_check_qmt", lambda: (_ for _ in ()).throw(AssertionError("qmt should not be checked")))
    checks = api_module._ready_checks()
    assert checks["qmt"] == "skipped"


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
