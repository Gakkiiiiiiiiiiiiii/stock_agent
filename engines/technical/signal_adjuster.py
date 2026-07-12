from __future__ import annotations


def adjust_signal_score(raw_signal_score: float, market_regime: str, theme_strength: float = 50, liquidity_ok: bool = True) -> dict:
    score = raw_signal_score
    if market_regime == "rotation_market":
        score += 5 if raw_signal_score <= 80 else -5
    elif market_regime == "high_position_retreat":
        score -= 12
    elif market_regime == "downtrend_market":
        score -= 18
    if theme_strength >= 75:
        score += 5
    elif theme_strength <= 45:
        score -= 5
    if not liquidity_ok:
        score -= 10
    final_score = round(max(0.0, min(100.0, score)), 2)
    return {"raw_signal_score": raw_signal_score, "adjusted_signal_score": final_score, "selected": final_score >= 70}

