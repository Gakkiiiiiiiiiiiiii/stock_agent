from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from engines.decision.outcome_evaluator import DecisionOutcomeEvaluator

ENTRY = date(2026, 8, 10)
EXIT = date(2026, 8, 14)

KLINES = {
    "600000.SH": [
        {"date": "2026-08-10", "open": 10, "close": 10.5},
        {"date": "2026-08-11", "open": 10.5, "close": 9.5},
        {"date": "2026-08-12", "open": 9.5, "close": 10.2},
        {"date": "2026-08-14", "open": 10.2, "close": 12},
    ],
    "600001.SH": [
        {"date": "2026-08-10", "open": 20, "close": 20},
        {"date": "2026-08-11", "open": 20, "close": 19},
        {"date": "2026-08-12", "open": 19, "close": 21},
        {"date": "2026-08-14", "open": 21, "close": 21},
    ],
    "000991.SH": [{"date": "2026-08-10", "open": 100, "close": 100}, {"date": "2026-08-14", "open": 102, "close": 104}],
    "399006.SZ": [{"date": "2026-08-10", "open": 200, "close": 200}, {"date": "2026-08-14", "open": 201, "close": 202}],
}


def _decision(**overrides):
    decision = {
        "decision_as_of": datetime(2026, 8, 7, 15, 0, tzinfo=UTC),
        "candidates": [{"symbol": "600000.SH"}, {"symbol": "600001.SH"}],
        "sector": "医药",
        "style": "growth",
        "themes": ["创新药"],
    }
    return decision | overrides


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setattr("engines.decision.outcome_evaluator.advance_trading_days", lambda _day, _count: ENTRY)
    monkeypatch.setattr("engines.decision.outcome_evaluator.get_kline", lambda symbol, **_kwargs: {"records": KLINES[symbol]})
    return KLINES


def test_v2_metrics_and_backward_compat_aliases(fake_provider):
    result = DecisionOutcomeEvaluator().evaluate(_decision(), EXIT)
    # 候选等权：(0.2 + 0.05) / 2
    assert result["absolute_return"] == pytest.approx(0.125)
    assert result["portfolio_return"] == pytest.approx(result["absolute_return"])
    # 主基准 = sector 路由 → 000991.SH（全指医药），0.04
    assert result["market_return"] == pytest.approx(0.04)
    assert result["benchmark_return"] == pytest.approx(result["market_return"])
    assert result["market_excess_return"] == pytest.approx(0.085)
    assert result["excess_return"] == pytest.approx(result["market_excess_return"])
    # style 腿：创业板指 0.01
    assert result["style_return"] == pytest.approx(0.01)
    assert result["style_excess_return"] == pytest.approx(0.115)
    # sector 腿与主基准同源
    assert result["sector_return"] == pytest.approx(0.04)
    assert result["sector_excess_return"] == pytest.approx(0.085)
    # theme 篮子 = 候选等权代理
    assert result["theme_basket_return"] == pytest.approx(result["absolute_return"])
    assert result["theme_excess_return"] == pytest.approx(0.0)
    assert "THEME_BASKET_PROXY_EQUAL_WEIGHT_CANDIDATES" in result["realized_metrics"]["flags"]
    # 路径指标：组合净值 1.025 → 0.95 → 1.035 → 1.125
    assert result["max_favorable_excursion"] == pytest.approx(0.125)
    assert result["max_adverse_excursion"] == pytest.approx(-0.05)
    assert result["max_drawdown"] == pytest.approx(0.95 / 1.025 - 1)
    # 基准路由持久化
    route = result["benchmark_route"]
    assert route["primary_benchmark"] == "000991.SH"
    assert route["style_benchmark"] == "399006.SZ"
    assert route["router_version"] == "benchmark_router_v1"
    assert route["reason"]


def test_missing_benchmark_leg_sets_none_and_flag(monkeypatch):
    monkeypatch.setattr("engines.decision.outcome_evaluator.advance_trading_days", lambda _day, _count: ENTRY)

    def broken_kline(symbol, **_kwargs):
        if symbol == "399006.SZ":
            raise RuntimeError("provider down")
        return {"records": KLINES[symbol]}

    monkeypatch.setattr("engines.decision.outcome_evaluator.get_kline", broken_kline)
    result = DecisionOutcomeEvaluator().evaluate(_decision(), EXIT)
    assert result["style_return"] is None
    assert result["style_excess_return"] is None
    assert "STYLE_BENCHMARK_UNAVAILABLE" in result["realized_metrics"]["flags"]
    # 其余腿不受影响
    assert result["market_return"] == pytest.approx(0.04)
    assert result["absolute_return"] == pytest.approx(0.125)


def test_market_benchmark_unavailable_never_crashes(monkeypatch):
    monkeypatch.setattr("engines.decision.outcome_evaluator.advance_trading_days", lambda _day, _count: ENTRY)

    def broken_kline(symbol, **_kwargs):
        if symbol == "000001.SH":
            return {"records": []}
        return {"records": KLINES.get(symbol, [])}

    monkeypatch.setattr("engines.decision.outcome_evaluator.get_kline", broken_kline)
    decision = _decision(sector=None, style=None, themes=[])
    result = DecisionOutcomeEvaluator().evaluate(decision, EXIT)
    assert result["market_return"] is None
    assert result["benchmark_return"] is None
    assert result["excess_return"] is None
    assert result["theme_basket_return"] is None
    assert "MARKET_BENCHMARK_UNAVAILABLE" in result["realized_metrics"]["flags"]
    assert result["absolute_return"] == pytest.approx(0.125)


def test_explicit_benchmark_symbol_overrides_primary(fake_provider):
    result = DecisionOutcomeEvaluator().evaluate(_decision(benchmark_symbol="399006.SZ"), EXIT)
    assert result["market_return"] == pytest.approx(0.01)
    assert result["benchmark_route"]["primary_benchmark"] == "399006.SZ"
    assert result["realized_metrics"]["benchmark"]["symbol"] == "399006.SZ"


def test_evaluation_is_deterministic(fake_provider):
    evaluator = DecisionOutcomeEvaluator()
    first = evaluator.evaluate(_decision(), EXIT)
    second = evaluator.evaluate(_decision(), EXIT)
    assert first == second


def test_storage_round_trip_of_v2_columns(isolated_database):
    from sqlalchemy.orm import Session

    from engines.decision.decision_service import DecisionService
    from storage.models.research import DecisionReview, InvestmentDecision, InvestmentDecisionOutcome

    service = DecisionService()
    saved = service.save_decision(
        query="创新药主题",
        candidates=[{"symbol": "600000.SH"}],
        themes=["创新药"],
        sector="医药",
        style="growth",
        decision_type="theme_rotation",
        retrieval_context_ids=["ctx-1", "ctx-2"],
    )
    with Session(isolated_database) as session:
        row = session.get(InvestmentDecision, saved["decision_id"])
        assert row.benchmark_symbol == "000991.SH"
        assert row.benchmark_route["primary_benchmark"] == "000991.SH"
        assert row.benchmark_route["router_version"] == "benchmark_router_v1"
        assert row.retrieval_context_ids == ["ctx-1", "ctx-2"]

    outcome = service.record_outcome(
        saved["decision_id"],
        EXIT,
        5,
        absolute_return=0.125,
        portfolio_return=0.125,
        market_return=0.04,
        benchmark_return=0.04,
        market_excess_return=0.085,
        excess_return=0.085,
        style_return=0.01,
        style_excess_return=0.115,
        sector_return=0.04,
        sector_excess_return=0.085,
        theme_basket_return=0.125,
        theme_excess_return=0.0,
        max_drawdown=-0.0732,
        max_adverse_excursion=-0.05,
        max_favorable_excursion=0.125,
        benchmark_route={"primary_benchmark": "000991.SH", "router_version": "benchmark_router_v1"},
    )
    with Session(isolated_database) as session:
        row = session.get(InvestmentDecisionOutcome, outcome["outcome_id"])
        assert row.absolute_return == pytest.approx(0.125)
        assert row.portfolio_return == pytest.approx(row.absolute_return)
        assert row.market_return == pytest.approx(row.benchmark_return)
        assert row.market_excess_return == pytest.approx(row.excess_return)
        assert row.max_drawdown == pytest.approx(-0.0732)
        assert row.max_adverse_excursion == pytest.approx(-0.05)
        assert row.max_favorable_excursion == pytest.approx(0.125)
        assert row.benchmark_route["primary_benchmark"] == "000991.SH"

    attribution = {"decision_id": saved["decision_id"], "horizon": 5, "correct": ["direction"], "wrong": ["sector_selection"], "unknown": [], "contribution": {"market_regime": 0.04}}
    review = service.review(saved["decision_id"], {"decision_quality": 0.7, "lessons": ["行业选错"], "attribution": attribution}, outcome["outcome_id"])
    with Session(isolated_database) as session:
        row = session.get(DecisionReview, review["review_id"])
        assert row.attribution_json["wrong"] == ["sector_selection"]
        assert row.attribution_json["contribution"]["market_regime"] == pytest.approx(0.04)
