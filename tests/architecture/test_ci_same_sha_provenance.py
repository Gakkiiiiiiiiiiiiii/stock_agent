"""Executable coverage for CI provenance's fail-closed commit binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "verify_same_sha.py"


def _head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def test_provenance_artifact_is_bound_to_github_sha_and_rejects_another_sha(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "ci-provenance.json"
    env = {
        **os.environ,
        "GITHUB_SHA": _head_sha(),
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    written = subprocess.run(
        [sys.executable, str(SCRIPT), "write", "--output", str(artifact)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    assert json.loads(artifact.read_text(encoding="utf-8")) == {
        "schema_version": "ci.provenance.v1",
        "commit_sha": env["GITHUB_SHA"],
        "github_run_id": "42",
        "github_run_attempt": "1",
    }

    accepted = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--artifact", str(artifact)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "verify",
            "--artifact",
            str(artifact),
            "--expected-sha",
            "0" * 40,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0

    prior_run = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--artifact", str(artifact)],
        cwd=ROOT,
        env={**env, "GITHUB_RUN_ID": "41"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert prior_run.returncode != 0
