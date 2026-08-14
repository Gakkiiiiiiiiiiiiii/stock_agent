from datetime import date

from fastapi.testclient import TestClient

from financial_agent.models import KlineRecord, KlineResponse
from services import market_data_api


class _FixtureProvider:
    def get_kline(self, symbol, start_date, end_date, freq, adjust):
        records = {
            "600000": [
                KlineRecord(date=date(2026, 8, 10), open=10, high=11, low=9, close=10.5, volume=100, amount=1050),
                KlineRecord(date=date(2026, 8, 11), open=10.5, high=12, low=10, close=11, volume=120, amount=1320),
            ],
            "000001": [
                KlineRecord(date=date(2026, 8, 11), open=8, high=8.5, low=7.9, close=8.2, volume=80, amount=656),
            ],
        }[symbol]
        return KlineResponse(symbol=symbol, records=records, source="fixture")


def test_bars_batch_is_versioned_and_keeps_missing_values(monkeypatch):
    monkeypatch.setattr(market_data_api, "get_market_data_provider", lambda: _FixtureProvider())
    response = TestClient(market_data_api.app).post(
        "/v1/bars/batch",
        json={"symbols": ["600000", "000001"], "start": "2026-08-10", "end": "2026-08-11", "adjust": "qfq"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "market-data.v1"
    assert payload["data"]["symbols"] == ["600000", "000001"]
    assert payload["data"]["dates"] == ["2026-08-10", "2026-08-11"]
    assert payload["data"]["bars"]["close"] == [[10.5, 11.0], [None, 8.2]]
    assert len(payload["data"]["data_version"]) == 64


def test_bars_batch_rejects_reversed_dates():
    response = TestClient(market_data_api.app).post(
        "/v1/bars/batch",
        json={"symbols": ["600000"], "start": "2026-08-11", "end": "2026-08-10"},
    )
    assert response.status_code == 422
