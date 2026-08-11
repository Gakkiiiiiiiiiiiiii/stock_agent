"""engines/portfolio/pipeline.py v2 流水线测试。

覆盖：regime 预算、仓位档、暴露约束（theme/sector/cluster）、换手控制、
拒因透传、各动作的 reason_codes、确定性。
"""
from __future__ import annotations

import copy

from engines.portfolio.pipeline import run_portfolio_pipeline

RULES = {
    "version": "portfolio_rules_v2",
    "sizing_bands": {
        "watch": 0.0,
        "starter": [0.01, 0.03],
        "normal": [0.03, 0.06],
        "high_conviction": [0.06, 0.10],
    },
    "score_thresholds": {"starter": 60, "normal": 72, "high_conviction": 85, "reduce_below": 45, "exit_below": 30},
    "reduce_factor": 0.5,
    "regime_risk_budget": {
        "crowding_market": {"max_total_position": 0.90},
        "rotation_market": {"max_total_position": 0.75},
        "range_market": {"max_total_position": 0.60},
        "high_position_retreat": {"max_total_position": 0.35},
        "downtrend_market": {"max_total_position": 0.20},
        "default": {"max_total_position": 0.50},
    },
    "exposure": {
        "max_single_stock": 0.10,
        "max_theme_weight": 0.40,
        "max_sector_weight": 0.50,
        "max_correlated_cluster_weight": 0.50,
    },
    "turnover": {"min_rebalance_delta": 0.01, "max_daily_turnover": 0.30},
}


def _run(candidates, positions=None, context=None, rules=None):
    return run_portfolio_pipeline(
        candidates=candidates,
        positions=positions or [],
        context=context or {"regime": "crowding_market"},
        rules=copy.deepcopy(rules or RULES),
    )


def _action(result, symbol):
    return next(item for item in result["actions"] if item["symbol"] == symbol)


# ---- regime 风险预算 ----------------------------------------------------------


def test_downtrend_regime_caps_total_position_at_budget():
    candidates = [
        {"symbol": f"S{i}", "opportunity_score": 90, "theme": f"T{i}", "sector": f"SEC{i}"}
        for i in range(6)
    ]
    result = _run(candidates, context={"regime": "downtrend_market"})
    assert result["summary"]["regime_budget"] == 0.20
    assert result["summary"]["total_target_weight"] <= 0.20 + 1e-9
    # 超预算时最低分先被压：所有 90 分并列，按 symbol 顺序裁到预算内
    scaled = [item for item in result["actions"] if "RISK_BUDGET_SCALED" in item["reason_codes"]]
    assert scaled


def test_unknown_regime_falls_back_to_default_budget():
    result = _run([{"symbol": "A", "opportunity_score": 90}], context={"regime": "not_a_regime"})
    assert result["summary"]["regime_budget"] == 0.50


# ---- 仓位档 ------------------------------------------------------------------


def test_sizing_bands_by_score():
    result = _run(
        [
            {"symbol": "HIGH", "opportunity_score": 90},
            {"symbol": "MID", "opportunity_score": 75},
            {"symbol": "LOW", "opportunity_score": 62},
            {"symbol": "WATCH", "opportunity_score": 50},
        ]
    )
    high = _action(result, "HIGH")
    assert high["action"] == "high_conviction"
    assert high["band"] == "high_conviction"
    assert 0.06 <= high["target_weight"] <= 0.10
    assert "SCORE_BAND_HIGH_CONVICTION" in high["reason_codes"]

    mid = _action(result, "MID")
    assert mid["action"] == "normal"
    assert 0.03 <= mid["target_weight"] <= 0.06

    low = _action(result, "LOW")
    assert low["action"] == "starter"
    assert 0.01 <= low["target_weight"] <= 0.03

    watch = _action(result, "WATCH")
    assert watch["action"] == "watch"
    assert watch["target_weight"] == 0.0
    assert "WATCH_BELOW_STARTER_THRESHOLD" in watch["reason_codes"]


def test_band_target_capped_by_max_single_stock():
    rules = copy.deepcopy(RULES)
    rules["exposure"]["max_single_stock"] = 0.07
    result = _run([{"symbol": "A", "opportunity_score": 95}], rules=rules)
    action = _action(result, "A")
    assert action["target_weight"] <= 0.07
    assert "SINGLE_STOCK_CAP" in action["reason_codes"]


# ---- 持仓：reduce / exit / hold ----------------------------------------------


def test_existing_position_reduce_exit_hold_reason_codes():
    result = _run(
        candidates=[
            {"symbol": "EXIT_ME", "opportunity_score": 20},
            {"symbol": "REDUCE_ME", "opportunity_score": 40},
            {"symbol": "KEEP_ME", "opportunity_score": 75},
        ],
        positions=[
            {"symbol": "EXIT_ME", "weight": 0.08},
            {"symbol": "REDUCE_ME", "weight": 0.08},
            {"symbol": "KEEP_ME", "weight": 0.04},
        ],
    )
    exit_action = _action(result, "EXIT_ME")
    assert exit_action["action"] == "exit"
    assert exit_action["target_weight"] == 0.0
    assert "SCORE_BELOW_EXIT_THRESHOLD" in exit_action["reason_codes"]

    reduce_action = _action(result, "REDUCE_ME")
    assert reduce_action["action"] == "reduce"
    assert reduce_action["target_weight"] == 0.04
    assert "SCORE_BELOW_REDUCE_THRESHOLD" in reduce_action["reason_codes"]

    keep = _action(result, "KEEP_ME")
    assert keep["action"] == "hold"
    assert keep["target_weight"] == 0.04
    assert "HOLD_WITHIN_BAND" in keep["reason_codes"]


def test_position_without_score_is_held():
    result = _run(candidates=[], positions=[{"symbol": "OLD", "weight": 0.10}])
    action = _action(result, "OLD")
    assert action["action"] == "hold"
    assert action["target_weight"] == 0.10
    assert "HOLD_NO_SCORE" in action["reason_codes"]


# ---- 暴露约束 -----------------------------------------------------------------


def test_theme_exposure_cap_trims_lowest_score_first():
    result = _run(
        [
            {"symbol": "A", "opportunity_score": 90, "theme": "AI"},
            {"symbol": "B", "opportunity_score": 88, "theme": "AI"},
            {"symbol": "C", "opportunity_score": 86, "theme": "AI"},
            {"symbol": "D", "opportunity_score": 84, "theme": "AI"},
            {"symbol": "E", "opportunity_score": 82, "theme": "AI"},
        ]
    )
    # 每只 high_conviction 中值 0.08，5 只共 0.40 不超；6 只才超，改规则缩小 cap
    assert result["summary"]["theme_exposure"]["AI"] <= 0.40 + 1e-9

    rules = copy.deepcopy(RULES)
    rules["exposure"]["max_theme_weight"] = 0.20
    result2 = _run(
        [
            {"symbol": "A", "opportunity_score": 90, "theme": "AI"},
            {"symbol": "B", "opportunity_score": 88, "theme": "AI"},
            {"symbol": "C", "opportunity_score": 86, "theme": "AI"},
        ],
        rules=rules,
    )
    # 3 只 high_conviction 中值各 0.08 共 0.24，超 cap 0.20 → 最低分 C 先被裁
    assert result2["summary"]["theme_exposure"]["AI"] <= 0.20 + 1e-9
    trimmed = [item["symbol"] for item in result2["actions"] if "THEME_CAP_TRIM" in item["reason_codes"]]
    assert trimmed
    assert "C" in trimmed


def test_sector_exposure_cap():
    rules = copy.deepcopy(RULES)
    rules["exposure"]["max_sector_weight"] = 0.10
    result = _run(
        [
            {"symbol": "A", "opportunity_score": 90, "sector": "TMT", "theme": "AI"},
            {"symbol": "B", "opportunity_score": 88, "sector": "TMT", "theme": "半导体"},
            {"symbol": "C", "opportunity_score": 86, "sector": "TMT", "theme": "软件"},
        ],
        rules=rules,
    )
    assert result["summary"]["sector_exposure"]["TMT"] <= 0.10 + 1e-9
    assert any("SECTOR_CAP_TRIM" in item["reason_codes"] for item in result["actions"])


def test_cluster_exposure_cap_via_theme_cluster_map():
    rules = copy.deepcopy(RULES)
    rules["exposure"]["max_correlated_cluster_weight"] = 0.10
    result = _run(
        candidates=[
            {"symbol": "A", "opportunity_score": 90, "theme": "AI算力"},
            {"symbol": "B", "opportunity_score": 88, "theme": "光通信"},
            {"symbol": "C", "opportunity_score": 86, "theme": "其他"},
        ],
        context={"regime": "crowding_market", "theme_cluster_map": {"AI算力": "光模块", "光通信": "光模块"}},
        rules=rules,
    )
    assert result["summary"]["cluster_exposure"]["光模块"] <= 0.10 + 1e-9
    assert any("CLUSTER_CAP_TRIM" in item["reason_codes"] for item in result["actions"])


# ---- 换手控制 -----------------------------------------------------------------


def test_min_rebalance_delta_turns_into_hold():
    # exit 目标 0，但 |delta| = 0.005 < min_rebalance_delta 0.01 → 不动作
    result = _run(
        candidates=[{"symbol": "A", "opportunity_score": 20}],
        positions=[{"symbol": "A", "weight": 0.005}],
    )
    action = _action(result, "A")
    assert action["action"] == "hold"
    assert action["delta_weight"] == 0.0
    assert "SCORE_BELOW_EXIT_THRESHOLD" in action["reason_codes"]
    assert "DELTA_BELOW_MIN_REBALANCE" in action["reason_codes"]


def test_daily_turnover_cap_trims_smallest_delta_first():
    rules = copy.deepcopy(RULES)
    rules["turnover"]["max_daily_turnover"] = 0.10
    result = _run(
        [
            {"symbol": "BIG", "opportunity_score": 95},   # 0.08
            {"symbol": "MID", "opportunity_score": 75},   # 0.045
            {"symbol": "SMALL", "opportunity_score": 62}, # 0.02
        ],
        rules=rules,
    )
    assert result["summary"]["turnover"] <= 0.10 + 1e-9
    small = _action(result, "SMALL")
    assert small["action"] == "hold" or small["action"] == "watch"
    assert "TURNOVER_CAP_TRIMMED" in small["reason_codes"]
    big = _action(result, "BIG")
    assert big["delta_weight"] > 0  # 最大 delta 保留


# ---- 拒因透传 -----------------------------------------------------------------


def test_rejected_candidates_surface_in_summary():
    result = _run(
        candidates=[
            {"symbol": "OK", "opportunity_score": 90},
            {"symbol": "SUSP", "opportunity_score": 95},
        ],
        context={"regime": "crowding_market", "symbols": {"SUSP": {"suspended": True}}},
    )
    assert result["summary"]["rejected"] == [
        {"symbol": "SUSP", "eligible": False, "reject_reasons": ["SUSPENDED"]}
    ]
    assert all(item["symbol"] != "SUSP" for item in result["actions"])


def test_missing_opportunity_score_scored_from_components():
    result = _run(
        candidates=[{"symbol": "A", "theme_score": 95, "technical_score": 90, "factor_score": 85, "regime_fit_score": 80, "risk_score": 10}],
    )
    action = _action(result, "A")
    assert action["score"] is not None
    assert action["score"] > 60


# ---- 确定性 -------------------------------------------------------------------


def test_pipeline_determinism_same_input_twice():
    candidates = [
        {"symbol": "A", "opportunity_score": 90, "theme": "AI", "sector": "TMT"},
        {"symbol": "B", "opportunity_score": 75, "theme": "黄金", "sector": "有色"},
        {"symbol": "C", "opportunity_score": 62, "theme": "AI", "sector": "TMT"},
    ]
    positions = [{"symbol": "OLD", "weight": 0.10, "theme": "黄金", "sector": "有色"}]
    context = {"regime": "range_market", "as_of": "2026-08-10"}
    first = _run(candidates, positions, context)
    second = _run(candidates, positions, context)
    assert first == second


def test_output_shape_summary_fields():
    result = _run([{"symbol": "A", "opportunity_score": 90, "theme": "AI", "sector": "TMT"}])
    summary = result["summary"]
    for key in (
        "total_target_weight",
        "regime",
        "regime_budget",
        "turnover",
        "theme_exposure",
        "sector_exposure",
        "cluster_exposure",
        "rejected",
        "rules_version",
    ):
        assert key in summary
    assert summary["rules_version"] == "portfolio_rules_v2"
    action = result["actions"][0]
    for key in ("symbol", "action", "target_weight", "current_weight", "delta_weight", "reason_codes", "band"):
        assert key in action
