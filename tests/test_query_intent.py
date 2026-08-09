from __future__ import annotations

from engines.retrieval.query_understanding import build_retrieval_plan
from engines.retrieval.retrieval_policy import RetrievalPolicy


def test_factual_query_requires_external_verified():
    plan = build_retrieval_plan("宁德时代当前PE是多少")
    assert plan["task_type"] == "factual_qa"
    policy = RetrievalPolicy.filters_for(plan["task_type"])
    assert policy["truth_status"] == "EXTERNALLY_VERIFIED"
    assert policy["minimum_support_status"] == "SOURCE_SUPPORTED"
    assert policy["minimum_support_probability"] == 0.7


def test_factual_query_variants():
    assert build_retrieval_plan("贵州茅台的毛利率是多少？")["task_type"] == "factual_qa"
    assert build_retrieval_plan("最新 CPI 数据出来了吗")["task_type"] == "factual_qa"
    assert build_retrieval_plan("央行政策利率几个基点?")["task_type"] == "factual_qa"


def test_indicator_without_question_word_is_not_factual_qa():
    # 指标词但没有疑问词：不判为 factual_qa
    assert build_retrieval_plan("PE 估值框架的逻辑")["task_type"] == "method_explanation"


def test_author_viewpoint_does_not_require_external_truth():
    plan = build_retrieval_plan("博主怎么看宁德时代")
    assert plan["task_type"] == "author_viewpoint"
    policy = RetrievalPolicy.filters_for(plan["task_type"])
    assert "truth_status" not in policy
    assert policy["minimum_support_status"] == "SOURCE_SUPPORTED"
    assert policy["minimum_support_probability"] == 0.6


def test_author_viewpoint_variants():
    assert build_retrieval_plan("这个主播认为黄金还能涨吗")["task_type"] == "author_viewpoint"
    assert build_retrieval_plan("up主在视频里说了什么")["task_type"] == "author_viewpoint"


def test_factual_takes_precedence_over_viewpoint_wording():
    # 同时命中 "怎么看" 与 factual 指标 + 疑问词：factual 优先
    plan = build_retrieval_plan("怎么看宁德时代现在 PE 是多少")
    assert plan["task_type"] == "factual_qa"


def test_existing_intents_still_work():
    assert build_retrieval_plan("这个方法框架怎么判断")["task_type"] == "method_explanation"
    assert build_retrieval_plan("券商仓位怎么操作")["task_type"] == "trading_decision"
    assert build_retrieval_plan("历史复盘当时怎么演变")["task_type"] == "history_review"


def test_market_opportunity_scan_override_stays_last():
    plan = build_retrieval_plan("最近有什么值得关注的板块方向")
    assert plan["task_type"] == "market_opportunity_scan"
