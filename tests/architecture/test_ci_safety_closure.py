"""Static and executable coverage for the release safety-closure selector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.ci.safety_closure import SCHEMA_VERSION, closure_manifest, selected_tests

ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATES = ROOT / ".github" / "workflows" / "quality-gates.yml"
SELECTOR = ROOT / "scripts" / "ci" / "safety_closure.py"


def test_safety_closure_has_a_nonempty_machine_verified_category_for_every_invariant() -> None:
    manifest = closure_manifest()
    categories = manifest["categories"]
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert set(categories) == {
        "formal_authority",
        "unit_of_work",
        "readiness",
        "replay",
        "outcome",
        "ci",
        "kubernetes",
    }
    assert all(categories.values())
    selected = selected_tests(manifest)
    assert "tests/test_replay_outcome_exactly_once.py" in selected
    assert "tests/test_formal_readiness.py" in selected
    assert "tests/test_kubernetes_runtime_layout.py" in selected


def test_safety_closure_verify_command_writes_the_discovered_test_manifest(tmp_path: Path) -> None:
    output = tmp_path / "safety-closure.json"
    result = subprocess.run(
        [sys.executable, str(SELECTOR), "verify", "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == closure_manifest()


def test_release_gate_waits_for_the_full_machine_verified_safety_closure() -> None:
    workflow = yaml.safe_load(QUALITY_GATES.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    closure = jobs["safety-closure"]
    commands = "\n".join(str(step.get("run", "")) for step in closure["steps"])
    assert "ci-provenance" in closure["needs"]
    assert "safety_closure.py verify" in commands
    assert "safety_closure.py run" in commands
    assert "not integration" in (ROOT / "scripts" / "ci" / "safety_closure.py").read_text(
        encoding="utf-8"
    )
    assert "safety-closure" in jobs["release-gate"]["needs"]


def test_release_gate_requires_non_skippable_postgres_safety_evidence() -> None:
    workflow = yaml.safe_load(QUALITY_GATES.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    postgres = jobs["postgres-safety-evidence"]
    assert postgres["needs"] == ["ci-provenance"]
    assert postgres["env"]["DATABASE_URL"] == (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/stock_agent"
    )
    service = postgres["services"]["postgres"]
    assert service["image"] == "postgres:16"
    assert service["ports"] == ["55432:5432"]
    assert service["env"] == {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "POSTGRES_DB": "stock_agent",
    }
    commands = "\n".join(str(step.get("run", "")) for step in postgres["steps"])
    assert "verify_same_sha.py verify" in commands
    assert "-m integration tests/test_postgres_safety_evidence.py" in commands
    assert "--junitxml=postgres-safety-evidence.xml" in commands
    assert "sys.exit(bool(skipped))" in commands
    artifact = postgres["steps"][-1]
    assert artifact["if"] == "always()"
    assert "postgres-safety-evidence.xml" in artifact["with"]["path"]
    assert "postgres-safety-environment.json" in artifact["with"]["path"]

    release_gate = jobs["release-gate"]
    assert release_gate["if"] == "always()"
    assert "postgres-safety-evidence" in release_gate["needs"]
    gate_step = release_gate["steps"][-1]
    assert gate_step["env"]["POSTGRES_SAFETY"] == "${{ needs.postgres-safety-evidence.result }}"
    assert 'test "$POSTGRES_SAFETY" = success' in gate_step["run"]
