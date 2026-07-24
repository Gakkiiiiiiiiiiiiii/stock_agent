from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

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


class MarketFeatureSnapshot(BaseModel):
    as_of: datetime
    universe_size: int | None = None
    up_count: int | None = None
    down_count: int | None = None
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    above_ma20_ratio: float | None = None
    above_ma60_ratio: float | None = None
    median_return_5d: float | None = None
    index_return_5d: float | None = None
    index_return_20d: float | None = None
    index_volatility_20d: float | None = None
    turnover_amount: float | None = None
    turnover_percentile_1y: float | None = None
    top10_amount_share: float | None = None
    sector_dispersion: float | None = None
    sector_rotation_speed: float | None = None
    high_position_loss_ratio: float | None = None
    quality_score: float = 0.0


def _missing_fields(snapshot: MarketFeatureSnapshot) -> list[str]:
    required = (
        "up_count",
        "down_count",
        "limit_up_count",
        "limit_down_count",
        "index_return_5d",
        "index_return_20d",
        "index_volatility_20d",
    )
    return [name for name in required if getattr(snapshot, name) is None]


def _unknown_result(snapshot: MarketFeatureSnapshot, previous_regime: str | None, missing_fields: list[str]) -> dict:
    features = {
        "breadth": None,
        "trend_score": None,
        "crowding_score": None,
        "rotation_score": None,
        "range_score": None,
        "drawdown_risk": None,
        "retreat_score": None,
    }
    regime = preclassify_regime(features)
    state = resolve_regime_transition(previous_regime=previous_regime, candidate_regime=regime["primary_regime"])
    return {
        "snapshot": snapshot.model_dump(),
        "features": features,
        "regime": regime,
        "llm_hint": {"regime": "UNKNOWN", "reason": "market feature snapshot is incomplete"},
        "state_machine": state,
        "retreat": None,
        "quality_flags": ["MARKET_FEATURES_INCOMPLETE"],
        "missing_fields": missing_fields,
    }


def get_market_regime(
    snapshot: dict | MarketFeatureSnapshot | None = None,
    as_of: datetime | None = None,
    up_count: int | None = None,
    down_count: int | None = None,
    index_return_5d: float | None = None,
    index_return_20d: float | None = None,
    top_theme_strength: float | None = None,
    limit_up_count: int | None = None,
    index_volatility: float | None = None,
    index_volatility_20d: float | None = None,
    index_drawdown_20d: float | None = None,
    limit_down_count: int | None = None,
    previous_regime: str | None = None,
) -> dict:
    if snapshot is None:
        snapshot_obj = MarketFeatureSnapshot(
            as_of=as_of or datetime.now(timezone.utc),
            up_count=up_count,
            down_count=down_count,
            index_return_5d=index_return_5d,
            index_return_20d=index_return_20d,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            index_volatility_20d=index_volatility_20d if index_volatility_20d is not None else index_volatility,
        )
    elif isinstance(snapshot, MarketFeatureSnapshot):
        snapshot_obj = snapshot
    else:
        snapshot_obj = MarketFeatureSnapshot.model_validate(snapshot)

    missing_fields = _missing_fields(snapshot_obj)
    if missing_fields or top_theme_strength is None or index_drawdown_20d is None:
        extra_missing = []
        if top_theme_strength is None:
            extra_missing.append("top_theme_strength")
        if index_drawdown_20d is None:
            extra_missing.append("index_drawdown_20d")
        return _unknown_result(snapshot_obj, previous_regime, missing_fields + extra_missing)

    breadth = compute_breadth(snapshot_obj.up_count, snapshot_obj.down_count)
    crowding_score = compute_crowding_score(top_theme_strength, snapshot_obj.limit_up_count)
    rotation_score = compute_rotation_score(top_theme_strength, breadth)
    range_score = compute_range_score(snapshot_obj.index_volatility_20d, breadth)
    drawdown_risk = compute_drawdown_risk(index_drawdown_20d, snapshot_obj.limit_down_count)
    retreat = detect_high_position_retreat(0.35, 0.2, 0.22, 2)
    features = {
        "breadth": breadth,
        "trend_score": compute_trend_score(snapshot_obj.index_return_5d, snapshot_obj.index_return_20d),
        "crowding_score": crowding_score,
        "rotation_score": rotation_score,
        "range_score": range_score,
        "drawdown_risk": drawdown_risk,
        "retreat_score": retreat["retreat_score"],
    }
    regime = preclassify_regime(features)
    llm_hint = judge_regime_with_llm_hint(features)
    state = resolve_regime_transition(previous_regime=previous_regime, candidate_regime=regime["primary_regime"])
    return {
        "snapshot": snapshot_obj.model_dump(),
        "features": features,
        "regime": regime,
        "llm_hint": llm_hint,
        "state_machine": state,
        "retreat": retreat,
        "quality_flags": [],
        "missing_fields": [],
    }


def get_high_position_retreat() -> dict:
    return detect_high_position_retreat(0.4, 0.25, 0.3, 3)
