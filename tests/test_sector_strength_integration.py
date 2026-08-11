"""QmtMarketDataProvider.get_sector_strength 集成测试：假 QMT bridge 驱动全量成分路径。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from engines.market.data_provider import QmtMarketDataProvider
from engines.market.qmt_bridge_client import QmtBridgeError

LEGACY_KEYS = {"sector", "strength_score", "reason", "change_pct"}
NEW_KEYS = {
    "rank",
    "universe_size",
    "valid_symbol_count",
    "coverage",
    "components",
    "as_of",
    "feature_version",
    "quality_flags",
}
SECTORS = (("半导体", "GICS_SEMI", 0.004), ("银行", "GICS_BANK", -0.003), ("医药", "GICS_MED", 0.001))
SYMBOLS_PER_SECTOR = 10


def _make_kline(seed: int, drift: float, days: int = 130) -> list[dict]:
    base = date(2026, 3, 1)
    close = 10.0 + seed * 0.5
    records = []
    for i in range(days):
        prev = close
        wave = ((i + seed) % 7 - 3) * 0.01
        close = max(1.0, close * (1 + drift) + wave)
        volume = 1_000_000 * (1 + ((i + seed) % 5) / 10)
        records.append(
            {
                "date": base + timedelta(days=i),
                "open": round(prev, 4),
                "high": round(max(prev, close) * 1.01, 4),
                "low": round(min(prev, close) * 0.99, 4),
                "close": round(close, 4),
                "volume": round(volume, 2),
                "amount": round(volume * close, 2),
            }
        )
    return records


class FakeBridge:
    """鸭子类型 QMT bridge：行业映射 + 行情 + 历史 K 线，全内存实现。"""

    def __init__(self, missing_symbols: set[str] | None = None) -> None:
        self.klines: dict[str, list[dict]] = {}
        self.quotes: dict[str, dict] = {}
        self.industry_rows: list[dict] = []
        missing = set(missing_symbols or set())
        seed = 0
        for sector, code, drift in SECTORS:
            for _ in range(SYMBOLS_PER_SECTOR):
                symbol = f"60{seed:04d}.SH"
                seed += 1
                self.industry_rows.append({"symbol": symbol, "industry_name": sector, "industry_code": code})
                if symbol in missing:
                    continue
                kline = _make_kline(seed, drift)
                self.klines[symbol] = kline
                self.quotes[symbol] = {
                    "symbol": symbol,
                    "last_price": round(kline[-1]["close"] * 1.01, 4),
                    "last_close": kline[-1]["close"],
                    "amount": kline[-1]["amount"],
                }

    def get_industry_map(self, symbols=None, sector_prefix: str = "GICS2", only_a_share: bool = True) -> list[dict]:
        return list(self.industry_rows)

    def get_quotes(self, symbols) -> dict:
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def get_history(self, symbols, period="1d", start_time=None, end_time=None, dividend_type="front", fill_data=True, prefer_cache_first=True) -> list[dict]:
        rows = []
        for symbol in symbols:
            for record in self.klines.get(symbol, []):
                rows.append(
                    {
                        "symbol": symbol,
                        "trading_date": record["date"].isoformat(),
                        "open": record["open"],
                        "high": record["high"],
                        "low": record["low"],
                        "close": record["close"],
                        "volume": record["volume"],
                        "amount": record["amount"],
                    }
                )
        return rows


class BrokenBridge(FakeBridge):
    def get_industry_map(self, symbols=None, sector_prefix: str = "GICS2", only_a_share: bool = True) -> list[dict]:
        raise QmtBridgeError("bridge offline")


@pytest.fixture
def provider(monkeypatch):
    instance = QmtMarketDataProvider(bridge=FakeBridge())
    monkeypatch.setattr(instance, "_get_feature_repository", lambda: None)
    return instance


def test_full_universe_contract(provider):
    results = provider.get_sector_strength(top_k=20)
    assert len(results) == len(SECTORS)
    for item in results:
        assert LEGACY_KEYS <= set(item)
        assert NEW_KEYS <= set(item)
        assert item["universe_size"] == SYMBOLS_PER_SECTOR  # 全量成分股，非 3 只样本
        assert item["valid_symbol_count"] == SYMBOLS_PER_SECTOR
        assert item["coverage"] == 1.0
        assert item["feature_version"] == "sector_strength_v2"
        assert 0.0 <= item["strength_score"] <= 100.0
        assert isinstance(item["reason"], str) and item["reason"]
        assert item["change_pct"] is not None
        assert isinstance(item["quality_flags"], list)
    assert [item["rank"] for item in results] == [1, 2, 3]
    scores = [item["strength_score"] for item in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0]["sector"] == "半导体"  # 正漂移板块强度最高
    assert results[-1]["sector"] == "银行"


def test_determinism(provider):
    first = provider.get_sector_strength()
    second = provider.get_sector_strength()
    assert first == second


def test_top_k_respected(provider):
    results = provider.get_sector_strength(top_k=2)
    assert len(results) == 2
    assert [item["rank"] for item in results] == [1, 2]


def test_historical_as_of_returns_empty(provider):
    assert provider.get_sector_strength(as_of=date.today() - timedelta(days=5)) == []


def test_partial_data_quality_flags(monkeypatch):
    bridge = FakeBridge()
    banking_symbols = [row["symbol"] for row in bridge.industry_rows if row["industry_name"] == "银行"]
    missing = set(banking_symbols[3:])  # 银行板块 10 只中 7 只无行情且无 K 线
    bridge = FakeBridge(missing_symbols=missing)
    instance = QmtMarketDataProvider(bridge=bridge)
    monkeypatch.setattr(instance, "_get_feature_repository", lambda: None)
    results = instance.get_sector_strength()
    banking = next(item for item in results if item["sector"] == "银行")
    assert banking["universe_size"] == SYMBOLS_PER_SECTOR
    assert banking["valid_symbol_count"] == 3
    assert banking["coverage"] == pytest.approx(0.3, abs=1e-6)
    assert "LOW_COVERAGE" in banking["quality_flags"]


def test_bridge_error_returns_empty(monkeypatch):
    instance = QmtMarketDataProvider(bridge=BrokenBridge())
    monkeypatch.setattr(instance, "_get_feature_repository", lambda: None)
    assert instance.get_sector_strength() == []


def test_sampled_fallback_marked_with_quality_flag(provider, monkeypatch):
    def _boom(self, *args, **kwargs):
        raise RuntimeError("v2 path broken")

    monkeypatch.setattr("engines.market.feature_service.SectorFeatureService.get_sector_strength", _boom)
    results = provider.get_sector_strength()
    assert len(results) == len(SECTORS)
    for item in results:
        assert LEGACY_KEYS <= set(item)
        assert NEW_KEYS <= set(item)
        assert item["quality_flags"] == ["SECTOR_STRENGTH_FALLBACK_SAMPLE3"]
        assert item["feature_version"] == "sector_strength_v1_sample3"
        assert item["universe_size"] == SYMBOLS_PER_SECTOR
        assert item["valid_symbol_count"] == 3  # 旧逻辑每板块 3 只样本
    assert [item["rank"] for item in results] == [1, 2, 3]
