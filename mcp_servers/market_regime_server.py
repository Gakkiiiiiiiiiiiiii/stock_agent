from __future__ import annotations

from engines.market.breadth_engine import compute_breadth
from engines.market.crowding_engine import compute_crowding_score
from engines.market.range_engine import compute_range_score
from engines.market.risk_engine import compute_drawdown_risk
from engines.market.rotation_engine import compute_rotation_score
from engines.market.trend_engine import compute_trend_score
from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.llm_regime_judge import judge_regime_with_llm_hint
from engines.regime.regime_preclassifier import preclassify_regime
from engines.regime.regime_state_machine import resolve_regime_transition


def get_market_regime(
    up_count: int = 2400,
    down_count: int = 1800,
    index_return_5d: float = 0.01,
    index_return_20d: float = 0.03,
    top_theme_strength: float = 72,
    limit_up_count: int = 28,
    index_volatility: float = 0.02,
    index_drawdown_20d: float = -0.04,
    limit_down_count: int = 8,
    previous_regime: str | None = None,
) -> dict:
    breadth = compute_breadth(up_count, down_count)
    crowding_score = compute_crowding_score(top_theme_strength, limit_up_count)
    rotation_score = compute_rotation_score(top_theme_strength, breadth)
    range_score = compute_range_score(index_volatility, breadth)
    drawdown_risk = compute_drawdown_risk(index_drawdown_20d, limit_down_count)
    retreat = detect_high_position_retreat(0.35, 0.2, 0.22, 2)
    features = {
        "breadth": breadth,
        "trend_score": compute_trend_score(index_return_5d, index_return_20d),
        "crowding_score": crowding_score,
        "rotation_score": rotation_score,
        "range_score": range_score,
        "drawdown_risk": drawdown_risk,
        "retreat_score": retreat["retreat_score"],
    }
    regime = preclassify_regime(features)
    llm_hint = judge_regime_with_llm_hint(features)
    state = resolve_regime_transition(previous_regime=previous_regime, candidate_regime=regime["primary_regime"])
    return {"features": features, "regime": regime, "llm_hint": llm_hint, "state_machine": state, "retreat": retreat}


def get_high_position_retreat() -> dict:
    return detect_high_position_retreat(0.4, 0.25, 0.3, 3)

