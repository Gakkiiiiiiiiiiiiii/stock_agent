"""MarketFeatureService / SectorFeatureService 测试：假 provider 驱动，无网络依赖。"""
from __future__ import annotations

from datetime import date, timedelta

from engines.market.feature_service import MarketFeatureService, SectorFeatureService

SECTOR_RESULT_KEYS = {
    "sector",
    "strength_score",
    "rank",
    "universe_size",
    "valid_symbol_count",
    "coverage",
    "components",
    "as_of",
    "feature_version",
    "quality_flags",
}
COMPONENT_KEYS = {"trend", "breadth", "relative_strength", "liquidity", "momentum", "risk_penalty"}


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


class FakeProvider:
    """鸭子类型 provider：同时提供 snapshot / industry map / quotes / kline。"""

    def __init__(self) -> None:
        self.klines: dict[str, list[dict]] = {}
        self.quotes: dict[str, dict] = {}
        self.industry_rows: list[dict] = []
        seed = 0
        for sector, code, drift in (("半导体", "GICS_SEMI", 0.004), ("银行", "GICS_BANK", -0.003)):
            for _ in range(12):
                symbol = f"60{seed:04d}.SH"
                kline = _make_kline(seed, drift)
                self.klines[symbol] = kline
                last_close = kline[-1]["close"]
                self.quotes[symbol] = {
                    "symbol": symbol,
                    "last_price": round(last_close * 1.01, 4),
                    "last_close": last_close,
                    "amount": kline[-1]["amount"],
                }
                self.industry_rows.append({"symbol": symbol, "industry_name": sector, "industry_code": code})
                seed += 1

    def get_market_snapshot(self, as_of=None, force_refresh=False) -> dict:
        total_amount = sum(quote["amount"] for quote in self.quotes.values())
        return {
            "market_regime": "震荡偏强",
            "risk_appetite": "中等",
            "turnover_amount": total_amount,
            "universe_size": len(self.quotes),
            "up_count": 12,
            "down_count": 12,
            "limit_up_count": 1,
            "limit_down_count": 0,
            "quote_coverage": 0.97,
            "quality_flags": [],
            "source": "fake",
            "indices": {"return_5d_pct": 0.8, "return_20d_pct": 1.5, "volatility_20d_pct": 1.1, "drawdown_20d_pct": -2.0},
        }

    def get_sector_strength(self, top_k: int = 20, as_of=None) -> list[dict]:
        return [
            {"sector": "半导体", "strength_score": 80.0, "change_pct": 1.2},
            {"sector": "银行", "strength_score": 40.0, "change_pct": -0.6},
        ][:top_k]

    def get_industry_map(self, symbols=None, sector_prefix: str = "GICS2", only_a_share: bool = True) -> list[dict]:
        return list(self.industry_rows)

    def get_quotes(self, symbols) -> dict:
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def get_kline(self, symbol, **kwargs) -> dict:
        return {"records": self.klines.get(symbol, [])}


class RecordingRepository:
    """鸭子类型仓库，签名与 storage.repositories.MarketFeatureRepository 一致。"""

    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_sector_snapshot(
        self,
        sector_name,
        trade_date,
        as_of,
        component_scores,
        final_score,
        feature_version,
        sector_code=None,
        universe_size=0,
        valid_symbol_count=0,
        coverage=0.0,
        quality_flags=None,
    ) -> None:
        self.saved.append(
            {
                "sector_name": sector_name,
                "trade_date": trade_date,
                "as_of": as_of,
                "component_scores": component_scores,
                "final_score": final_score,
                "feature_version": feature_version,
                "sector_code": sector_code,
                "universe_size": universe_size,
                "valid_symbol_count": valid_symbol_count,
                "coverage": coverage,
                "quality_flags": quality_flags,
            }
        )

    def get_sector_snapshots(self, trade_date, feature_version=None):
        return []


class FailingRepository:
    def save_sector_snapshot(self, *args, **kwargs) -> None:
        raise RuntimeError("db down")

    def get_sector_snapshots(self, *args, **kwargs):
        raise RuntimeError("db down")


def test_market_features_shape_and_meta():
    service = MarketFeatureService(provider=FakeProvider())
    result = service.get_market_features()
    assert set(result) == {"data", "meta"}
    meta = result["meta"]
    assert meta["calculation_version"] == "market_feature_v2"
    assert meta["data_source"] == "fake"
    assert meta["coverage"] == 0.97
    assert meta["confidence"] is not None
    assert isinstance(meta["warnings"], list)
    assert isinstance(meta["quality_flags"], list)
    data = result["data"]
    assert data["up_count"] == 12
    assert data["index_return_5d"] is not None


def test_sector_strength_matches_design_doc_shape():
    service = SectorFeatureService(provider=FakeProvider())
    results = service.get_sector_strength(top_k=20)
    assert len(results) == 2
    for item in results:
        assert SECTOR_RESULT_KEYS <= set(item)
        assert set(item["components"]) == COMPONENT_KEYS
        assert item["feature_version"] == "sector_strength_v2"
        assert item["universe_size"] == 12  # 全量成分股，非 3 只样本
        assert item["valid_symbol_count"] == 12
        assert item["coverage"] == 1.0
        assert 0.0 <= item["strength_score"] <= 100.0
    # rank 顺序与分数一致
    assert [item["rank"] for item in results] == [1, 2]
    assert results[0]["strength_score"] >= results[1]["strength_score"]
    assert results[0]["sector"] == "半导体"


def test_sector_strength_repository_saved_and_cached():
    repository = RecordingRepository()
    service = SectorFeatureService(provider=FakeProvider(), repository=repository)
    results = service.get_sector_strength(top_k=1)
    assert len(results) == 1
    assert len(repository.saved) == 1
    saved = repository.saved[0]
    assert saved["sector_name"] == results[0]["sector"]
    assert saved["final_score"] == results[0]["strength_score"]
    assert saved["feature_version"] == results[0]["feature_version"]


def test_sector_strength_persistence_failure_does_not_break():
    service = SectorFeatureService(provider=FakeProvider(), repository=FailingRepository())
    results = service.get_sector_strength()
    assert len(results) == 2


def test_get_sector_features_single_sector():
    service = SectorFeatureService(provider=FakeProvider())
    detail = service.get_sector_features("半导体")
    assert detail is not None
    assert detail["sector"] == "半导体"
    assert detail["sector_code"] == "GICS_SEMI"
    assert detail["strength_score"] is not None
    assert detail["feature_version"] == "sector_strength_v2"
    assert set(detail["components"]) == COMPONENT_KEYS
    assert service.get_sector_features("不存在的板块") is None
