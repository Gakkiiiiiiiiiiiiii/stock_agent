from __future__ import annotations


def allowed_tools_for_task(task_type: str) -> list[str]:
    mapping = {
        "stock_analysis": [
            "retrieve_relevant_context",
            "calc_technical_indicators",
            "detect_pattern_signal",
            "get_market_regime",
            "route_strategy",
            "search_theme_logic",
            "evaluate_portfolio_risk",
        ],
        "theme_analysis": [
            "retrieve_relevant_context",
            "search_theme_logic",
            "get_theme_related_stocks",
            "evaluate_theme_trigger",
        ],
    }
    return mapping.get(task_type, [])

