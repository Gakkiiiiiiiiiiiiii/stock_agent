"""SectorFeatureBuilder 测试：全量成分股、覆盖度、加权强度分、质量标记、确定性。"""
from __future__ import annotations

from datetime import date, timedelta

from engines.market.sector_feature_builder import (
    LOW_COVERAGE,
    SectorFeatureBuilder,
)

WEIGHTS = {
    "trend": 0.25,
    "breadth": 0.25,
    "relative_strength": 0.20,
    "liquidity": 0.15,
    "momentum": 0.10,
    "risk_penalty": 0.05,
}


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


def _make_quote(kline: list[dict], day_change: float = 0.01) -> dict:
    last_close = kline[-1]["close"]
    return {
        "last_price": round(last_close * (1 + day_change), 4),
        "last_close": last_close,
        "amount": kline[-1]["amount"],
    }


class FakeDataAccess:
    def __init__(self, quotes: dict, klines: dict) -> None:
        self.quotes = quotes
        self.klines = klines

    def get_quotes(self, symbols):
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def get_kline(self, symbol):
        return {"records": self.klines.get(symbol, [])}


def _build_universe() -> tuple[dict[str, list[str]], dict, dict]:
    membership = {"半导体": [], "医药": [], "银行": []}
    drifts = {"半导体": 0.004, "医药": 0.0, "银行": -0.003}
    sizes = {"半导体": 20, "医药": 20, "银行": 10}
    quotes: dict = {}
    klines: dict = {}
    seed = 0
    for sector, size in sizes.items():
        for i in range(size):
            symbol = f"60{seed:04d}.SH"
            kline = _make_kline(seed, drifts[sector])
            klines[symbol] = kline
            quotes[symbol] = _make_quote(kline)
            membership[sector].append(symbol)
            seed += 1
    return membership, quotes, klines


def _market_context(quotes: dict) -> dict:
    return {
        "market_return_5d": 0.5,
        "market_return_20d": 1.0,
        "total_market_amount": sum(q["amount"] for q in quotes.values()),
        "trade_date": date(2026, 8, 7),
    }


def test_full_universe_used_no_sampling_cap():
    membership, quotes, klines = _build_universe()
    builder = SectorFeatureBuilder(FakeDataAccess(quotes, klines))
    features = builder.build_sector_features(membership, _market_context(quotes))
    assert features["半导体"]["universe_size"] == 20  # 不再是 3 只样本
    assert features["医药"]["universe_size"] == 20
    assert features["银行"]["universe_size"] == 10
    assert features["半导体"]["valid_symbol_count"] == 20
    assert features["半导体"]["coverage"] == 1.0


def test_sector_feature_fields_populated():
    membership, quotes, klines = _build_universe()
    builder = SectorFeatureBuilder(FakeDataAccess(quotes, klines))
    feature = builder.build_sector_features(membership, _market_context(quotes))["半导体"]
    for key in (
        "return_1d_median", "return_5d_median", "return_20d_median",
        "relative_return_5d", "relative_return_20d",
        "up_ratio", "above_ma20_ratio", "above_ma60_ratio", "new_high_20d_ratio", "positive_5d_ratio",
        "sector_amount", "amount_share", "amount_change_5d", "amount_percentile_120d",
        "limit_up_count", "limit_down_count", "big_up_count", "big_down_count",
        "volatility_20d", "max_drawdown_20d",
    ):
        assert feature[key] is not None, key
    assert feature["up_ratio"] == 1.0
    assert feature["relative_return_5d"] == feature["return_5d_median"] - 0.5
    assert feature["quality_flags"] == []


def test_compute_strength_scores_weights_and_ranking():
    membership, quotes, klines = _build_universe()
    builder = SectorFeatureBuilder(FakeDataAccess(quotes, klines))
    features = builder.build_sector_features(membership, _market_context(quotes))
    results = builder.compute_strength(features, WEIGHTS)
    assert len(results) == 3
    assert [item.rank for item in results] == [1, 2, 3]
    # 涨得最多的半导体应排第一，下跌的银行垫底
    assert results[0].sector == "半导体"
    assert results[-1].sector == "银行"
    for item in results:
        assert 0.0 <= item.strength_score <= 100.0
        components = item.components
        expected = (
            WEIGHTS["trend"] * components.trend
            + WEIGHTS["breadth"] * components.breadth
            + WEIGHTS["relative_strength"] * components.relative_strength
            + WEIGHTS["liquidity"] * components.liquidity
            + WEIGHTS["momentum"] * components.momentum
            - WEIGHTS["risk_penalty"] * components.risk_penalty
        )
        expected = round(max(0.0, min(100.0, expected)), 2)
        assert abs(item.strength_score - expected) < 1e-6
        assert item.universe_size > 3
        assert item.coverage is not None


def test_low_coverage_flag():
    membership, quotes, klines = _build_universe()
    # 10 只成分股，仅 3 只有有效数据 → coverage 0.3 < 0.6
    cold_symbols = [f"60{i:04d}.SH" for i in range(90, 100)]
    for symbol in cold_symbols[:3]:
        kline = _make_kline(int(symbol[2:6]), 0.001)
        klines[symbol] = kline
        quotes[symbol] = _make_quote(kline)
    membership["冷清板块"] = cold_symbols
    builder = SectorFeatureBuilder(FakeDataAccess(quotes, klines))
    features = builder.build_sector_features(membership, _market_context(quotes))
    cold = features["冷清板块"]
    assert cold["universe_size"] == 10
    assert cold["valid_symbol_count"] == 3
    assert cold["coverage"] == 0.3
    assert LOW_COVERAGE in cold["quality_flags"]
    # 常规板块不受影响
    assert LOW_COVERAGE not in features["半导体"]["quality_flags"]


def test_builder_deterministic():
    membership, quotes, klines = _build_universe()
    context = _market_context(quotes)
    outputs = []
    for _ in range(2):
        builder = SectorFeatureBuilder(FakeDataAccess(quotes, klines))
        features = builder.build_sector_features(membership, context)
        results = builder.compute_strength(features, WEIGHTS)
        outputs.append([item.model_dump(mode="json") for item in results])
    assert outputs[0] == outputs[1]
