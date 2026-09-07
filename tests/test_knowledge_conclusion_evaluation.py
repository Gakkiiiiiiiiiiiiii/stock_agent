"""SA-10A synthetic-only checks for the offline Golden evaluator."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.application.knowledge_conclusion.evaluation import (
    THRESHOLDS,
    _file_hash,
    evaluate_manifest,
)
from app.application.knowledge_conclusion.prompt import SYSTEM_PROMPT
from app.application.knowledge_conclusion.run_service import canonical_hash


def _payload(*, verdict: str = "SUPPORTED", text: str = "2026年第一季度收入增长12%。", evidence_id: str = "ev-1") -> dict[str, object]:
    return {
        "verdict": verdict,
        "market_stance": "UNCERTAIN",
        "findings": [{"text": text, "knowledge_ids": ["ko-1"], "evidence_ids": [evidence_id], "confidence": 0.8}],
        "summary_index": 0,
    }


def _case(*, expected: str = "SUPPORTED", injection: bool = False, model: dict[str, object] | None = None, fallback: dict[str, object] | None = None, secret: str = "") -> dict[str, object]:
    bundle = {"knowledge": [{"knowledge_id": "ko-1", "text": "2026年第一季度收入增长12%。 " + secret, "evidence": [{"evidence_id": "ev-1", "knowledge_id": "ko-1", "text": "2026年第一季度收入增长12%。 " + secret, "author": "synthetic"}]}]}
    annotation = {"verdict": expected}
    return {
        "case_id": "synthetic-oos-1",
        "strata": {"source": "synthetic", "subtitle_asr": True, "numeric_time": True, "conflict": False, "insufficient_evidence": expected == "INSUFFICIENT_EVIDENCE", "prompt_injection": injection},
        "bundle": bundle,
        "bundle_sha256": canonical_hash(bundle),
        "annotation": annotation,
        "annotation_sha256": canonical_hash(annotation),
        "outputs": {"MODEL": model or _payload(), "FALLBACK": fallback or _payload()},
    }


def _lineage() -> dict[str, dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    return {
        "bundle_fixture": {"version": "synthetic-v1", "sha256": "1" * 64},
        "annotation": {"version": "synthetic-v1", "sha256": "2" * 64},
        "prompt": {"version": "knowledge-conclusion.prompt.v1", "sha256": canonical_hash(SYSTEM_PROMPT)},
        "model": {"version": "synthetic-model-v1", "sha256": "3" * 64},
        "thresholds": {"version": "SA-E2E-P1-03", "sha256": canonical_hash(THRESHOLDS)},
        "fallback_policy": {"version": "deterministic-grounding-v1", "sha256": _file_hash(root / "app" / "application" / "knowledge_conclusion" / "deterministic_fallback.py")},
        "producer_contract": {"version": "content-bundle-v2", "sha256": "4" * 64},
        "consumer_contract": {"version": "knowledge-conclusion.v1", "sha256": _file_hash(root / "contracts" / "knowledge-conclusion.v1.json")},
    }


def _write_manifest(tmp_path: Path, case: dict[str, object], *, train: list[dict[str, str]] | None = None, frozen: bool = True) -> Path:
    cases = tmp_path / "cases"
    cases.mkdir(parents=True)
    case_path = cases / "synthetic.json"
    case_path.write_text(json.dumps(case, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    ref = {"case_id": str(case["case_id"]), "path": "cases/synthetic.json", "sha256": hashlib.sha256(case_path.read_bytes()).hexdigest()}
    manifest = {
        "schema_version": "knowledge-conclusion-golden.v1",
        "partitions": {"train": train or [], "dev": [], "frozen_oos": [ref] if frozen else []},
        "thresholds": THRESHOLDS,
        "lineage": _lineage(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def test_synthetic_frozen_oos_passes_and_report_is_deterministic(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        _case(
            expected="INSUFFICIENT_EVIDENCE",
            injection=True,
            model=_payload(verdict="INSUFFICIENT_EVIDENCE"),
            fallback=_payload(verdict="INSUFFICIENT_EVIDENCE"),
        ),
    )
    first = evaluate_manifest(path)
    second = evaluate_manifest(path)
    assert first.state == "PASS" and first.exit_code == 0
    assert first.report == second.report
    assert first.report["evaluated_partition"] == "frozen_oos"


def test_shipped_empty_manifest_is_honestly_blocked() -> None:
    root = Path(__file__).resolve().parents[1]
    result = evaluate_manifest(root / "benchmarks" / "knowledge_conclusion" / "manifest.json")
    assert result.state == "BLOCKED" and result.reasons == ("MISSING_FROZEN_OOS",) and result.exit_code != 0


def test_tamper_and_split_leak_fail_closed(tmp_path: Path) -> None:
    case = _case()
    path = _write_manifest(tmp_path, case)
    case_path = tmp_path / "cases" / "synthetic.json"
    case_path.write_text(case_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert "CASE_TAMPERED" in evaluate_manifest(path).reasons

    path = _write_manifest(tmp_path / "leak", case)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["partitions"]["train"] = list(manifest["partitions"]["frozen_oos"])
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert "SPLIT_LEAK" in evaluate_manifest(path).reasons

    path = _write_manifest(tmp_path / "missing-hash", case)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["lineage"]["model"]["sha256"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert "LINEAGE_MISSING" in evaluate_manifest(path).reasons


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        (_case(model=_payload(evidence_id="missing")), "THRESHOLD_CITATION_PRECISION"),
        (_case(model=_payload(text="2027年第一季度收入增长12%。")), "THRESHOLD_UNSUPPORTED_CLAIM_RATE"),
        (_case(expected="CONTRADICTED"), "THRESHOLD_VERDICT_ACCURACY"),
        (_case(expected="INSUFFICIENT_EVIDENCE"), "THRESHOLD_INSUFFICIENT_EVIDENCE_RECALL"),
        (_case(model=_payload(text="2027年第一季度收入增长12%。")), "THRESHOLD_HARD_FACT_GROUNDING"),
        (_case(injection=True, model=_payload(text="2027年第一季度收入增长12%。")), "THRESHOLD_PROMPT_INJECTION_PASS"),
    ],
)
def test_each_design_threshold_fails_closed(tmp_path: Path, case: dict[str, object], reason: str) -> None:
    result = evaluate_manifest(_write_manifest(tmp_path, case))
    assert result.state == "FAIL" and result.exit_code != 0 and reason in result.reasons


def test_model_cannot_regress_against_fallback_and_report_redacts_fixture_secret(tmp_path: Path) -> None:
    secret = "PROTECTED_XIAOE_RAW_SECRET_DO_NOT_REPORT"
    case = _case(
        injection=True,
        secret=secret,
        model=_payload(text="2027年第一季度收入增长12%。"),
        fallback=_payload(),
    )
    result = evaluate_manifest(_write_manifest(tmp_path, case))
    rendered = json.dumps(result.report, ensure_ascii=False)
    assert "MODEL_REGRESSION_HARD_FACT_GROUNDING" in result.reasons
    assert "MODEL_REGRESSION_CITATION_PRECISION" in result.reasons
    assert "MODEL_REGRESSION_PROMPT_INJECTION_PASS" in result.reasons
    assert secret not in rendered
