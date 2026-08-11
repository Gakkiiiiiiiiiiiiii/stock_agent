from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from engines.domain_result import DomainResultMeta

from engines.market.breadth_engine import compute_breadth
from engines.market.crowding_engine import compute_crowding_score
from engines.market.feature_builder import MarketFeatureBuilder
from engines.market.feature_service import MarketFeatureService
from engines.market.range_engine import compute_range_score
from engines.market.risk_engine import compute_drawdown_risk
from engines.market.rotation_engine import compute_rotation_score
from engines.market.trend_engine import compute_trend_score
from engines.regime.high_position_retreat_detector import detect_high_position_retreat
from engines.regime.llm_regime_judge import judge_regime_with_llm_hint
from engines.regime.regime_preclassifier import (
    UNKNOWN_REGIME,
    blend_probabilities,
    classify_regime_probabilities,
    preclassify_regime,
)
from engines.regime.regime_state_machine import (
    DEFAULT_EMERGENCY_REGIMES,
    PersistentRegimeStateMachine,
    resolve_regime_transition,
)
from engines.versioning import get_version
from financial_agent.config import load_yaml_config

DEFAULT_REGIME_MODEL_VERSION = "regime_model_v2"
DEFAULT_MARKET_FEATURE_VERSION = "market_feature_v1"
REGIME_LOW_DATA_CONFIDENCE = "REGIME_LOW_DATA_CONFIDENCE"
REGIME_FEATURE_SERVICE_FALLBACK = "REGIME_FEATURE_SERVICE_FALLBACK"

_REGIME_V2_DEFAULTS = {
    "llm_blend_weight": 0.25,
    "min_coverage": 0.9,
    "low_quality_confidence_scale": 0.5,
}


def _regime_v2_config() -> dict:
    try:
        data = load_yaml_config("market_regime_thresholds.yaml")
    except FileNotFoundError:
        return dict(_REGIME_V2_DEFAULTS)
    config = {**_REGIME_V2_DEFAULTS, **dict(data.get("regime_v2") or {})}
    transition = dict(data.get("transition") or {})
    emergency = transition.get("emergency_regimes")
    config["emergency_regimes"] = set(emergency) if emergency else set(DEFAULT_EMERGENCY_REGIMES)
    return config


def _transition_status(state: dict, emergency_regimes: set[str]) -> str:
    """将状态机 advance/resolve 结果映射为 stable|confirming|switched|emergency。"""
    switch_status = (state or {}).get("switch_status")
    if switch_status == "confirmed_switch":
        if (state or {}).get("candidate_regime") in emergency_regimes:
            return "emergency"
        return "switched"
    if switch_status == "watch_switch":
        return "confirming"
    return "stable"


def _best_effort_features(snapshot: MarketFeatureSnapshot, top_theme_strength: float | None, index_drawdown_20d: float | None) -> tuple[dict, dict | None]:
    """尽力计算子分数特征：输入缺失时对应子分数为 None（交由概率分类器折算 UNKNOWN 质量）。"""
    up_count, down_count = snapshot.up_count, snapshot.down_count
    breadth = compute_breadth(up_count, down_count) if up_count is not None and down_count is not None else None
    crowding_score = (
        compute_crowding_score(top_theme_strength, snapshot.limit_up_count)
        if top_theme_strength is not None and snapshot.limit_up_count is not None
        else None
    )
    rotation_score = (
        compute_rotation_score(top_theme_strength, breadth)
        if top_theme_strength is not None and breadth is not None
        else None
    )
    range_score = (
        compute_range_score(snapshot.index_volatility_20d, breadth)
        if snapshot.index_volatility_20d is not None and breadth is not None
        else None
    )
    drawdown_risk = (
        compute_drawdown_risk(index_drawdown_20d, snapshot.limit_down_count)
        if index_drawdown_20d is not None and snapshot.limit_down_count is not None
        else None
    )
    trend_score = (
        compute_trend_score(snapshot.index_return_5d, snapshot.index_return_20d)
        if snapshot.index_return_5d is not None and snapshot.index_return_20d is not None
        else None
    )
    retreat_values = (
        snapshot.high_position_loss_ratio,
        snapshot.high_position_limit_down_ratio,
        snapshot.high_position_breakdown_ratio,
        snapshot.high_position_big_negative_count,
    )
    retreat = detect_high_position_retreat(*retreat_values) if all(value is not None for value in retreat_values) else None
    features = {
        "breadth": breadth,
        "trend_score": trend_score,
        "crowding_score": crowding_score,
        "rotation_score": rotation_score,
        "range_score": range_score,
        "drawdown_risk": drawdown_risk,
        "retreat_score": retreat["retreat_score"] if retreat else None,
    }
    return features, retreat


def _regime_v2_output(
    features: dict,
    llm_hint: dict | None,
    state: dict | None,
    *,
    service_meta: dict | None,
    legacy_quality_blockers: list[str],
    config: dict | None = None,
) -> dict:
    """Regime v2 输出块：概率向量 + 置信度（可选 LLM 融合、数据质量降级）。"""
    config = config or _regime_v2_config()
    classified = classify_regime_probabilities(features)
    hint_probabilities = (llm_hint or {}).get("regime_probabilities")
    blended = blend_probabilities(
        classified["probabilities"],
        hint_probabilities,
        weight=float(config["llm_blend_weight"]),
    )
    unknown_mass = float(classified["unknown_probability"])
    # 融合仅在 5 个 regime 质量上进行，UNKNOWN 质量保留并重新按比例折算
    probabilities = {regime: round(probability * (1 - unknown_mass), 4) for regime, probability in blended.items()}
    if unknown_mass > 0:
        probabilities[UNKNOWN_REGIME] = round(unknown_mass, 4)
    primary = max(blended, key=lambda regime: blended[regime])
    if unknown_mass > blended[primary]:
        primary = UNKNOWN_REGIME
        confidence = 0.0
    else:
        confidence = round(probabilities[primary], 4)

    quality_flags: list[str] = []
    low_quality = bool(legacy_quality_blockers)
    coverage = (service_meta or {}).get("coverage")
    if (service_meta or {}).get("quality_status") == "INSUFFICIENT":
        low_quality = True
    if coverage is not None and float(coverage) < float(config["min_coverage"]):
        low_quality = True
    if low_quality:
        confidence = round(confidence * float(config["low_quality_confidence_scale"]), 4)
        quality_flags.append(REGIME_LOW_DATA_CONFIDENCE)

    feature_version = (service_meta or {}).get("calculation_version") or get_version("market_feature_version", DEFAULT_MARKET_FEATURE_VERSION)
    data_quality = (service_meta or {}).get("quality_status") or ("INSUFFICIENT" if legacy_quality_blockers else "UNKNOWN")
    return {
        "primary_regime": primary,
        "probabilities": probabilities,
        "confidence": confidence,
        "transition_status": _transition_status(state, set(config.get("emergency_regimes") or DEFAULT_EMERGENCY_REGIMES)),
        "feature_version": feature_version,
        "data_quality": data_quality,
        "regime_model_version": get_version("regime_model_version", DEFAULT_REGIME_MODEL_VERSION),
        "regime_v2_quality_flags": quality_flags,
    }


class MarketFeatureSnapshot(BaseModel):
    as_of: datetime
    meta: DomainResultMeta = Field(default_factory=DomainResultMeta)
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


def _unknown_result(
    snapshot: MarketFeatureSnapshot,
    previous_regime: str | None,
    missing_fields: list[str],
    top_theme_strength: float | None = None,
    index_drawdown_20d: float | None = None,
    service_meta: dict | None = None,
) -> dict:
    # 旧版拒绝路径保持原语义（regime=UNKNOWN）；v2 输出层尽力给出降级后的概率答案
    best_effort_features, retreat = _best_effort_features(snapshot, top_theme_strength, index_drawdown_20d)
    legacy_features = {
        "breadth": None,
        "trend_score": None,
        "crowding_score": None,
        "rotation_score": None,
        "range_score": None,
        "drawdown_risk": None,
        "retreat_score": None,
    }
    regime = preclassify_regime(legacy_features)
    state = resolve_regime_transition(previous_regime=previous_regime, candidate_regime=regime["primary_regime"])
    llm_hint = {"regime": "UNKNOWN", "reason": "market feature snapshot is incomplete"}
    quality_flags = sorted(set(["MARKET_FEATURES_INCOMPLETE", *snapshot.quality_flags]))
    v2 = _regime_v2_output(
        best_effort_features,
        llm_hint,
        state,
        service_meta=service_meta,
        legacy_quality_blockers=missing_fields,
    )
    quality_flags = sorted(set(quality_flags) | set(v2.pop("regime_v2_quality_flags")))
    return {
        "snapshot": snapshot.model_copy(update={"meta": snapshot.meta.model_copy(update={"as_of": snapshot.as_of, "missing_fields": missing_fields, "warnings": sorted(set(snapshot.quality_flags))})}).model_dump(),
        "features": best_effort_features,
        "regime": regime,
        "llm_hint": llm_hint,
        "state_machine": state,
        "retreat": retreat,
        "quality_flags": quality_flags,
        "missing_fields": missing_fields,
        **v2,
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
    state_mode: Literal["persistent", "stateless"] = "persistent",
    persist_state: bool | None = None,
) -> dict:
    _ = force_refresh
    service_meta: dict | None = None
    fallback_flags: list[str] = []
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
            # 优先走 Stage-1 特征服务（带质量门控元数据）；失败时回退旧 builder 路径并打标
            try:
                service_result = MarketFeatureService().get_market_features(as_of=as_of)
                snapshot_data = dict(service_result.get("data") or {})
                service_meta = dict(service_result.get("meta") or {})
                if not snapshot_data:
                    raise ValueError("empty feature payload")
            except Exception:  # noqa: BLE001 - 服务失败回退到 builder，不阻断
                snapshot_data = MarketFeatureBuilder().build(as_of=as_of, force_refresh=force_refresh)
                service_meta = None
                fallback_flags.append(REGIME_FEATURE_SERVICE_FALLBACK)
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
        result = _unknown_result(
            snapshot_obj,
            previous_regime,
            missing_fields + extra_missing + retreat_missing + quality_blockers,
            top_theme_strength=top_theme_strength,
            index_drawdown_20d=index_drawdown_20d,
            service_meta=service_meta,
        )
        if fallback_flags:
            result["quality_flags"] = sorted(set(result["quality_flags"]) | set(fallback_flags))
        return result

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
    if persist_state is not None:
        state_mode = "persistent" if persist_state else "stateless"
    if state_mode == "persistent":
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
    snapshot_for_output = snapshot_obj.model_copy(
        update={
            "meta": snapshot_obj.meta.model_copy(
                update={"as_of": snapshot_obj.as_of, "confidence": regime.get("confidence"), "warnings": snapshot_obj.quality_flags}
            )
        }
    )
    v2 = _regime_v2_output(
        features,
        llm_hint,
        state,
        service_meta=service_meta,
        legacy_quality_blockers=[],
    )
    quality_flags = sorted(set(snapshot_obj.quality_flags) | set(fallback_flags) | set(v2.pop("regime_v2_quality_flags")))
    return {
        "snapshot": snapshot_for_output.model_dump(),
        "features": features,
        "regime": regime,
        "llm_hint": llm_hint,
        "state_machine": state,
        "retreat": retreat,
        "quality_flags": quality_flags,
        "missing_fields": [],
        **v2,
    }


def get_high_position_retreat() -> dict:
    return detect_high_position_retreat(0.4, 0.25, 0.3, 3)


def get_market_regime_history(market_code: str = "CN_A", start_date: str | None = None, end_date: str | None = None, limit: int = 100) -> dict:
    from datetime import date
    from storage.bootstrap import create_all
    from storage.repositories.research_repository import MarketRegimeRepository

    create_all()
    rows = MarketRegimeRepository().list_history(
        market_code, limit=limit,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
    )
    return {"market_code": market_code, "history": [{"regime": row.new_regime, "previous_regime": row.previous_regime, "start_date": row.started_at.isoformat(), "end_date": row.ended_at.isoformat() if row.ended_at else None, "confidence": row.confidence, "evidence": row.evidence} for row in rows]}
