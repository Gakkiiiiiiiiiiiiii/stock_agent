from engines.portfolio.portfolio_construction_engine import construct_portfolio_actions


def test_construct_portfolio_actions_respects_total_position():
    result = construct_portfolio_actions(
        candidates=[
            {"symbol": "A", "theme": "黄金", "final_signal_score": 88, "suggested_weight": 0.08},
            {"symbol": "B", "theme": "AI", "final_signal_score": 82, "suggested_weight": 0.08},
        ],
        positions=[{"symbol": "OLD", "theme": "黄金", "weight": 0.55, "market_value": 55}],
        risk_limits={"max_total_position": 0.65, "max_single_stock": 0.08},
    )
    assert result["total_position_after"] <= 0.71
    assert result["actions"]

