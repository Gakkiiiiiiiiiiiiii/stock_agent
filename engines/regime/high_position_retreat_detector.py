from __future__ import annotations


def detect_high_position_retreat(
    leader_drawdown_score: float,
    limit_up_next_day_loss_rate: float,
    b2_b3_failure_rate: float,
    high_position_big_negative_count: int,
) -> dict:
    retreat_score = (
        leader_drawdown_score * 0.35
        + limit_up_next_day_loss_rate * 0.25
        + b2_b3_failure_rate * 0.25
        + min(high_position_big_negative_count / 10, 1.0) * 0.15
    )
    retreat_score = round(max(0.0, min(1.0, retreat_score)), 4)
    return {
        "retreat_score": retreat_score,
        "is_high_position_retreat": retreat_score >= 0.65,
        "evidence": {
            "leader_drawdown_score": leader_drawdown_score,
            "limit_up_next_day_loss_rate": limit_up_next_day_loss_rate,
            "b2_b3_failure_rate": b2_b3_failure_rate,
            "high_position_big_negative_count": high_position_big_negative_count,
        },
    }

