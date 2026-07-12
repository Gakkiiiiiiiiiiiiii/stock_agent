from fastapi.testclient import TestClient

from app.api import app


client = TestClient(app)


def test_market_regime_api():
    response = client.post("/api/v1/market/regime", json={})
    assert response.status_code == 200
    body = response.json()
    assert "regime" in body
    assert "primary_regime" in body["regime"]

