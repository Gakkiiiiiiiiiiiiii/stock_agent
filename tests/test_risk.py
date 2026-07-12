from engines.risk.portfolio_risk import evaluate_portfolio_risk
from financial_agent.models import Position


def test_portfolio_risk_warns_concentration():
    result = evaluate_portfolio_risk(
        [
            Position(symbol="A", theme="黄金", market_value=80),
            Position(symbol="B", theme="AI", market_value=20),
        ],
        max_single_weight=0.5,
    )
    assert result.warnings
    assert result.concentration[0]["symbol"] == "A"

