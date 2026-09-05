from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.baselines import Baseline, compare_to_baseline
from evals.datasets import DecisionEvalCase, EvalDataset, EvalExpected, load_jsonl
from evals.metrics import (
    aggregate_metrics,
    compute_metrics,
    evidence_precision,
    evidence_recall,
)
from evals.reports import EvaluationReport
from evals.runners import EvalRunner, Variant, compare_variants

NOW = datetime(2026, 1, 2, 9, tzinfo=UTC)


def case(**expected):
    return DecisionEvalCase(case_id="case-1", decision_time=NOW, task="decision", bundle_id="b-1", expected=EvalExpected(**expected))


def fixed_inputs():
    return {"bundle_id": "b-1", "bundle_hash": "b" * 64}, {"snapshot_id": "s-1", "snapshot_hash": "s" * 64}


def test_dataset_rejects_naive_duplicate_and_unknown_cases(tmp_path):
    naive = {"case_id": "a", "decision_time": "2026-01-02T09:00:00", "task": "x", "bundle_id": "b", "expected": {}}
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(naive) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid eval case"):
        load_jsonl(path)
    aware = dict(naive, decision_time="2026-01-02T09:00:00Z")
    path.write_text(json.dumps(aware) + "\n" + json.dumps(aware) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_jsonl(path)
    with pytest.raises(ValueError):
        DecisionEvalCase.model_validate({**aware, "expected": {"required_evidence_types": ["NOPE"]}})


def test_dataset_order_and_serialization_are_deterministic(tmp_path):
    rows = [
        {"case_id": "z", "decision_time": "2026-01-02T09:00:00Z", "task": "x", "bundle_id": "b", "expected": {}},
        {"case_id": "a", "decision_time": "2026-01-02T09:00:00Z", "task": "x", "bundle_id": "b", "expected": {}},
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    dataset = load_jsonl(path)
    assert [item.case_id for item in dataset.ordered()] == ["a", "z"]
    assert dataset.model_dump_json_deterministic() == dataset.model_dump_json_deterministic()


def test_evidence_metrics_and_zero_denominators_are_explicit():
    empty = case()
    assert evidence_recall(empty, {}).value == 1.0
    assert evidence_precision(empty, {}).value == 1.0
    assert compute_metrics(empty, {})["stale_evidence_rate"] == 0.0
    labelled = case(required_evidence_types=["MARKET_SNAPSHOT", "FACTOR_SCORE"])
    output = {"evidence": [{"evidence_type": "MARKET_SNAPSHOT", "evidence_id": "e1", "quality_status": "STALE"}, {"evidence_type": "OTHER"}], "claims": [{"claim": "explicit", "evidence_refs": ["missing"]}]}
    metrics = compute_metrics(labelled, output)
    assert metrics["evidence_recall"] == pytest.approx(0.5)
    assert metrics["evidence_precision"] == pytest.approx(0.5)
    assert metrics["stale_evidence_rate"] == pytest.approx(0.5)
    assert metrics["unsupported_claim_rate"] == 1.0


def test_metrics_use_explicit_claim_refs_and_calibration():
    labelled = case(required_evidence_types=["MARKET_SNAPSHOT"], risk_flags=["LIQUIDITY"])
    output = {"evidence": [{"evidence_type": "MARKET_SNAPSHOT", "evidence_id": "e1"}], "claims": [{"claim": "unsupported words", "evidence_refs": ["e1"]}], "risk_flags": ["LIQUIDITY"], "confidence": 0.8, "correct": True}
    metrics = compute_metrics(labelled, output)
    assert metrics["unsupported_claim_rate"] == 0.0
    assert metrics["risk_recall"] == 1.0
    assert metrics["brier_score"] == pytest.approx(0.04)
    assert metrics["ece"] is None  # ECE is only a fixed-bin dataset aggregate.


def test_calibration_is_aggregated_with_fixed_bins_and_specialist_denominator():
    first = case(required_specialists=["risk", "technical"])
    second = DecisionEvalCase(case_id="case-2", decision_time=NOW, task="decision", bundle_id="b-2", expected=EvalExpected(required_specialists=["risk", "technical"]))
    outputs = [
        {"confidence": 0.81, "correct": True, "specialists": [{"name": "risk", "status": "COMPLETED"}]},
        {"confidence": 0.89, "correct": False, "specialists": [{"name": "risk", "status": "COMPLETED"}, {"name": "technical", "status": "COMPLETED"}]},
    ]
    metrics = aggregate_metrics([first, second], outputs)
    assert metrics["brier_score"] == pytest.approx((0.19**2 + 0.89**2) / 2)
    # Both predictions fall into the .8 bin: confidence mean .85, outcome .5.
    assert metrics["ece"] == pytest.approx(0.35)
    assert metrics["confidence_calibration"] == pytest.approx(1 - (0.19 + 0.89) / 2)
    assert compute_metrics(first, outputs[0])["specialist_completion_rate"] == pytest.approx(0.5)


def test_expected_constraints_are_auditable_metrics():
    labelled = case(forbidden_claims=["c-1"], acceptable_actions=["HOLD"], max_target_weight=0.2)
    metrics = compute_metrics(labelled, {"claims": [{"claim_id": "c-1", "evidence_refs": []}], "action": "BUY", "target_weight": 0.3})
    assert metrics["forbidden_claim_rate"] == 1.0
    assert metrics["acceptable_action_rate"] == 0.0
    assert metrics["target_weight_constraint_violation_rate"] == 1.0


def test_runner_fixed_inputs_and_variant_comparison():
    bundle, snapshot = fixed_inputs()
    dataset = EvalDataset(cases=[case(required_evidence_types=["MARKET_SNAPSHOT"])])
    seen = []

    def adapter(context):
        seen.append((context.bundle, context.snapshot, context.replay_kind))
        return {"evidence": [{"evidence_type": "MARKET_SNAPSHOT", "evidence_id": "e1"}], "action": "HOLD"}

    report = compare_variants(dataset, Variant("v2", adapter), Variant("v3", adapter), bundle=bundle, snapshot=snapshot, kind="skill")
    assert report.results_a[0].status == report.results_b[0].status == "OK"
    assert report.results_a[0].bundle_hash == "b" * 64
    assert report.deltas["evidence_recall"] == 0.0
    assert seen and all(item[2] == "skill" for item in seen)


def test_runner_resolves_fixed_inputs_per_case_and_computes_stability():
    first = case()
    second = DecisionEvalCase(case_id="case-2", decision_time=NOW, task="decision", bundle_id="b-2", expected=EvalExpected())
    dataset = EvalDataset(cases=[first, second])
    bundle = {"b-1": {"bundle_id": "b-1", "bundle_hash": "1" * 64}, "b-2": {"bundle_id": "b-2", "bundle_hash": "2" * 64}}
    snapshot = {"b-1": {"snapshot_id": "s-1", "snapshot_hash": "3" * 64}, "b-2": {"snapshot_id": "s-2", "snapshot_hash": "4" * 64}}
    left = Variant("a", lambda context: {"action": "HOLD"})
    right = Variant("b", lambda context: {"action": "HOLD" if context.case.case_id == "case-1" else "BUY"})
    report = compare_variants(dataset, left, right, bundle=bundle, snapshot=snapshot, kind="model")
    assert [item.bundle_id for item in report.results_a] == ["b-1", "b-2"]
    assert report.results_a[0].metrics["decision_stability"] == 1.0
    assert report.results_a[1].metrics["decision_stability"] == 0.0


def test_runner_stability_reads_nested_final_decision_payload():
    bundle, snapshot = fixed_inputs()
    dataset = EvalDataset(cases=[case()])
    left = Variant("buy", lambda _: {"final_decision": {"decision_action": "APPROVE", "investment_action": "BUY", "target_weight": 0.2}})
    right = Variant("veto", lambda _: {"final_decision": {"decision_action": "VETO", "investment_action": "HOLD"}})
    report = compare_variants(dataset, left, right, bundle=bundle, snapshot=snapshot, kind="model")
    assert report.results_a[0].metrics["decision_stability"] == 0.0
    assert report.results_b[0].metrics["decision_stability"] == 0.0


def test_package_discovery_includes_evals():
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert "evals*" in config["tool"]["setuptools"]["packages"]["find"]["include"]


def test_runner_rejects_changed_bundle_and_policy_gets_same_proposal():
    bundle, snapshot = fixed_inputs()
    proposal = {"proposal_id": "p-1", "proposal_hash": "p" * 64, "action": "BUY"}
    dataset = EvalDataset(cases=[case()])
    changed = Variant("changed", lambda context: {"bundle": {"bundle_id": "b-2", "bundle_hash": "x" * 64}})
    result = EvalRunner(dataset).evaluate_case(dataset.cases[0], changed, bundle=bundle, snapshot=snapshot)
    assert result.status == "ERROR"
    assert result.errors[0].code == "EVAL_ADAPTER_ERROR"
    observed = []

    def policy(context):
        observed.append(context.proposal)
        return {"action": "HOLD"}

    report = compare_variants(dataset, Variant("p1", policy), Variant("p2", policy), bundle=bundle, snapshot=snapshot, proposal=proposal, kind="policy")
    assert observed[0] is proposal and observed[1] is proposal
    assert all(item.status == "OK" for item in report.results_a + report.results_b)


def test_runner_does_not_supply_network_or_latest_data_and_errors_are_visible():
    bundle, snapshot = fixed_inputs()
    dataset = EvalDataset(cases=[case()])
    called = False

    def forbidden(_context):
        nonlocal called
        called = True
        raise RuntimeError("network/latest access forbidden")

    result = EvalRunner(dataset).evaluate_case(dataset.cases[0], Variant("bad", forbidden), bundle=bundle, snapshot=snapshot)
    assert called is True
    assert result.status == "ERROR"
    assert "network/latest" in result.errors[0].message


def test_report_hash_and_baseline_are_deterministic():
    bundle, snapshot = fixed_inputs()
    dataset = EvalDataset(cases=[case()])
    result = EvalRunner(dataset).evaluate_case(dataset.cases[0], Variant("v", lambda _: {"action": "HOLD"}), bundle=bundle, snapshot=snapshot)
    report = EvaluationReport.from_results([result])
    assert report.to_json() == report.to_json()
    assert len(report.report_hash) == 64
    baseline = Baseline("b1", {"evidence_recall": 1.0})
    assert compare_to_baseline({"evidence_recall": 0.9}, baseline)[0].metric == "evidence_recall"
    lower_baseline = Baseline("b2", {"brier_score": 0.1})
    assert compare_to_baseline({"brier_score": 0.2}, lower_baseline)[0].direction == "lower"
