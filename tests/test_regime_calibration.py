"""Regime × 策略历史校准：统计正确性与 route_strategy 附加行为。"""
from __future__ import annotations

from datetime import date

import pytest

from engines.regime.calibration import (
    compute_regime_strategy_stats,
    extract_strategy_key,
    summarize_for_route,
)
from mcp_servers.strategy_router_server import route_strategy
from storage.repositories.research_repository import DecisionRepository


def _seed_decision(repo: DecisionRepository, regime: str, skill_slug: str | None, excess_by_horizon: dict[int, float], thesis: dict | None = None):
    decision = repo.create(query="q", market_regime=regime, skill_slug=skill_slug, thesis=thesis or {})
    for horizon, excess in excess_by_horizon.items():
        repo.add_outcome(
            decision_id=decision.id,
            evaluation_date=date(2026, 7, 25),
            horizon_days=horizon,
            market_excess_return=excess,
        )
    return decision


def test_extract_strategy_key_priority():
    assert extract_strategy_key(skill_slug="B1") == "B1"
    assert extract_strategy_key(thesis={"strategy_key": "RPS"}) == "RPS"
    assert extract_strategy_key(thesis={"route": {"preferred_strategies": {"B2": 0.5, "B1": 0.3}}}) == "B2"
    assert extract_strategy_key(tool_trace=[{"tool": "route_strategy", "strategy": "BOX_TRADING"}]) == "BOX_TRADING"
    assert extract_strategy_key(themes=["半导体"]) == "theme:半导体"
    assert extract_strategy_key() == "UNKNOWN"


def test_compute_regime_strategy_stats_math(isolated_database):
    repo = DecisionRepository()
    values = [0.01, -0.02, 0.03, 0.0, 0.005, -0.01]
    for excess in values:
        _seed_decision(repo, "rotation_market", "B1", {5: excess})
    # 其他 regime / 策略 / 未覆盖 horizon 不应混入
    _seed_decision(repo, "rotation_market", "RPS", {5: 0.02})
    _seed_decision(repo, "downtrend_market", "B1", {5: -0.03})
    _seed_decision(repo, "rotation_market", "B1", {7: 0.5})  # horizon 7 不在默认 (1,5,20)

    stats = compute_regime_strategy_stats()
    row = next(item for item in stats if item["market_regime"] == "rotation_market" and item["strategy_key"] == "B1" and item["horizon_days"] == 5)
    assert row["sample_size"] == 6
    assert row["mean_excess_return"] == pytest.approx(round(sum(values) / 6, 6))
    assert row["median_excess_return"] == pytest.approx(0.0025)
    assert row["hit_rate"] == pytest.approx(0.5)
    assert not any(item["horizon_days"] == 7 for item in stats)
    assert any(item["strategy_key"] == "RPS" for item in stats)


def test_summarize_for_route_respects_min_samples(isolated_database):
    repo = DecisionRepository()
    for _ in range(6):
        _seed_decision(repo, "rotation_market", "B1", {5: 0.02})
    for _ in range(3):
        _seed_decision(repo, "rotation_market", "RPS", {5: -0.01})

    stats = compute_regime_strategy_stats()
    summary = summarize_for_route(stats, "rotation_market", ["B1", "RPS", "BOX_TRADING"], min_samples=5)
    assert set(summary) == {"B1"}
    assert summary["B1"]["sample_size"] == 6
    assert summary["B1"]["historical_hit_rate"] == 1.0
    assert summary["B1"]["by_horizon"]["5"]["sample_size"] == 6

    relaxed = summarize_for_route(stats, "rotation_market", ["B1", "RPS"], min_samples=3)
    assert set(relaxed) == {"B1", "RPS"}


def test_route_strategy_attaches_calibration_when_enough_samples(isolated_database):
    repo = DecisionRepository()
    for index in range(6):
        _seed_decision(repo, "rotation_market", "B1", {1: 0.01, 5: 0.02, 20: -0.01})

    route = route_strategy("rotation_market")
    assert "calibration" in route
    assert route["calibration"]["min_samples"] == 5
    b1 = route["calibration"]["strategies"]["B1"]
    assert b1["sample_size"] == 6
    assert b1["historical_hit_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert set(b1["by_horizon"]) == {"1", "5", "20"}
    # 规则路由本体不变
    assert route["risk_limits"]["max_total_position"] == 0.65
    assert "B1" in route["preferred_strategies"]


def test_route_strategy_omits_calibration_when_insufficient_samples(isolated_database):
    repo = DecisionRepository()
    for _ in range(3):
        _seed_decision(repo, "rotation_market", "B1", {5: 0.02})

    route = route_strategy("rotation_market")
    assert "calibration" not in route
    assert route["risk_limits"]["max_total_position"] == 0.65


def test_route_strategy_omits_calibration_without_data(isolated_database):
    route = route_strategy("range_market")
    assert "calibration" not in route
    assert route["regime_name"] == "震荡箱体"
