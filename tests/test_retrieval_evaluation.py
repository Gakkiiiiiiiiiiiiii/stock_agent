from __future__ import annotations

from datetime import datetime

from engines.memory.memory_retriever import retrieve_memory
from engines.retrieval.evaluation.ablation_runner import RetrievalAblationRunner
from engines.retrieval.evaluation.models import RetrievalGoldenCase
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner
from engines.retrieval.filters import RetrievalFilter, normalize_retrieval_filters
from engines.retrieval.evaluation.regression import compare_to_baseline
from app.model_capabilities import ModelCapabilities
from engines.domain_result import DomainResultMeta


class FakeRetriever:
    def __init__(self) -> None:
        self.filters = None

    def retrieve(self, query, **kwargs):
        self.filters = kwargs.get("filters")
        return {
            "contexts": [
                {"record": {"id": "memory:strategy-1", "title": "轮动策略", "memory_type": "STRATEGY_EXPERIENCE", "related_regime": "rotation_market", "source_type": "decision_review", "source_date": "2026-08-08T08:00:00+00:00", "status": "ACTIVE"}},
                {"record": {"id": "memory:expired", "title": "过期策略", "memory_type": "THEME", "status": "EXPIRED"}},
            ]
        }


def test_retrieval_filter_normalizes_typed_constraints():
    filters = normalize_retrieval_filters(RetrievalFilter(memory_types=["STRATEGY_EXPERIENCE"], symbols=["600000.SH"]))
    assert filters == {"memory_type": ["STRATEGY_EXPERIENCE"], "related_symbol": ["600000.SH"]}


def test_memory_type_is_pushed_before_candidate_limit(monkeypatch):
    fake = FakeRetriever()
    monkeypatch.setattr("engines.memory.memory_retriever.HybridRetriever", lambda: fake)
    monkeypatch.setattr("engines.memory.memory_retriever.MemoryScorer.rank", lambda _self, contexts, _regime: contexts)
    result = retrieve_memory("轮动策略", memory_types=["STRATEGY_EXPERIENCE"], top_k=1)
    assert fake.filters["memory_type"] == ["STRATEGY_EXPERIENCE"]
    assert [item["record"]["id"] for item in result["contexts"]] == ["memory:strategy-1"]


def test_evaluation_and_ablation_write_reports(tmp_path):
    case = RetrievalGoldenCase.model_validate(
        {
            "case_id": "strategy_memory_001",
            "query": "轮动市策略",
            "task_type": "memory_lookup",
            "expected_ids": ["memory:strategy-1"],
            "expected_memory_types": ["STRATEGY_EXPERIENCE"],
            "expected_regime": "rotation_market",
            "forbidden_ids": ["memory:expired"],
            "as_of": "2026-08-08T10:00:00+08:00",
            "expected_sources": [{"source_type": "decision_review"}],
        }
    )
    result = RetrievalEvaluationRunner(FakeRetriever()).run([case])
    assert result["summary"]["recall_at_10"] == 1
    assert result["summary"]["conflict_accuracy"] == 0
    reports = RetrievalAblationRunner({"dense_sparse": FakeRetriever()}).run([case], tmp_path)
    assert reports[0].variant == "dense_sparse"
    assert (tmp_path / "dense_sparse" / "summary.json").exists()


def test_shared_model_and_domain_contracts():
    capabilities = ModelCapabilities(tool_calling=True, json_schema=True, context_window=128000)
    assert capabilities.tool_calling and capabilities.context_window == 128000
    meta = DomainResultMeta(data_source="qmt", missing_fields=["turnover"])
    assert meta.data_source == "qmt" and meta.missing_fields == ["turnover"]


def test_regression_gate_uses_relative_tolerance_and_zero_leakage_growth():
    baseline = {"recall_at_10": 0.9, "ndcg_at_10": 0.8, "expired_knowledge_leakage_rate": 0.0}
    assert compare_to_baseline({"recall_at_10": 0.88, "ndcg_at_10": 0.78, "expired_knowledge_leakage_rate": 0.0}, baseline) == []
    violations = compare_to_baseline({"recall_at_10": 0.8, "ndcg_at_10": 0.8, "expired_knowledge_leakage_rate": 0.1}, baseline)
    assert {"REGRESSION:recall_at_10", "REGRESSION:expired_knowledge_leakage_rate"} <= set(violations)
