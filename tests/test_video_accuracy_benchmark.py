"""P2-1/P2-2/P2-3 评测脚手架测试：schema 校验、benchmark 指标与门禁、calibration 已知值。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.video_accuracy import benchmark
from evaluation.video_accuracy.calibration import brier_score, expected_calibration_error, reliability_bins
from evaluation.video_accuracy.schema import AnnotationError, load_dataset, validate_sample

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evaluation" / "video_accuracy" / "sample_annotations.jsonl"
SYSTEM_EXPORT = ROOT / "evaluation" / "video_accuracy" / "fixtures" / "sample_system_export.json"


# ---------- schema ----------

def test_load_dataset_validates_synthetic_samples():
    samples = load_dataset(DATASET)
    assert len(samples) == 3
    assert all(sample["synthetic"] for sample in samples)


def test_validate_sample_missing_required_field():
    sample = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    del sample["claims"]
    with pytest.raises(AnnotationError, match="claims"):
        validate_sample(sample)


def test_validate_sample_rejects_bad_support_label():
    sample = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    sample["claims"][0]["support_label"] = "MAYBE"
    with pytest.raises(AnnotationError, match="support_label"):
        validate_sample(sample)


def test_validate_sample_rejects_non_bool_negation():
    sample = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    sample["claims"][0]["negation"] = "yes"
    with pytest.raises(AnnotationError, match="negation"):
        validate_sample(sample)


# ---------- benchmark ----------

def test_benchmark_synthetic_passes_gate(tmp_path):
    output = tmp_path / "report.json"
    exit_code = benchmark.main([
        "--dataset", str(DATASET),
        "--system", str(SYSTEM_EXPORT),
        "--output", str(output),
    ])
    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    metrics = report["metrics"]
    assert metrics["entity_accuracy"] == 1.0
    assert metrics["ticker_exact_match"] == 1.0
    assert metrics["numeric_exact_match"] == 1.0
    assert metrics["negation_accuracy"] == 1.0
    assert metrics["claim_precision"] == 1.0
    assert metrics["source_supported_precision"] == 1.0
    assert metrics["unsupported_claim_rate"] == 0.0
    assert metrics["critical_numeric_hallucination"] == 0.0
    assert metrics["critical_speaker_attribution_error"] == 0.0
    # 合成样本只有 5 对 (score,label) < 20 → ECE 不纳入门禁
    assert report["gate"]["checks"]["calibration_ece"]["status"] == "skipped"
    assert metrics["calibration"]["pairs"] == 5


def test_benchmark_gate_fails_on_wrong_number(tmp_path):
    payload = json.loads(SYSTEM_EXPORT.read_text(encoding="utf-8"))
    unit = payload["videos"][0]["units"][0]
    unit["statement"] = unit["statement"].replace("增长20%", "增长25%")
    unit["canonical_statement"] = unit["statement"]
    degraded = tmp_path / "degraded.json"
    degraded.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    exit_code = benchmark.main([
        "--dataset", str(DATASET),
        "--system", str(degraded),
        "--output", str(tmp_path / "report.json"),
    ])
    assert exit_code == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["metrics"]["numeric_exact_match"] == pytest.approx(4 / 5)
    # 同指标同单位但数值对不上 → critical numeric hallucination
    assert report["metrics"]["critical_numeric_hallucination"] == 1.0
    assert report["gate"]["checks"]["numeric_exact_match"]["status"] == "fail"


def test_benchmark_no_gate_returns_zero_on_failure(tmp_path):
    payload = json.loads(SYSTEM_EXPORT.read_text(encoding="utf-8"))
    payload["videos"][0]["units"] = []  # 实体/数字全丢
    degraded = tmp_path / "degraded.json"
    degraded.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    exit_code = benchmark.main([
        "--dataset", str(DATASET),
        "--system", str(degraded),
        "--no-gate",
    ])
    assert exit_code == 0


# ---------- calibration ----------

def test_brier_score_known_value():
    assert brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)
    assert brier_score([(0.0, 0), (1.0, 1)]) == pytest.approx(0.0)
    assert brier_score([]) == 0.0


def test_ece_known_value():
    # 单桶：mean_confidence 0.8，mean_accuracy 0.5 → ECE = 0.3
    assert expected_calibration_error([(0.8, 1), (0.8, 0)]) == pytest.approx(0.3)
    # 完美校准
    assert expected_calibration_error([(0.0, 0), (1.0, 1)]) == pytest.approx(0.0)


def test_reliability_bins():
    bins = reliability_bins([(0.05, 0), (0.95, 1), (0.9, 1)], n_bins=10)
    assert len(bins) == 2
    high = bins[-1]
    assert high["count"] == 2
    assert high["mean_confidence"] == pytest.approx(0.925)
    assert high["mean_accuracy"] == pytest.approx(1.0)


def test_calibration_rejects_out_of_range_score():
    with pytest.raises(ValueError):
        brier_score([(1.5, 1)])
