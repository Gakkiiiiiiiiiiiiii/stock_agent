from __future__ import annotations

from datetime import date

import pytest

from engines.decision.attribution import build_attribution


def _outcome(**overrides):
    outcome = {
        "horizon_days": 5,
        "absolute_return": None,
        "market_return": None,
        "market_excess_return": None,
        "sector_return": None,
        "sector_excess_return": None,
        "theme_excess_return": None,
        "max_adverse_excursion": None,
        "max_favorable_excursion": None,
    }
    return outcome | overrides


def test_market_driven_win_attributes_market_regime_and_direction():
    result = build_attribution(
        {"id": "d1"},
        _outcome(absolute_return=0.09, market_return=0.08, market_excess_return=0.01, max_adverse_excursion=-0.005),
    )
    assert result["decision_id"] == "d1"
    assert result["horizon"] == 5
    assert set(result["correct"]) == {"direction", "market_regime", "entry_timing"}
    assert result["wrong"] == []
    assert set(result["unknown"]) == {"stock_selection", "sector_selection", "theme_selection"}
    assert result["contribution"]["market_regime"] == pytest.approx(0.08)
    assert result["contribution"]["theme_selection"] is None
    assert result["contribution"]["stock_selection"] == pytest.approx(0.01)
    assert result["contribution"]["timing"] == pytest.approx(-0.005)


def test_beating_market_but_lagging_sector_flags_sector_selection_wrong():
    result = build_attribution(
        {"decision_id": "d2"},
        _outcome(absolute_return=0.06, market_return=0.01, market_excess_return=0.05, sector_return=0.09, sector_excess_return=-0.03, max_adverse_excursion=-0.02),
    )
    assert "sector_selection" in result["wrong"]
    assert "stock_selection" in result["correct"]
    assert "direction" in result["correct"]
    assert "market_regime" in result["unknown"]
    assert "entry_timing" in result["unknown"]
    # 有行业腿时：主题/行业贡献=行业超额，选股贡献=组合相对行业
    assert result["contribution"]["theme_selection"] == pytest.approx(-0.03)
    assert result["contribution"]["stock_selection"] == pytest.approx(-0.03)


def test_deep_mae_before_recovery_flags_entry_timing_wrong():
    result = build_attribution(
        {"id": "d3"},
        _outcome(absolute_return=0.03, market_return=-0.08, market_excess_return=0.11, max_adverse_excursion=-0.12, max_favorable_excursion=0.05),
    )
    assert "entry_timing" in result["wrong"]
    assert "direction" in result["correct"]
    assert "stock_selection" in result["correct"]
    # 下跌市中 absolute > 0 → market_regime 不归因错误
    assert "market_regime" not in result["wrong"]


def test_falling_market_with_long_loss_flags_market_regime_wrong():
    result = build_attribution(
        {"id": "d4"},
        _outcome(absolute_return=-0.07, market_return=-0.06, market_excess_return=-0.01, max_adverse_excursion=-0.08),
    )
    assert set(result["wrong"]) == {"direction", "market_regime"}
    assert "stock_selection" in result["unknown"]


def test_missing_legs_fall_into_unknown():
    result = build_attribution({"id": "d5"}, _outcome())
    assert result["correct"] == []
    assert result["wrong"] == []
    assert set(result["unknown"]) == {"direction", "market_regime", "stock_selection", "sector_selection", "theme_selection", "entry_timing"}
    assert result["contribution"] == {"market_regime": None, "theme_selection": None, "stock_selection": None, "timing": None}


def test_backward_compat_alias_fields():
    outcome = {"horizon_days": 5, "portfolio_return": 0.05, "benchmark_return": 0.02, "excess_return": 0.03}
    result = build_attribution({"id": "d6"}, outcome)
    assert "direction" in result["correct"]
    assert "stock_selection" in result["correct"]
    assert result["contribution"]["market_regime"] == pytest.approx(0.02)


def test_review_runner_attaches_attribution_to_deterministic_review(isolated_database):
    from sqlalchemy.orm import Session

    from engines.decision.decision_service import DecisionService
    from engines.decision.review_runner import DecisionReviewRunner
    from storage.models.research import DecisionReview

    service = DecisionService()
    saved = service.save_decision(query="归因复盘", candidates=[{"symbol": "600000.SH"}], sector="医药")
    outcome = service.record_outcome(
        saved["decision_id"],
        date(2026, 8, 14),
        5,
        absolute_return=0.06,
        portfolio_return=0.06,
        market_return=0.01,
        benchmark_return=0.01,
        market_excess_return=0.05,
        excess_return=0.05,
        sector_return=0.09,
        sector_excess_return=-0.03,
        max_adverse_excursion=-0.02,
    )
    result = DecisionReviewRunner(review_agent=object()).run(saved["decision_id"], 5)
    assert result["mode"] == "deterministic_fallback"
    attribution = result["review"]["attribution"]
    assert "sector_selection" in attribution["wrong"]
    assert "stock_selection" in attribution["correct"]
    # what_was_wrong 使用归因维度而非单纯超额符号
    assert any("行业选择" in item for item in result["review"]["what_was_wrong"])
    with Session(isolated_database) as session:
        row = session.get(DecisionReview, result["review_id"])
        assert row.attribution_json["wrong"] == ["sector_selection"]
    # 结构化复盘（lessons + attribution）进入策略记忆
    assert result["memory_ids"]
    from storage.repositories.vector_repository import MemoryRepository

    memory = MemoryRepository().get(result["memory_ids"][0])
    assert memory.facts["attribution"]["wrong"] == ["sector_selection"]
