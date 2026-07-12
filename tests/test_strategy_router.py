from engines.strategy.strategy_router import route_strategies
from engines.technical.signal_adjuster import adjust_signal_score


def test_route_rotation_market():
    route = route_strategies("rotation_market")
    assert route["risk_limits"]["max_total_position"] == 0.65
    assert "B1" in route["preferred_strategies"]


def test_adjust_signal_high_position_retreat_penalty():
    result = adjust_signal_score(85, market_regime="high_position_retreat", theme_strength=80, liquidity_ok=True)
    assert result["adjusted_signal_score"] < 85

