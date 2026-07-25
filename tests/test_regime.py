from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.regime_preclassifier import preclassify_regime
from engines.market.high_position_feature_builder import HighPositionFeatureBuilder
from engines.market.feature_builder import MarketFeatureBuilder, pct_to_decimal
from mcp_servers.market_regime_server import get_market_regime
from datetime import date, timedelta


def test_high_position_retreat_detector():
    result = detect_high_position_retreat(0.8, 0.6, 0.7, 8)
    assert result["is_high_position_retreat"] is True
    assert result["retreat_score"] >= 0.65
    assert "high_position_breakdown_rate" in result["evidence"]


def test_regime_preclassifier_downtrend():
    result = preclassify_regime(
        {
            "breadth": 0.2,
            "crowding_score": 0.2,
            "drawdown_risk": 0.8,
            "retreat_score": 0.2,
        }
    )
    assert result["primary_regime"] == "downtrend_market"


def test_market_regime_missing_features_returns_unknown():
    result = get_market_regime()
    assert result["regime"]["primary_regime"] == "UNKNOWN"
    assert "MARKET_FEATURES_INCOMPLETE" in result["quality_flags"]
    assert "up_count" in result["missing_fields"]


def test_market_feature_builder_enables_regime_classification(monkeypatch):
    class Provider:
        def get_market_snapshot(self, as_of=None, force_refresh=False):
            return {
                "source": "fake",
                "up_count": 3200,
                "down_count": 900,
                "limit_up_count": 65,
                "limit_down_count": 2,
                "turnover_amount": 1_200_000_000_000,
                "top10_amount_share": 0.08,
                "indices": {
                    "return_5d_pct": 2.0,
                    "return_20d_pct": 5.0,
                    "volatility_20d_pct": 1.2,
                    "drawdown_20d_pct": -1.0,
                },
                "high_position_loss_ratio": 0.1,
                "high_position_limit_down_ratio": 0.02,
                "high_position_breakdown_ratio": 0.03,
                "high_position_big_negative_count": 1,
            }

        def get_sector_strength(self, as_of=None):
            return [{"sector": "TMT", "strength_score": 86, "change_pct": 3.2}, {"sector": "红利", "strength_score": 62, "change_pct": 0.4}]

    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    built = MarketFeatureBuilder().build()
    assert built["up_count"] == 3200
    assert built["top_theme_strength"] == 86
    result = get_market_regime(snapshot=built, top_theme_strength=built["top_theme_strength"], index_drawdown_20d=built["index_drawdown_20d"])
    assert result["missing_fields"] == []
    assert result["regime"]["primary_regime"] != "UNKNOWN"


def test_high_position_feature_builder_outputs_formal_features():
    class Bridge:
        def get_history(self, symbols, period, start_time, end_time, dividend_type, fill_data=True, prefer_cache_first=True):
            rows = []
            for idx, symbol in enumerate(symbols):
                base = 10 + idx
                for day in range(70):
                    close = base + day * (0.08 if idx < 3 else 0.01)
                    trading_day = date(2026, 3, 1) + timedelta(days=day)
                    rows.append(
                        {
                            "symbol": symbol,
                            "trading_date": trading_day.isoformat(),
                            "open": close * 0.99,
                            "high": close * 1.01,
                            "close": close,
                            "amount": 1_000_000 * (3 if idx < 3 and day == 69 else 1),
                        }
                    )
            return rows

    symbols = [f"60000{i}.SH" for i in range(12)]
    quotes = {
        symbol: {"last_price": 15 - i * 0.1, "last_close": 15, "open": 15.2}
        for i, symbol in enumerate(symbols)
    }
    features = HighPositionFeatureBuilder(Bridge()).build(symbols, quotes).as_dict()
    assert features["high_position_pool_size"] > 0
    assert features["high_position_valid_count"] >= 10
    assert features["high_position_quote_coverage"] >= 0.8
    assert features["high_position_loss_ratio"] is not None
    assert features["high_position_limit_down_ratio"] is not None
    assert features["high_position_breakdown_ratio"] is not None
    assert features["high_position_big_negative_count"] is not None


def test_high_position_builder_blocks_small_pool_quality():
    class Bridge:
        def get_history(self, symbols, period, start_time, end_time, dividend_type, fill_data=True, prefer_cache_first=True):
            rows = []
            for idx, symbol in enumerate(symbols):
                for day in range(35):
                    trading_day = date(2026, 3, 1) + timedelta(days=day)
                    close = 10 + day * 0.02 + idx * 0.01
                    rows.append(
                        {
                            "symbol": symbol,
                            "trading_date": trading_day.isoformat(),
                            "open": close,
                            "high": close * 1.01,
                            "close": close,
                            "amount": 1_000_000,
                        }
                    )
            return rows

    symbols = [f"60000{i}.SH" for i in range(6)]
    quotes = {symbol: {"last_price": 10, "last_close": 10, "open": 10} for symbol in symbols}
    features = HighPositionFeatureBuilder(Bridge()).build(symbols, quotes).as_dict()
    assert features["high_position_loss_ratio"] is None
    assert "HIGH_POSITION_POOL_TOO_SMALL" in features["high_position_quality_flags"]


def test_estimated_high_position_features_do_not_drive_regime(monkeypatch):
    class Provider:
        def get_market_snapshot(self, as_of=None, force_refresh=False):
            return {
                "source": "fake",
                "up_count": 3200,
                "down_count": 900,
                "limit_up_count": 65,
                "limit_down_count": 2,
                "indices": {
                    "return_5d_pct": 2.0,
                    "return_20d_pct": 5.0,
                    "volatility_20d_pct": 1.2,
                    "drawdown_20d_pct": -1.0,
                },
            }

        def get_sector_strength(self, as_of=None):
            return [{"sector": "TMT", "strength_score": 86, "change_pct": 3.2}]

    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    built = MarketFeatureBuilder().build()
    assert "HIGH_POSITION_FEATURES_ESTIMATED" in built["quality_flags"]
    assert built["high_position_loss_ratio"] is None
    assert built["estimated_high_position_loss_ratio"] is not None
    result = get_market_regime(snapshot=built, top_theme_strength=built["top_theme_strength"], index_drawdown_20d=built["index_drawdown_20d"])
    assert result["regime"]["primary_regime"] == "UNKNOWN"
    assert "high_position_loss_ratio" in result["missing_fields"]


def test_real_zero_high_position_values_are_preserved(monkeypatch):
    class Provider:
        def get_market_snapshot(self, as_of=None, force_refresh=False):
            return {
                "source": "fake",
                "up_count": 10,
                "down_count": 10,
                "limit_up_count": 1,
                "limit_down_count": 1,
                "indices": {"return_5d_pct": 0.5, "return_20d_pct": 1.0, "volatility_20d_pct": 1.0, "drawdown_20d_pct": -1.0},
                "high_position_loss_ratio": 0.0,
                "high_position_limit_down_ratio": 0.0,
                "high_position_breakdown_ratio": 0.0,
                "high_position_big_negative_count": 0,
            }

        def get_sector_strength(self, as_of=None):
            return [{"sector": "TMT", "strength_score": 50, "change_pct": 0.0}]

    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    built = MarketFeatureBuilder().build()
    assert built["high_position_loss_ratio"] == 0.0
    assert built["high_position_limit_down_ratio"] == 0.0
    assert built["high_position_breakdown_ratio"] == 0.0
    assert built["high_position_big_negative_count"] == 0


def test_pct_to_decimal_always_converts_percent_fields():
    assert pct_to_decimal(0.5) == 0.005
    assert pct_to_decimal(1.0) == 0.01
    assert pct_to_decimal(-1.0) == -0.01


def test_market_feature_builder_marks_historical_unavailable(monkeypatch):
    from datetime import datetime

    class Provider:
        def get_market_snapshot(self, as_of=None, force_refresh=False):
            assert str(as_of) == "2024-01-10"
            return {"warning": "HISTORICAL_MARKET_SNAPSHOT_UNAVAILABLE", "quality_score": 0.0, "source": "fake"}

        def get_sector_strength(self, as_of=None):
            assert str(as_of) == "2024-01-10"
            return []

    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    built = MarketFeatureBuilder().build(as_of=datetime(2024, 1, 10))
    result = get_market_regime(snapshot=built, top_theme_strength=built["top_theme_strength"], index_drawdown_20d=built["index_drawdown_20d"])
    assert result["regime"]["primary_regime"] == "UNKNOWN"
    assert "HISTORICAL_MARKET_SNAPSHOT_UNAVAILABLE" in result["snapshot"]["warning"]


def test_low_quote_coverage_blocks_regime():
    snapshot = {
        "as_of": "2026-07-25T00:00:00+00:00",
        "up_count": 100,
        "down_count": 50,
        "limit_up_count": 3,
        "limit_down_count": 1,
        "index_return_5d": 0.02,
        "index_return_20d": 0.05,
        "index_volatility_20d": 0.01,
        "high_position_loss_ratio": 0.1,
        "high_position_limit_down_ratio": 0.02,
        "high_position_breakdown_ratio": 0.03,
        "high_position_big_negative_count": 1,
        "quality_score": 0.0,
        "quote_coverage": 0.5,
        "quality_flags": ["MARKET_QUOTE_COVERAGE_LOW"],
    }
    result = get_market_regime(snapshot=snapshot, top_theme_strength=80, index_drawdown_20d=-0.01)
    assert result["regime"]["primary_regime"] == "UNKNOWN"
    assert "MARKET_QUOTE_COVERAGE_LOW" in result["missing_fields"]
