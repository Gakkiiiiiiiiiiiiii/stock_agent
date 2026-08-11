"""Regime × 策略历史校准：统计各市场状态下各策略路由的历史超额收益表现。

数据源：investment_decision（market_regime 列 + skill_slug / thesis / tool_trace 中的
策略标识）联表 investment_decision_outcome（horizon_days, market_excess_return）。
输出按 (regime, strategy_key, horizon) 分组的 count / mean / median / hit_rate。

本模块纯统计、只读；路由仍保持规则驱动（"逐渐"过渡的第一阶段），
统计结果仅作为 route_strategy 的附加信息暴露。
"""
from __future__ import annotations

from statistics import mean, median
from typing import Any

from sqlalchemy import select

DEFAULT_HORIZONS = (1, 5, 20)
UNKNOWN_STRATEGY = "UNKNOWN"


def extract_strategy_key(
    skill_slug: str | None = None,
    thesis: dict | None = None,
    tool_trace: list | None = None,
    themes: list | None = None,
) -> str:
    """从 decision 记录中提取策略标识。

    优先级：skill_slug → thesis.strategy_key/strategy/strategy_code →
    thesis.route.preferred_strategies 首个 → tool_trace 中的 strategy 字段 →
    首个 theme（theme: 前缀）→ "UNKNOWN"。
    """
    if skill_slug:
        return str(skill_slug)
    thesis = thesis or {}
    for key in ("strategy_key", "strategy", "strategy_code"):
        if thesis.get(key):
            return str(thesis[key])
    route = thesis.get("route") or {}
    if isinstance(route, dict):
        preferred = route.get("preferred_strategies")
        if isinstance(preferred, dict) and preferred:
            return str(next(iter(preferred)))
        if isinstance(preferred, list) and preferred:
            return str(preferred[0])
    for item in tool_trace or []:
        if not isinstance(item, dict):
            continue
        for key in ("strategy_key", "strategy"):
            if item.get(key):
                return str(item[key])
        payload = item.get("output") or item.get("result")
        if isinstance(payload, dict):
            preferred = payload.get("preferred_strategies")
            if isinstance(preferred, dict) and preferred:
                return str(next(iter(preferred)))
    if themes:
        return f"theme:{themes[0]}"
    return UNKNOWN_STRATEGY


def _rows_from_session(session: Any) -> list[dict]:
    from storage.models.research import InvestmentDecision, InvestmentDecisionOutcome

    pairs = session.execute(
        select(InvestmentDecision, InvestmentDecisionOutcome)
        .join(InvestmentDecisionOutcome, InvestmentDecisionOutcome.decision_id == InvestmentDecision.id)
        .where(InvestmentDecision.market_regime.is_not(None))
        .where(InvestmentDecisionOutcome.market_excess_return.is_not(None))
    ).all()
    return [
        {
            "decision_id": decision.id,
            "market_regime": decision.market_regime,
            "skill_slug": decision.skill_slug,
            "thesis": dict(decision.thesis or {}),
            "tool_trace": list(decision.tool_trace or []),
            "themes": list(decision.themes or []),
            "horizon_days": outcome.horizon_days,
            "market_excess_return": outcome.market_excess_return,
            "evaluation_date": outcome.evaluation_date.isoformat() if outcome.evaluation_date else None,
        }
        for decision, outcome in pairs
    ]


def _load_rows(session_or_repo: Any = None) -> list[dict]:
    if session_or_repo is None:
        from storage.repositories.research_repository import DecisionRepository

        return DecisionRepository().list_decision_outcome_rows()
    fetcher = getattr(session_or_repo, "list_decision_outcome_rows", None)
    if callable(fetcher):
        return list(fetcher())
    if hasattr(session_or_repo, "execute"):
        return _rows_from_session(session_or_repo)
    raise TypeError(f"unsupported session_or_repo: {type(session_or_repo)!r}")


def compute_regime_strategy_stats(session_or_repo: Any = None, horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[dict]:
    """按 (market_regime, strategy_key, horizon_days) 聚合历史超额收益统计。

    返回 list[dict]，每行包含:
        market_regime, strategy_key, horizon_days, sample_size,
        mean_excess_return, median_excess_return, hit_rate
    （hit_rate = market_excess_return > 0 的占比；统计口径为 market_excess_return 非空的 outcome 行）
    """
    rows = _load_rows(session_or_repo)
    horizon_filter = set(horizons) if horizons else None
    groups: dict[tuple[str, str, int], list[float]] = {}
    for row in rows:
        horizon = row.get("horizon_days")
        excess = row.get("market_excess_return")
        regime = row.get("market_regime")
        if horizon is None or excess is None or not regime:
            continue
        if horizon_filter is not None and int(horizon) not in horizon_filter:
            continue
        strategy_key = extract_strategy_key(
            skill_slug=row.get("skill_slug"),
            thesis=row.get("thesis"),
            tool_trace=row.get("tool_trace"),
            themes=row.get("themes"),
        )
        groups.setdefault((str(regime), strategy_key, int(horizon)), []).append(float(excess))

    stats: list[dict] = []
    for (regime, strategy_key, horizon), values in sorted(groups.items()):
        stats.append(
            {
                "market_regime": regime,
                "strategy_key": strategy_key,
                "horizon_days": horizon,
                "sample_size": len(values),
                "mean_excess_return": round(mean(values), 6),
                "median_excess_return": round(median(values), 6),
                "hit_rate": round(sum(1 for value in values if value > 0) / len(values), 4),
            }
        )
    return stats


def summarize_for_route(stats: list[dict], market_regime: str, strategy_keys: list[str], min_samples: int = 5) -> dict[str, dict]:
    """面向 route_strategy 的聚合视图：仅保留样本数达标 (>= min_samples) 的策略。

    返回 {strategy_key: {"sample_size", "historical_hit_rate", "mean_excess_return", "by_horizon"}}；
    sample_size 取各 horizon 的最大样本数（= 决策条数），hit_rate/均值按样本数加权。
    """
    result: dict[str, dict] = {}
    for strategy in strategy_keys:
        rows = [
            row for row in stats
            if row["market_regime"] == market_regime and row["strategy_key"] == strategy
        ]
        if not rows:
            continue
        sample_size = max(row["sample_size"] for row in rows)
        if sample_size < min_samples:
            continue
        total = sum(row["sample_size"] for row in rows)
        hit_rate = sum(row["hit_rate"] * row["sample_size"] for row in rows) / total
        mean_excess = sum(row["mean_excess_return"] * row["sample_size"] for row in rows) / total
        result[strategy] = {
            "sample_size": sample_size,
            "historical_hit_rate": round(hit_rate, 4),
            "mean_excess_return": round(mean_excess, 6),
            "by_horizon": {
                str(row["horizon_days"]): {
                    "sample_size": row["sample_size"],
                    "hit_rate": row["hit_rate"],
                    "mean_excess_return": row["mean_excess_return"],
                }
                for row in rows
            },
        }
    return result
