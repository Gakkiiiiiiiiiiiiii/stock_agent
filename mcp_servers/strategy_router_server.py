from __future__ import annotations

import logging

from engines.strategy.signal_score_engine import score_signal
from engines.strategy.strategy_router import route_strategies
from engines.technical.signal_adjuster import adjust_signal_score

logger = logging.getLogger(__name__)

_CALIBRATION_DEFAULTS = {"enabled": True, "min_samples": 5, "horizons": (1, 5, 20)}


def _calibration_config() -> dict:
    from financial_agent.config import load_yaml_config

    try:
        data = load_yaml_config("market_regime_thresholds.yaml")
    except FileNotFoundError:
        return dict(_CALIBRATION_DEFAULTS)
    config = {**_CALIBRATION_DEFAULTS, **dict(data.get("calibration") or {})}
    config["horizons"] = tuple(int(h) for h in (config.get("horizons") or _CALIBRATION_DEFAULTS["horizons"]))
    return config


def _attach_calibration(route: dict) -> dict:
    """在规则路由结果上附加历史校准统计；数据不足或查询失败时保持原样。"""
    config = _calibration_config()
    if not config.get("enabled", True):
        return route
    strategies = list((route.get("preferred_strategies") or {}).keys())
    if not strategies:
        return route
    try:
        from engines.regime.calibration import compute_regime_strategy_stats, summarize_for_route
        from storage.bootstrap import create_all

        create_all()
        stats = compute_regime_strategy_stats(horizons=config["horizons"])
        summary = summarize_for_route(stats, route["market_regime"], strategies, min_samples=int(config["min_samples"]))
    except Exception:  # noqa: BLE001 - 校准是附加信息，绝不阻断路由
        logger.warning("regime calibration unavailable", exc_info=True)
        return route
    if summary:
        route = dict(route)
        route["calibration"] = {"min_samples": int(config["min_samples"]), "strategies": summary}
    return route


def route_strategy(market_regime: str) -> dict:
    return _attach_calibration(route_strategies(market_regime))


def adjust_signal(pattern: str, raw_signal_score: float, market_regime: str, theme_strength: float = 50, liquidity_ok: bool = True) -> dict:
    route = route_strategies(market_regime)
    scored = score_signal(pattern=pattern, base_score=raw_signal_score, route=route)
    adjusted = adjust_signal_score(scored, market_regime=market_regime, theme_strength=theme_strength, liquidity_ok=liquidity_ok)
    return {"route": route, "signal": adjusted, "pattern": pattern}
