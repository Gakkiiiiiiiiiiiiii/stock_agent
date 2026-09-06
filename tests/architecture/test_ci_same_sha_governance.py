"""Static coverage for same-SHA CI and auditable branch-governance declarations."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATES = ROOT / ".github" / "workflows" / "quality-gates.yml"


def _workflow() -> dict:
    return yaml.safe_load(QUALITY_GATES.read_text(encoding="utf-8"))


def _step_commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_blocking_jobs_verify_the_event_sha_against_a_commit_bound_artifact() -> None:
    jobs = _workflow()["jobs"]
    blocking = (
        "lint",
        "unit-tests",
        "architecture",
        "contract",
        "decision-replay",
        "safety-closure",
        "postgres-safety-evidence",
        "integration",
        "docker-build",
    )
    for name in blocking:
        job = jobs[name]
        assert "ci-provenance" in job["needs"]
        assert any(
            step.get("uses") == "actions/download-artifact@v4"
            and step.get("with", {}).get("name")
            == "ci-provenance-${{ needs.ci-provenance.outputs.commit_sha }}"
            for step in job["steps"]
        )
        assert "verify_same_sha.py verify" in _step_commands(job)
        assert "${{ github.sha }}" in _step_commands(job)


def test_release_gate_requires_and_rechecks_all_blocking_contexts_at_the_event_sha() -> (
    None
):
    jobs = _workflow()["jobs"]
    release_gate = jobs["release-gate"]
    blocking = {
        "lint",
        "unit-tests",
        "architecture",
        "contract",
        "decision-replay",
        "safety-closure",
        "postgres-safety-evidence",
        "integration",
        "docker-build",
    }
    assert blocking | {"ci-provenance"} == set(release_gate["needs"])
    assert "verify_same_sha.py verify" in _step_commands(release_gate)
    assert "${{ github.sha }}" in _step_commands(release_gate)


def test_governance_declarations_are_owned_and_external_protection_is_report_only() -> (
    None
):
    owners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert ".github/workflows/" in owners
    source = (ROOT / "scripts" / "check_branch_protection.py").read_text(
        encoding="utf-8"
    )
    assert '"release-gate"' in source
    assert '"mutation_performed": False' in source
    assert '"status": "PASS" if not missing else "REPORT_ONLY"' in source
