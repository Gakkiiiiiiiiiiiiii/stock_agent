"""Schema-v2 retrieval evaluation: graded metrics, temporal/expired/conflict/diversity
metrics, v2 dataset loader, ablation ladder, and golden_v1 backward compatibility."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from engines.retrieval.evaluation.ablation_runner import RetrievalAblationRunner, build_standard_ablation_variants
from engines.retrieval.evaluation.fixture_corpus import build_fixture_hybrid_retriever, load_fixture_records
from engines.retrieval.evaluation.metrics import (
    conflict_resolution_accuracy,
    expired_context_rate,
    graded_ndcg_at_k,
    graded_recall_at_k,
    graded_reciprocal_rank,
    ndcg_at_k,
    source_diversity,
    temporal_precision,
)
from engines.retrieval.evaluation.models import ContradictionPair, RetrievalGoldenCase
from engines.retrieval.evaluation.regression import compare_to_baseline
from engines.retrieval.evaluation.runner import RetrievalEvaluationRunner

DATASETS = Path("engines/retrieval/evaluation/datasets")


class StubRetriever:
    """Deterministic retriever returning a fixed ranking for metric tests."""

    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def retrieve(self, query, **kwargs):
        return {"contexts": [{"record": record, **record} for record in self.records]}


# --- graded metric math (hand-computed) -------------------------------------


def test_graded_ndcg_hand_computed():
    retrieved = ["a", "b", "c"]
    gains = {"a": 3.0, "c": 1.0}
    dcg = 3.0 / math.log2(2) + 1.0 / math.log2(4)  # 3.5
    idcg = 3.0 / math.log2(2) + 1.0 / math.log2(3)  # 3 + 0.6309...
    assert graded_ndcg_at_k(retrieved, gains, 3) == pytest.approx(dcg / idcg)
    assert graded_ndcg_at_k([], gains, 3) == 0.0
    assert graded_ndcg_at_k(retrieved, {}, 3) == 1.0  # vacuous


def test_graded_ndcg_matches_binary_for_uniform_gains():
    retrieved = ["a", "x", "b", "y"]
    expected = {"a", "b"}
    gains = {"a": 2.0, "b": 2.0}
    assert graded_ndcg_at_k(retrieved, gains, 4) == pytest.approx(ndcg_at_k(retrieved, expected, 4))


def test_graded_recall_counts_partial_and_mrr_first_relevant():
    gains = {"a": 1.0, "c": 2.0}  # partially relevant counts toward recall
    assert graded_recall_at_k(["a", "b"], gains, 5) == pytest.approx(0.5)
    assert graded_recall_at_k(["a", "c"], gains, 1) == pytest.approx(0.5)
    assert graded_recall_at_k(["a", "c"], gains, 5) == pytest.approx(1.0)
    assert graded_reciprocal_rank(["x", "a"], gains) == pytest.approx(0.5)
    assert graded_reciprocal_rank(["x", "y"], gains) == 0.0


# --- temporal / expired / conflict / diversity metrics -----------------------


def _record(doc_id, **extra):
    return {"id": doc_id, **extra}


def test_temporal_precision_penalizes_expired_and_superseded():
    records = [_record("d1"), _record("d2"), _record("d3")]
    assert temporal_precision(records, {"d2"}, {"d3"}) == pytest.approx(1 / 3)
    assert temporal_precision(records, set(), set()) == 1.0
    assert temporal_precision([], {"d2"}, set()) == 1.0


def test_expired_context_rate_uses_labels_and_record_status():
    records = [_record("d1"), _record("d2", status="EXPIRED"), _record("d3")]
    assert expired_context_rate(records, {"d3"}) == pytest.approx(2 / 3)
    assert expired_context_rate(records, set()) == pytest.approx(1 / 3)
    assert expired_context_rate([], {"d3"}) == 0.0


def test_conflict_resolution_accuracy_winner_above_or_loser_absent():
    pair = ContradictionPair(winner_id="w", loser_id="l")
    assert conflict_resolution_accuracy(["w", "x", "l"], [pair]) == 1.0
    assert conflict_resolution_accuracy(["w", "x"], [pair]) == 1.0  # loser absent
    assert conflict_resolution_accuracy(["l", "w"], [pair]) == 0.0  # loser on top
    assert conflict_resolution_accuracy(["l", "x"], [pair]) == 0.0  # winner absent
    assert conflict_resolution_accuracy(["w"], []) is None  # no annotation


def test_source_diversity_against_available_corpus_types():
    records = [_record("d1", source_type="a"), _record("d2", source_type="a"), _record("d3", source_type="b")]
    assert source_diversity(records, 3, {"a", "b", "c"}) == pytest.approx(2 / 3)
    assert source_diversity(records, 2, {"a", "b", "c"}) == pytest.approx(1 / 2)
    assert source_diversity(records, 3, None) == 1.0  # unknown corpus -> no penalty
    assert source_diversity([], 3, {"a"}) == 1.0


# --- dataset schema v2 loader -------------------------------------------------


def test_v2_case_parses_grades_category_and_pairs():
    case = RetrievalGoldenCase.model_validate(
        {
            "case_id": "v2_demo",
            "query": "冲突知识",
            "category": "冲突知识",
            "graded_labels": {"w": 3, "p": "partially_relevant", "i": 0, "e": "expired", "l": "contradictory"},
            "superseded": {"e": "w"},
            "contradictions": [{"winner_id": "w", "loser_id": "l"}],
        }
    )
    assert case.gain_map() == {"w": 3.0, "p": 1.0}
    assert case.relevant_ids() == {"w", "p"}
    assert case.expired_ids() == {"e"}
    assert case.contradictory_ids() == {"l"}
    assert case.contradictions[0].winner_id == "w"


def test_v2_case_rejects_unknown_category_and_grade():
    with pytest.raises(ValueError, match="unknown category"):
        RetrievalGoldenCase.model_validate({"case_id": "x", "query": "q", "category": "不存在的类别"})
    with pytest.raises(ValueError, match="unknown grade"):
        RetrievalGoldenCase.model_validate({"case_id": "x", "query": "q", "graded_labels": {"d": 7}})


def test_v1_binary_labels_map_onto_v2_schema():
    case = RetrievalGoldenCase.model_validate({"case_id": "v1", "query": "q", "expected_ids": ["a", "b"]})
    assert case.category is None
    assert case.gain_map() == {"a": 2.0, "b": 2.0}
    assert case.expired_ids() == set() and case.contradictions == []


def test_v2_sample_dataset_covers_all_categories_and_validates():
    runner = RetrievalEvaluationRunner(StubRetriever([]))
    cases = runner.load_dataset(DATASETS / "golden_v2_sample.jsonl")
    assert 10 <= len(cases) <= 20
    categories = {case.category for case in cases}
    assert categories == {
        "当前市场方向",
        "历史主题逻辑",
        "个股研究",
        "决策经验",
        "用户偏好",
        "视频最新观点",
        "冲突知识",
        "已过期知识",
    }
    assert any(case.contradictions for case in cases)
    assert any(case.expired_ids() for case in cases)
    assert any(case.superseded for case in cases)
    # every labeled doc id exists in the fixture corpus
    corpus_ids = {record["id"] for record in load_fixture_records()}
    for case in cases:
        assert set(case.graded_labels) <= corpus_ids
        assert set(case.superseded) | set(case.superseded.values()) <= corpus_ids


# --- runner integration -------------------------------------------------------


def test_runner_emits_v2_metrics_for_annotated_cases():
    records = [
        _record("w", source_type="decision_review", status="ACTIVE"),
        _record("l", source_type="video_knowledge_unit", status="ACTIVE"),
        _record("e", source_type="video_knowledge_unit", status="EXPIRED"),
    ]
    case = RetrievalGoldenCase.model_validate(
        {
            "case_id": "v2_run",
            "query": "q",
            "category": "冲突知识",
            "graded_labels": {"w": 3, "l": "contradictory", "e": "expired"},
            "contradictions": [{"winner_id": "w", "loser_id": "l"}],
            "superseded": {"e": "w"},
        }
    )
    result = RetrievalEvaluationRunner(StubRetriever(records), corpus_records=records).run([case])
    row = result["cases"][0]
    assert row["recall_at_10"] == 1.0
    assert row["mrr"] == 1.0
    assert row["ndcg_at_10"] == 1.0
    assert row["conflict_resolution_accuracy"] == 1.0  # winner ranks above loser
    assert row["expired_context_rate"] == pytest.approx(1 / 3)
    assert row["temporal_precision"] == pytest.approx(2 / 3)  # "e" superseded by "w"
    assert row["source_diversity"] == pytest.approx(1.0)
    summary = result["summary"]
    for key in ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10", "temporal_precision", "expired_context_rate", "conflict_resolution_accuracy", "source_diversity"):
        assert key in summary


def test_runner_conflict_accuracy_vacuous_without_pairs():
    records = [_record("a", source_type="decision_review")]
    case = RetrievalGoldenCase.model_validate({"case_id": "plain", "query": "q", "expected_ids": ["a"]})
    result = RetrievalEvaluationRunner(StubRetriever(records)).run([case])
    assert result["cases"][0]["conflict_resolution_accuracy"] is None
    assert result["summary"]["conflict_resolution_accuracy"] == 1.0  # vacuous


# --- ablation ladder ----------------------------------------------------------


def test_ablation_ladder_covers_component_steps():
    variants = build_standard_ablation_variants(factory=lambda *, config: config)
    assert list(variants) == [
        "dense_only",
        "sparse_only",
        "dense_sparse",
        "with_bm25_score",
        "with_reranker",
        "with_freshness",
        "with_source_priority",
        "with_conflict_resolution",
    ]
    assert variants["sparse_only"].dense_recall_enabled is False
    assert variants["sparse_only"].sparse_recall_enabled is True
    assert variants["dense_only"].dense_recall_enabled is True
    assert variants["dense_only"].sparse_recall_enabled is False


def test_ablation_ladder_produces_per_component_metric_rows(tmp_path):
    corpus = load_fixture_records()
    cases = RetrievalEvaluationRunner(build_fixture_hybrid_retriever()).load_dataset(DATASETS / "golden_v2_sample.jsonl")
    variants = build_standard_ablation_variants(factory=lambda *, config: build_fixture_hybrid_retriever(config=config, records=corpus))
    reports = RetrievalAblationRunner(variants, corpus_records=corpus).run(cases, tmp_path)
    assert len(reports) == 8
    for report in reports:
        assert 0.0 <= report.recall_at_10 <= 1.0
        assert 0.0 <= report.temporal_precision <= 1.0
        assert 0.0 <= report.expired_context_rate <= 1.0
        assert 0.0 <= report.source_diversity <= 1.0
    conflict_reports = [report for report in reports if report.variant == "with_conflict_resolution"]
    assert 0.0 <= conflict_reports[0].conflict_resolution_accuracy <= 1.0
    assert (tmp_path / "sparse_only" / "summary.json").exists()


# --- regression gate opt-in ----------------------------------------------------


def test_regression_gate_skips_v2_metrics_absent_from_baseline():
    baseline = {"recall_at_10": 0.9, "expired_knowledge_leakage_rate": 0.0}
    current = {"recall_at_10": 0.9, "expired_knowledge_leakage_rate": 0.0, "temporal_precision": 0.1, "expired_context_rate": 0.9}
    assert compare_to_baseline(current, baseline) == []


def test_regression_gate_compares_v2_metrics_when_baseline_carries_them():
    baseline = {"recall_at_10": 0.9, "temporal_precision": 0.9, "conflict_resolution_accuracy": 0.8, "source_diversity": 0.7, "expired_context_rate": 0.1}
    violations = compare_to_baseline(
        {"recall_at_10": 0.9, "temporal_precision": 0.5, "conflict_resolution_accuracy": 0.8, "source_diversity": 0.7, "expired_context_rate": 0.2},
        baseline,
    )
    assert violations == ["REGRESSION:temporal_precision", "REGRESSION:expired_context_rate"]


# --- golden_v1 backward compatibility -----------------------------------------


def test_golden_v1_metrics_unchanged_and_new_keys_present():
    cases = RetrievalEvaluationRunner(build_fixture_hybrid_retriever()).load_dataset(DATASETS / "golden_v1.jsonl")
    corpus = load_fixture_records()
    summary = RetrievalEvaluationRunner(build_fixture_hybrid_retriever(), corpus_records=corpus).run(cases)["summary"]
    baseline = json.loads((DATASETS / "baseline_v1.json").read_text(encoding="utf-8"))["metrics"]
    assert compare_to_baseline(summary, baseline) == []
    # v1 stays binary: identical metric values within float noise
    assert summary["recall_at_10"] == pytest.approx(baseline["recall_at_10"], abs=1e-9)
    assert summary["ndcg_at_10"] == pytest.approx(baseline["ndcg_at_10"], abs=1e-9)
    assert summary["mrr"] == pytest.approx(baseline["mrr"], abs=1e-9)
    # new v2 keys are emitted (not compared against the v1 baseline)
    for key in ("temporal_precision", "expired_context_rate", "conflict_resolution_accuracy", "source_diversity"):
        assert key in summary
    # golden_v1 annotates no contradictory pairs -> vacuous 1.0
    assert summary["conflict_resolution_accuracy"] == 1.0
