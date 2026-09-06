from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_decision_docs_link_to_safe_smoke_and_preserve_evidence_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    smoke = ROOT / "docs" / "runbooks" / "formal-decision-smoke.md"
    degraded = ROOT / "docs" / "runbooks" / "formal-decision-degraded.md"
    replay = ROOT / "docs" / "runbooks" / "replay-mismatch.md"

    assert smoke.is_file()
    assert "docs/runbooks/formal-decision-smoke.md" in readme
    assert "SQLite and deterministic/fake dependencies" in readme
    assert "real PostgreSQL" in readme
    assert "BLOCKED" in smoke.read_text(encoding="utf-8")
    assert "ANALYSIS_ONLY" in degraded.read_text(encoding="utf-8")
    assert "EXACT_REPLAY" in replay.read_text(encoding="utf-8")
    assert (ROOT / "tests" / "smoke" / "test_deployed_decision_api.sh").is_file()
