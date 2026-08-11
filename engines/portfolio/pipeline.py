"""组合构建流水线 v2（设计文档 P0-04 组合构建引擎）。

阶段：候选 → 资格过滤（复用 engines/opportunity/eligibility）→ 打分
→ regime 风险预算 → 目标仓位档 → 暴露约束 → 换手控制 → 组合动作。

全部确定性纯逻辑，无 LLM。所有"为什么"（为什么买/不买/这个仓位/保留/减仓）
都可从 reason_codes + 结构化字段回答。

reason code 词表：
  分档/建仓   SCORE_BAND_STARTER / SCORE_BAND_NORMAL / SCORE_BAND_HIGH_CONVICTION /
              WATCH_BELOW_STARTER_THRESHOLD / SINGLE_STOCK_CAP
  持仓处理   HOLD_WITHIN_BAND / HOLD_NO_SCORE / TRIM_TO_BAND_RANGE /
              SCORE_BELOW_REDUCE_THRESHOLD / SCORE_BELOW_EXIT_THRESHOLD
  约束裁剪   SINGLE_STOCK_CAP / THEME_CAP_TRIM / SECTOR_CAP_TRIM /
              CLUSTER_CAP_TRIM / RISK_BUDGET_SCALED
  换手控制   DELTA_BELOW_MIN_REBALANCE / TURNOVER_CAP_TRIMMED
  拒因       见 engines/opportunity/eligibility.py（QUOTE_MISSING 等）
"""
from __future__ import annotations

import copy

from financial_agent.config import load_yaml_config
from engines.opportunity.candidate import OpportunityCandidate
from engines.opportunity.eligibility import evaluate_eligibility
from engines.opportunity.scorer import load_opportunity_config, score_candidate

RULES_CONFIG_FILE = "portfolio_rules.yaml"
DEFAULT_RULES_VERSION = "portfolio_rules_v2"

#: 暴露约束组 → (配置键, reason code)
_GROUP_CAPS: tuple[tuple[str, str, str], ...] = (
    ("theme", "max_theme_weight", "THEME_CAP_TRIM"),
    ("sector", "max_sector_weight", "SECTOR_CAP_TRIM"),
    ("cluster", "max_correlated_cluster_weight", "CLUSTER_CAP_TRIM"),
)


def load_portfolio_rules() -> dict:
    """读取 config/portfolio_rules.yaml 的 portfolio_rules 段。"""
    try:
        data = load_yaml_config(RULES_CONFIG_FILE)
    except FileNotFoundError:
        data = {}
    return dict(data.get("portfolio_rules") or {})


def _band_for_score(score: float, thresholds: dict) -> str:
    if score >= float(thresholds["high_conviction"]):
        return "high_conviction"
    if score >= float(thresholds["normal"]):
        return "normal"
    if score >= float(thresholds["starter"]):
        return "starter"
    return "watch"


def _band_bounds(band: str, sizing_bands: dict) -> tuple[float, float]:
    spec = sizing_bands[band]
    if isinstance(spec, (int, float)):
        value = float(spec)
        return value, value
    return float(spec[0]), float(spec[1])


def _band_target(band: str, sizing_bands: dict) -> float:
    lo, hi = _band_bounds(band, sizing_bands)
    return round((lo + hi) / 2.0, 4)


def _resolve_cluster(symbol: str, theme: str | None, context: dict) -> str | None:
    """相关簇键：优先 symbol 级 cluster_map，其次 theme→cluster 映射（如 AI算力→光模块）。"""
    cluster_map = context.get("cluster_map") or {}
    if symbol in cluster_map:
        return cluster_map[symbol]
    theme_cluster_map = context.get("theme_cluster_map") or {}
    if theme and theme in theme_cluster_map:
        return theme_cluster_map[theme]
    return None


def run_portfolio_pipeline(
    candidates: list[dict],
    positions: list[dict],
    context: dict | None = None,
    rules: dict | None = None,
) -> dict:
    """执行组合构建流水线，返回 {actions, summary}。

    candidates: [{symbol, theme?, sector?, opportunity_score?, confidence?, 成分分?, ...}]
    positions:  [{symbol, theme?, sector?, weight, market_value?}]
    context:    {regime?, as_of?, symbols? (资格过滤上下文), position_scores?,
                 cluster_map?, theme_cluster_map?, opportunity_config?}
    rules:      覆盖 config/portfolio_rules.yaml 的规则 dict（测试/适配器用）。
    """
    ctx = context or {}
    active_rules = copy.deepcopy(rules) if rules is not None else load_portfolio_rules()
    sizing_bands = active_rules["sizing_bands"]
    thresholds = active_rules["score_thresholds"]
    exposure_cfg = active_rules["exposure"]
    turnover_cfg = active_rules["turnover"]
    reduce_factor = float(active_rules.get("reduce_factor", 0.5))
    max_single = float(exposure_cfg["max_single_stock"])

    budgets = active_rules["regime_risk_budget"]
    regime = ctx.get("regime") or "default"
    regime_budget = float(
        (budgets.get(regime) or budgets.get("default") or {"max_total_position": 1.0})["max_total_position"]
    )

    # ---- 阶段 1：资格过滤（复用机会层硬过滤） -------------------------------
    opportunity_cfg = ctx.get("opportunity_config") or load_opportunity_config()
    eligibility_cfg = opportunity_cfg.get("eligibility") or {}
    min_liquidity = float(eligibility_cfg.get("min_liquidity_score", 20.0))
    min_coverage = float(eligibility_cfg.get("min_data_coverage", 0.60))
    symbol_contexts = ctx.get("symbols") or {}

    # ---- 阶段 2：打分（优先使用已提供的 opportunity_score） -----------------
    scored_candidates: dict[str, dict] = {}
    rejected: list[dict] = []
    for raw in candidates:
        candidate = OpportunityCandidate.model_validate(raw)
        verdict = evaluate_eligibility(
            candidate,
            symbol_contexts.get(candidate.symbol) or {},
            min_liquidity_score=min_liquidity,
            min_data_coverage=min_coverage,
        )
        if not verdict["eligible"]:
            rejected.append(verdict)
            continue
        if raw.get("opportunity_score") is not None:
            score = round(float(raw["opportunity_score"]), 4)
        else:
            score = score_candidate(candidate, opportunity_cfg)["opportunity_score"]
        scored_candidates[candidate.symbol] = {
            "symbol": candidate.symbol,
            "theme": candidate.theme,
            "sector": candidate.sector,
            "score": score,
            "confidence": candidate.confidence,
        }
    rejected.sort(key=lambda item: item["symbol"])

    # ---- 阶段 3：初始目标仓位（仓位档 + 持仓 reduce/exit/hold） -------------
    current_positions: dict[str, dict] = {}
    for position in positions:
        symbol = position["symbol"]
        current_positions[symbol] = {
            "symbol": symbol,
            "theme": position.get("theme"),
            "sector": position.get("sector"),
            "current_weight": round(float(position.get("weight", position.get("market_value", 0)) or 0), 4),
        }

    position_scores = ctx.get("position_scores") or {}
    symbols = sorted(set(scored_candidates) | set(current_positions))
    states: dict[str, dict] = {}
    for symbol in symbols:
        current = current_positions.get(symbol, {})
        candidate = scored_candidates.get(symbol, {})
        current_weight = float(current.get("current_weight", 0.0))
        theme = candidate.get("theme") or current.get("theme")
        sector = candidate.get("sector") or current.get("sector")
        score = candidate.get("score")
        if score is None and symbol in position_scores:
            score = round(float(position_scores[symbol]), 4)
        state = {
            "symbol": symbol,
            "theme": theme,
            "sector": sector,
            "cluster": _resolve_cluster(symbol, theme, ctx),
            "score": score,
            "current_weight": current_weight,
            "target_weight": current_weight,
            "band": "watch",
            "reason_codes": [],
        }
        if score is None:
            # 无分数的持仓：不动作，保留现状
            state["band"] = "hold"
            state["reason_codes"].append("HOLD_NO_SCORE")
        elif score < float(thresholds["exit_below"]) and current_weight > 0:
            state["target_weight"] = 0.0
            state["reason_codes"].append("SCORE_BELOW_EXIT_THRESHOLD")
        elif score < float(thresholds["reduce_below"]) and current_weight > 0:
            state["target_weight"] = round(current_weight * reduce_factor, 4)
            state["reason_codes"].append("SCORE_BELOW_REDUCE_THRESHOLD")
        else:
            band = _band_for_score(score, thresholds)
            state["band"] = band
            band_lo, band_hi = _band_bounds(band, sizing_bands)
            if band == "watch":
                if current_weight <= 0:
                    state["target_weight"] = 0.0
                    state["reason_codes"].append("WATCH_BELOW_STARTER_THRESHOLD")
                else:
                    # 持仓分数低于建仓线但高于 reduce 线：不加仓，保留现状
                    state["target_weight"] = current_weight
                    state["reason_codes"].append("HOLD_WITHIN_BAND")
            elif current_weight > band_hi:
                # 当前仓位高于档位上沿：降到档位上沿
                state["target_weight"] = round(band_hi, 4)
                state["reason_codes"].append("TRIM_TO_BAND_RANGE")
            elif current_weight >= band_lo:
                # 当前仓位已落在档位区间内：不补仓，保留现状
                state["reason_codes"].append("HOLD_WITHIN_BAND")
            else:
                # 当前仓位低于档位下沿（含新建仓）：补到档位中值，受个股上限约束
                target = _band_target(band, sizing_bands)
                state["reason_codes"].append(f"SCORE_BAND_{band.upper()}")
                if target > max_single:
                    target = max_single
                    state["reason_codes"].append("SINGLE_STOCK_CAP")
                state["target_weight"] = round(target, 4)
        states[symbol] = state

    # ---- 阶段 4：暴露约束（theme / sector / cluster，低分先裁） -------------
    for group_field, cap_key, reason_code in _GROUP_CAPS:
        cap = float(exposure_cfg[cap_key])
        groups: dict[str, list[dict]] = {}
        for state in states.values():
            key = state.get(group_field)
            if key:
                groups.setdefault(str(key), []).append(state)
        for key in sorted(groups):
            members = groups[key]
            group_total = sum(item["target_weight"] for item in members)
            if group_total <= cap:
                continue
            # 组内按 (score asc, symbol asc) 裁剪：最低分先降，降到组内不超 cap
            for member in sorted(members, key=lambda item: (item["score"] if item["score"] is not None else -1.0, item["symbol"])):
                if group_total <= cap:
                    break
                if member["target_weight"] <= 0:
                    continue
                excess = round(group_total - cap, 4)
                cut = min(member["target_weight"], excess)
                member["target_weight"] = round(member["target_weight"] - cut, 4)
                group_total = round(group_total - cut, 4)
                if reason_code not in member["reason_codes"]:
                    member["reason_codes"].append(reason_code)

    # ---- 阶段 5：regime 风险预算（总目标仓位 ≤ regime 上限，低分先压） ------
    total_target = round(sum(item["target_weight"] for item in states.values()), 4)
    if total_target > regime_budget:
        for state in sorted(
            states.values(),
            key=lambda item: (item["score"] if item["score"] is not None else -1.0, item["symbol"]),
        ):
            if total_target <= regime_budget:
                break
            if state["target_weight"] <= 0:
                continue
            excess = round(total_target - regime_budget, 4)
            cut = min(state["target_weight"], excess)
            state["target_weight"] = round(state["target_weight"] - cut, 4)
            total_target = round(total_target - cut, 4)
            if "RISK_BUDGET_SCALED" not in state["reason_codes"]:
                state["reason_codes"].append("RISK_BUDGET_SCALED")

    # ---- 阶段 6：换手控制 ---------------------------------------------------
    min_delta = float(turnover_cfg["min_rebalance_delta"])
    max_turnover = float(turnover_cfg["max_daily_turnover"])
    for state in states.values():
        delta = round(state["target_weight"] - state["current_weight"], 4)
        if 0 < abs(delta) < min_delta:
            state["target_weight"] = state["current_weight"]
            state["reason_codes"].append("DELTA_BELOW_MIN_REBALANCE")
    turnover = round(
        sum(abs(item["target_weight"] - item["current_weight"]) for item in states.values()), 4
    )
    if turnover > max_turnover:
        actionable = [
            item
            for item in states.values()
            if abs(item["target_weight"] - item["current_weight"]) > 0
        ]
        # 最小 delta 的动作先被取消（转为 hold），直到总换手不超上限
        for state in sorted(actionable, key=lambda item: (abs(item["target_weight"] - item["current_weight"]), item["symbol"])):
            if turnover <= max_turnover:
                break
            delta = round(abs(state["target_weight"] - state["current_weight"]), 4)
            state["target_weight"] = state["current_weight"]
            turnover = round(turnover - delta, 4)
            state["reason_codes"].append("TURNOVER_CAP_TRIMMED")

    # ---- 输出组装 -----------------------------------------------------------
    actions = []
    for symbol in symbols:
        state = states[symbol]
        current_weight = state["current_weight"]
        target_weight = round(state["target_weight"], 4)
        delta = round(target_weight - current_weight, 4)
        band = state["band"]
        if target_weight > current_weight:
            action = band if band in {"starter", "normal", "high_conviction"} else "normal"
        elif target_weight < current_weight:
            action = "exit" if target_weight == 0 else "reduce"
        elif current_weight > 0:
            action = "hold"
        else:
            action = "watch"
        actions.append(
            {
                "symbol": symbol,
                "action": action,
                "target_weight": target_weight,
                "current_weight": current_weight,
                "delta_weight": delta,
                "reason_codes": list(state["reason_codes"]),
                "band": band,
                "score": state["score"],
                "theme": state["theme"],
                "sector": state["sector"],
            }
        )

    def _exposure(field: str) -> dict[str, float]:
        totals: dict[str, float] = {}
        for state in states.values():
            key = state.get(field)
            if key and state["target_weight"] > 0:
                totals[str(key)] = round(totals.get(str(key), 0.0) + state["target_weight"], 4)
        return dict(sorted(totals.items()))

    summary = {
        "total_target_weight": round(sum(item["target_weight"] for item in states.values()), 4),
        "total_current_weight": round(sum(item["current_weight"] for item in states.values()), 4),
        "regime": regime,
        "regime_budget": regime_budget,
        "turnover": round(
            sum(abs(item["target_weight"] - item["current_weight"]) for item in states.values()), 4
        ),
        "theme_exposure": _exposure("theme"),
        "sector_exposure": _exposure("sector"),
        "cluster_exposure": _exposure("cluster"),
        "rejected": rejected,
        "rules_version": active_rules.get("version", DEFAULT_RULES_VERSION),
        "as_of": ctx.get("as_of"),
    }
    return {"actions": actions, "summary": summary}
