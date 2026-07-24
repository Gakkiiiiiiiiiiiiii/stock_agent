from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.regime_preclassifier import preclassify_regime
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
