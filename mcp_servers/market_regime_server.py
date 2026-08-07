from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from engines.market.breadth_engine import compute_breadth
from engines.market.crowding_engine import compute_crowding_score
from engines.market.feature_builder import MarketFeatureBuilder
from engines.market.range_engine import compute_range_score
from engines.market.risk_engine import compute_drawdown_risk
from engines.market.rotation_engine import compute_rotation_score
from engines.market.trend_engine import compute_trend_score
from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.llm_regime_judge import judge_regime_with_llm_hint
from engines.regime.regime_preclassifier import preclassify_regime
from engines.regime.regime_state_machine import PersistentRegimeStateMachine, resolve_regime_transition


class MarketFeatureSnapshot(BaseModel):
    as_of: datetime
    universe_size: int | None = None
    requested_quote_count: int | None = None
    received_quote_count: int | None = None
    quote_coverage: float | None = None
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
    high_position_limit_down_ratio: float | None = None
    high_position_breakdown_ratio: float | None = None
    high_position_big_negative_count: int | None = None
    high_position_pool_size: int | None = None
    high_position_valid_count: int | None = None
    high_position_quote_coverage: float | None = None
    high_position_prev_close_mismatch_count: int | None = None
    high_position_prev_close_mismatch_ratio: float | None = None
    high_position_quality_flags: list[str] = []
    quality_score: float = 0.0
    quality_flags: list[str] = []
    warning: str | None = None


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
        "quality_flags": sorted(set(["MARKET_FEATURES_INCOMPLETE", *snapshot.quality_flags])),
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
    high_position_loss_ratio: float | None = None,
    high_position_limit_down_ratio: float | None = None,
    high_position_breakdown_ratio: float | None = None,
    high_position_big_negative_count: int | None = None,
    retreat_days: int | None = None,
    force_refresh: bool = False,
    market_code: str = "CN_A",
    persist_state: bool = True,
) -> dict:
    _ = force_refresh
    if snapshot is None:
        if any(value is not None for value in (up_count, down_count, index_return_5d, index_return_20d, limit_up_count, limit_down_count, index_volatility, index_volatility_20d, high_position_loss_ratio, high_position_limit_down_ratio, high_position_breakdown_ratio, high_position_big_negative_count, retreat_days)):
            snapshot_data = {
                "as_of": as_of or datetime.now(timezone.utc),
                "up_count": up_count,
                "down_count": down_count,
                "index_return_5d": index_return_5d,
                "index_return_20d": index_return_20d,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "index_volatility_20d": index_volatility_20d if index_volatility_20d is not None else index_volatility,
                "high_position_loss_ratio": high_position_loss_ratio,
                "high_position_limit_down_ratio": high_position_limit_down_ratio,
                "high_position_breakdown_ratio": high_position_breakdown_ratio,
                "high_position_big_negative_count": high_position_big_negative_count if high_position_big_negative_count is not None else retreat_days,
            }
        else:
            snapshot_data = MarketFeatureBuilder().build(as_of=as_of, force_refresh=force_refresh)
            top_theme_strength = snapshot_data.pop("top_theme_strength", top_theme_strength)
            index_drawdown_20d = snapshot_data.pop("index_drawdown_20d", index_drawdown_20d)
        snapshot_obj = MarketFeatureSnapshot.model_validate(snapshot_data)
    elif isinstance(snapshot, MarketFeatureSnapshot):
        snapshot_obj = snapshot
    else:
        snapshot_obj = MarketFeatureSnapshot.model_validate(snapshot)

    missing_fields = _missing_fields(snapshot_obj)
    retreat_missing = [
        name for name in ("high_position_loss_ratio", "high_position_limit_down_ratio", "high_position_breakdown_ratio", "high_position_big_negative_count")
        if getattr(snapshot_obj, name) is None
    ]
    quality_blockers = []
    if snapshot_obj.warning:
        quality_blockers.append("MARKET_FEATURE_WARNING")
    if snapshot_obj.quality_score < 0.8:
        quality_blockers.append("MARKET_FEATURE_QUALITY_LOW")
    if snapshot_obj.quote_coverage is not None and snapshot_obj.quote_coverage < 0.9:
        quality_blockers.append("MARKET_QUOTE_COVERAGE_LOW")
    if "MARKET_QUOTE_COVERAGE_LOW" in snapshot_obj.quality_flags:
        quality_blockers.append("MARKET_QUOTE_COVERAGE_LOW")
    if snapshot_obj.high_position_quote_coverage is not None and snapshot_obj.high_position_quote_coverage < 0.8:
        quality_blockers.append("HIGH_POSITION_QUOTE_COVERAGE_LOW")
    high_position_blockers = {
        "HIGH_POSITION_POOL_TOO_SMALL",
        "HIGH_POSITION_VALID_COUNT_LOW",
        "HIGH_POSITION_QUOTE_COVERAGE_LOW",
        "HIGH_POSITION_FEATURES_UNAVAILABLE",
        "HIGH_POSITION_PREV_CLOSE_MISMATCH",
    }
    quality_blockers.extend([flag for flag in snapshot_obj.high_position_quality_flags if flag in high_position_blockers])
    if missing_fields or top_theme_strength is None or index_drawdown_20d is None or retreat_missing or quality_blockers:
        extra_missing = []
        if top_theme_strength is None:
            extra_missing.append("top_theme_strength")
        if index_drawdown_20d is None:
            extra_missing.append("index_drawdown_20d")
        return _unknown_result(snapshot_obj, previous_regime, missing_fields + extra_missing + retreat_missing + quality_blockers)

    breadth = compute_breadth(snapshot_obj.up_count, snapshot_obj.down_count)
    crowding_score = compute_crowding_score(top_theme_strength, snapshot_obj.limit_up_count)
    rotation_score = compute_rotation_score(top_theme_strength, breadth)
    range_score = compute_range_score(snapshot_obj.index_volatility_20d, breadth)
    drawdown_risk = compute_drawdown_risk(index_drawdown_20d, snapshot_obj.limit_down_count)
    retreat = detect_high_position_retreat(
        snapshot_obj.high_position_loss_ratio,
        snapshot_obj.high_position_limit_down_ratio,
        snapshot_obj.high_position_breakdown_ratio,
        snapshot_obj.high_position_big_negative_count,
    )
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
    if persist_state and previous_regime is None:
        from storage.bootstrap import create_all

        create_all()
        state = PersistentRegimeStateMachine().advance(
            market_code=market_code,
            candidate_regime=regime["primary_regime"],
            as_of=snapshot_obj.as_of,
            confidence=regime.get("confidence"),
            features=features,
            transition_reason={"llm_hint": llm_hint},
        )
    else:
        state = resolve_regime_transition(previous_regime=previous_regime, candidate_regime=regime["primary_regime"])
    return {
        "snapshot": snapshot_obj.model_dump(),
        "features": features,
        "regime": regime,
        "llm_hint": llm_hint,
        "state_machine": state,
        "retreat": retreat,
        "quality_flags": snapshot_obj.quality_flags,
        "missing_fields": [],
    }


def get_high_position_retreat() -> dict:
    return detect_high_position_retreat(0.4, 0.25, 0.3, 3)
