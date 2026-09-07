from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/contracts/verify_content_knowledge_bundle.py"


@pytest.fixture(scope="module")
def verifier():
    spec = importlib.util.spec_from_file_location("verify_content_knowledge_bundle", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema() -> dict:
    return json.loads((ROOT / "contracts/fixtures/content-knowledge-bundle.v1.json").read_text(encoding="utf-8"))


def _producer_fixture(tmp_path: Path) -> Path:
    producer = tmp_path / "stock_content"
    (producer / "contracts/fixtures").mkdir(parents=True)
    shutil.copy2(ROOT / "contracts/fixtures/content-knowledge-bundle.v1.json", producer / "contracts/content-knowledge-bundle.v1.json")
    shutil.copy2(ROOT / "contracts/fixtures/content-knowledge-bundle.c14n-v1.json", producer / "contracts/fixtures/content-knowledge-bundle.c14n-v1.json")
    (producer / "contracts/platform-manifest.yaml").write_text(
        "contracts:\n  - id: content-knowledge-bundle.v1\n    schema: contracts/content-knowledge-bundle.v1.json\n"
        "    checksum: sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621\n"
        "    producer: stock_content\n    owner: content-platform\n",
        encoding="utf-8",
    )
    return producer


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda schema: schema["required"].remove("query"), "REQUIRED_FIELD_REMOVED"),
        (lambda schema: schema["properties"]["query"].update({"type": "integer"}), "TYPE_CHANGED"),
        (lambda schema: schema["properties"].update({"status": {"enum": ["A"]}}), "HASH_MATERIAL_ADDITION"),
    ],
)
def test_breaking_schema_changes_require_v2(verifier, mutate, code) -> None:
    baseline, candidate = _schema(), _schema()
    mutate(candidate)
    codes = verifier.compatibility_codes(baseline, candidate, c14n_changed=False)
    assert code in codes
    assert "V2_REQUIRED" in codes


def test_enum_evidence_pit_and_no_evidence_changes_are_classified(verifier) -> None:
    baseline = {
        "properties": {
            "status": {"enum": ["A", "B"]}, "availability_as_of": {"type": "string"},
            "items": {"type": "array", "items": {"properties": {"evidence": {"type": "array", "minItems": 1}, "ownership": {"type": "string"}}}},
        }, "required": ["availability_as_of"],
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["properties"]["status"]["enum"] = ["A"]
    candidate["required"] = []
    candidate["properties"]["items"]["items"]["properties"]["evidence"].pop("minItems")
    candidate["properties"]["items"]["items"]["properties"]["ownership"]["type"] = "integer"
    codes = verifier.compatibility_codes(baseline, candidate, c14n_changed=True)
    assert {"ENUM_NARROWED", "EVIDENCE_OWNERSHIP_CHANGED", "NO_EVIDENCE_ALLOWED", "SNAPSHOT_PIT_AS_OF_CHANGED", "CANONICALIZATION_OR_HASH_MATERIAL_CHANGED", "V2_REQUIRED"} <= codes


def test_only_versioned_excluded_diagnostic_metadata_is_compatible(verifier) -> None:
    baseline, candidate = _schema(), _schema()
    candidate["properties"]["diagnostic"] = {
        "type": "string", "default": "", "x-hash-material": False,
        "x-diagnostic-contract": "content-bundle-diagnostics.v1",
    }
    codes = verifier.compatibility_codes(baseline, candidate, c14n_changed=False)
    assert codes == {"COMPATIBLE_DIAGNOSTIC_METADATA_ADDED"}


@pytest.mark.parametrize(
    ("target", "mutate", "expected"),
    [
        ("schema", lambda path: path.write_bytes(path.read_bytes() + b"\n"), "CONSUMER_SCHEMA_LOCK_MISMATCH"),
        ("c14n", lambda path: path.write_bytes(path.read_bytes() + b"\n"), "C14N_FIXTURE_MISMATCH"),
        ("manifest", lambda path: path.write_text(path.read_text(encoding="utf-8").replace("content-platform", "other"), encoding="utf-8"), "PRODUCER_MANIFEST_OWNERSHIP_INVALID"),
    ],
)
def test_verifier_detects_schema_fixture_and_manifest_tamper(verifier, tmp_path, target, mutate, expected) -> None:
    producer = _producer_fixture(tmp_path)
    target_path = {
        "schema": producer / "contracts/content-knowledge-bundle.v1.json",
        "c14n": producer / "contracts/fixtures/content-knowledge-bundle.c14n-v1.json",
        "manifest": producer / "contracts/platform-manifest.yaml",
    }[target]
    mutate(target_path)
    report = verifier.verify(producer_root=producer, expected_producer_sha="a" * 40,
                             expected_checksum="sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621",
                             consumer_sha=None, strict_ref=False)
    assert report["result"] == "FAIL"
    assert expected in report["reason_codes"]


def test_working_tree_report_is_deterministic_and_redacted(verifier) -> None:
    producer = Path(r"D:\project\worktrees\stock_content-EPIC-043")
    expected_sha, _ = verifier._git_state(producer)
    assert expected_sha
    first = verifier.verify(producer_root=producer, expected_producer_sha=expected_sha,
                            expected_checksum="sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621",
                            consumer_sha=None, strict_ref=False)
    second = verifier.verify(producer_root=producer, expected_producer_sha=expected_sha,
                             expected_checksum="sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621",
                             consumer_sha=None, strict_ref=False)
    assert first == second
    assert first["exact_ref_gate"] == "PENDING_UNCOMMITTED"
    assert str(producer) not in json.dumps(first)
    assert first["producer"]["actual_checksum"] == first["producer"]["checksum"]


def test_strict_ref_rejects_dirty_or_mismatched_checkout(verifier, monkeypatch) -> None:
    actual_sha = "a" * 40
    monkeypatch.setattr(verifier, "_git_state", lambda root: (actual_sha, True))
    report = verifier.verify(producer_root=ROOT, expected_producer_sha="b" * 40,
                             expected_checksum="sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621",
                             consumer_sha=actual_sha, strict_ref=True)
    assert report["exact_ref_gate"] == "FAIL"
    assert {"PRODUCER_EXACT_REF_GATE_FAILED", "CONSUMER_EXACT_REF_GATE_FAILED"} <= set(report["reason_codes"])


def test_workflow_is_dedicated_and_has_no_unrelated_gates() -> None:
    workflow = (ROOT / ".github/workflows/content-knowledge-contract.yml").read_text(encoding="utf-8")
    assert "STOCK_CONTENT_COMPAT_SHA" in workflow
    assert "verify_content_knowledge_bundle.py" in workflow
    assert "verify_manifest.py" not in workflow
    assert "quant" not in workflow.casefold() and "stock_factor" not in workflow.casefold()
    assert "needs:" not in workflow
