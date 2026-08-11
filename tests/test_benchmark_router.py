from __future__ import annotations

from engines.decision.benchmark_router import THEME_BASKET, BenchmarkRouter


def test_sector_map_wins_and_sets_sector_leg():
    route = BenchmarkRouter().route({"sector": "医药"})
    assert route["primary_benchmark"] == "000991.SH"
    assert route["sector_benchmark"] == "000991.SH"
    assert route["style_benchmark"] is None
    assert route["router_version"] == "benchmark_router_v1"
    assert "医药" in route["reason"]


def test_sector_beats_style_precedence():
    route = BenchmarkRouter().route({"sector": "医药", "style": "growth"})
    assert route["primary_benchmark"] == "000991.SH"
    assert route["style_benchmark"] == "399006.SZ"


def test_style_routing_variants():
    assert BenchmarkRouter().route({"style": "growth"})["primary_benchmark"] == "399006.SZ"
    assert BenchmarkRouter().route({"style": "dividend"})["primary_benchmark"] == "000922.SH"
    assert BenchmarkRouter().route({"style": "value"})["primary_benchmark"] == "000922.SH"
    assert BenchmarkRouter().route({"style": "microcap"})["primary_benchmark"] == "932000.CSI"
    assert BenchmarkRouter().route({"style": "small_cap"})["primary_benchmark"] == "000852.SH"
    assert BenchmarkRouter().route({"style": "large_cap"})["primary_benchmark"] == "000300.SH"


def test_decision_type_routing():
    route = BenchmarkRouter().route({"decision_type": "index_timing"})
    assert route["primary_benchmark"] == "000300.SH"
    assert "index_timing" in route["reason"]


def test_default_fallback_and_reason():
    route = BenchmarkRouter().route({})
    assert route["primary_benchmark"] == "000001.SH"
    assert route["style_benchmark"] is None
    assert route["sector_benchmark"] is None
    assert route["theme_benchmark"] is None
    assert route["reason"]
    assert route["router_version"] == "benchmark_router_v1"


def test_theme_basket_signaled_with_sector_primary_fallback():
    route = BenchmarkRouter().route({"themes": ["创新药"], "sector": "医药"})
    assert route["theme_benchmark"] == THEME_BASKET
    assert route["primary_benchmark"] == "000991.SH"


def test_unknown_values_fall_back_to_default():
    route = BenchmarkRouter().route({"sector": "不存在的行业", "style": "不存在的风格", "decision_type": "unknown"})
    assert route["primary_benchmark"] == "000001.SH"
    assert route["reason"].startswith("default:")


def test_router_is_deterministic():
    attrs = {"sector": "科技", "style": "growth", "themes": ["AI"], "market": "CN_A"}
    assert BenchmarkRouter().route(attrs) == BenchmarkRouter().route(attrs)
