from __future__ import annotations

from engines.retrieval.query_understanding import build_retrieval_plan
from scripts.check_video_knowledge_collections import check_collections
from storage.repositories.knowledge_repository import KnowledgeVectorTaskService


def test_video_knowledge_vector_task_collection_routing():
    router = KnowledgeVectorTaskService()

    assert router.route_collection({"temporal_class": "DURABLE", "knowledge_kind": "METHOD"}) == "financial_video_durable_v1_bge_m3"
    assert router.route_collection({"temporal_class": "SNAPSHOT", "knowledge_kind": "STATE"}) == "financial_video_timed_v1_bge_m3"
    assert router.route_collection({"temporal_class": "SNAPSHOT", "knowledge_kind": "ACTION"}) == "financial_video_action_v1_bge_m3"


def test_video_knowledge_retrieval_plan_collection_routing():
    assert build_retrieval_plan("这个方法框架怎么判断")["collections"][0] == "financial_video_durable_v1_bge_m3"
    assert build_retrieval_plan("券商当前怎么看")["collections"][0] == "financial_video_timed_v1_bge_m3"
    assert build_retrieval_plan("券商仓位怎么操作")["collections"][0] == "financial_video_action_v1_bge_m3"


def test_video_knowledge_collection_check_dry_run_contract():
    result = check_collections(dry_run=True)

    assert result["ok"] is True
    assert [item["collection"] for item in result["items"]] == [
        "financial_video_durable_v1_bge_m3",
        "financial_video_timed_v1_bge_m3",
        "financial_video_action_v1_bge_m3",
    ]
    assert all(item["expected_dimension"] == 1024 for item in result["items"])
