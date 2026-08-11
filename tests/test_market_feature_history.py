from __future__ import annotations

from datetime import date, datetime

from engines.market.data_provider import QmtMarketDataProvider
from engines.market.feature_service import SectorFeatureService


class Membership:
    def __init__(self, symbol: str, sector_name: str, sector_code: str) -> None:
        self.symbol, self.sector_name, self.sector_code = symbol, sector_name, sector_code


class Repo:
    def __init__(self) -> None:
        self.requested: list[date] = []

    def get_memberships_at(self, *, at_date: date):
        self.requested.append(at_date)
        return [Membership("000001.SZ", "历史板块", "HIST")]


class Provider:
    def __init__(self) -> None:
        self.end_times: list[str] = []

    def get_industry_map(self, **_kwargs):
        raise AssertionError("historical request must not use current industry map")

    def get_history(self, **kwargs):
        self.end_times.append(kwargs["end_time"])
        return [
            {"symbol": "000001.SZ", "trading_date": "2025-01-01", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1, "amount": 10},
            {"symbol": "000001.SZ", "trading_date": "2025-01-02", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 2, "amount": 22},
        ]

    def get_quotes(self, _symbols):
        return {}

    def get_market_snapshot(self, as_of=None):
        return {"indices": {}, "trade_date": as_of}


def test_historical_sector_feature_uses_membership_at_and_as_of_kline_cutoff():
    provider, repo = Provider(), Repo()
    result = SectorFeatureService(provider=provider, repository=repo).get_sector_strength(as_of=date(2025, 1, 2), read_cache=False)
    assert repo.requested == [date(2025, 1, 2)]
    assert provider.end_times and set(provider.end_times) == {"20250102"}
    assert result[0]["sector"] == "历史板块"


class Bridge:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_history(self, **kwargs):
        self.calls.append(kwargs)
        symbols = kwargs["symbols"]
        rows = []
        for symbol in symbols:
            for day, close in (("2024-12-01", 10), ("2024-12-31", 11), ("2025-01-02", 12)):
                rows.append({"symbol": symbol, "trading_date": day, "open": close, "high": close, "low": close, "close": close, "volume": 10, "amount": close * 10})
        return rows


def test_historical_market_snapshot_never_requests_data_after_as_of():
    bridge, repo = Bridge(), Repo()
    provider = QmtMarketDataProvider(bridge=bridge)
    provider._feature_repository = repo
    snapshot = provider.get_market_snapshot(as_of=date(2025, 1, 2))
    assert snapshot["source"] == "qmt"
    assert all(call["end_time"] <= "20250102" for call in bridge.calls)
    assert "HISTORICAL_MARKET_SNAPSHOT_UNAVAILABLE" not in snapshot.get("quality_flags", [])
