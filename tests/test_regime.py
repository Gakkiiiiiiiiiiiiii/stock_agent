from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.regime_preclassifier import preclassify_regime
from engines.market.feature_builder import MarketFeatureBuilder
from mcp_servers.market_regime_server import get_market_regime


def test_high_position_retreat_detector():
    result = detect_high_position_retreat(0.8, 0.6, 0.7, 8)
    assert result["is_high_position_retreat"] is True
    assert result["retreat_score"] >= 0.65


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
        def get_market_snapshot(self):
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
            }

        def get_sector_strength(self):
            return [{"sector": "TMT", "strength_score": 86, "change_pct": 3.2}, {"sector": "红利", "strength_score": 62, "change_pct": 0.4}]

    monkeypatch.setattr("engines.market.feature_builder.get_market_data_provider", lambda: Provider())
    built = MarketFeatureBuilder().build()
    assert built["up_count"] == 3200
    assert built["top_theme_strength"] == 86
    result = get_market_regime(snapshot=built, top_theme_strength=built["top_theme_strength"], index_drawdown_20d=built["index_drawdown_20d"])
    assert result["missing_fields"] == []
    assert result["regime"]["primary_regime"] != "UNKNOWN"
