from fastapi.testclient import TestClient

from app.api import app
from app import dependencies


client = TestClient(app)


class _QuantFixture:
    def get_market_regime(self, *, as_of=None):
        return {"regime": {"primary_regime": "quant_fixture_regime", "as_of": as_of}, "source": "quant"}


def test_market_regime_api(monkeypatch):
    monkeypatch.setattr(dependencies, "quant_client", _QuantFixture())
    response = client.post("/api/v1/market/regime", json={})
    assert response.status_code == 200
    body = response.json()
    assert "regime" in body
    assert "primary_regime" in body["regime"]
    assert body["regime"]["primary_regime"] == "quant_fixture_regime"
    assert body["source"] == "quant"


def test_market_regime_api_returns_503_when_quant_is_unavailable(monkeypatch):
    class _UnavailableQuant:
        def get_market_regime(self, *, as_of=None):
            raise RuntimeError("quant unavailable")

    monkeypatch.setattr(dependencies, "quant_client", _UnavailableQuant())
    response = client.post("/api/v1/market/regime", json={})
    assert response.status_code == 503
    assert response.json()["reason_code"] == "DEPENDENCY_UNAVAILABLE"
