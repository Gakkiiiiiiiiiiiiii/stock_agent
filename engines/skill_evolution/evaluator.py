from __future__ import annotations
import subprocess, sys
from pathlib import Path
from financial_agent.utils import project_root

def run_contract_lint(skill_root: str | Path | None = None) -> dict:
    command = [sys.executable, "-m", "scripts.check_skill_contracts"]
    if skill_root is not None:
        command.append(str(skill_root))
    completed = subprocess.run(command, cwd=project_root(), capture_output=True, text=True, timeout=120)
    return {"passed": completed.returncode == 0, "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:]}


def run_candidate_tests() -> dict:
    """Run deterministic contract tests; candidate skills contain no code."""
    completed = subprocess.run([sys.executable, "-m", "pytest", "tests/test_skill_contract_linter.py", "-q", "--basetemp", ".pytest_tmp_skill_candidate"], cwd=project_root(), capture_output=True, text=True, timeout=120)
    return {"passed": completed.returncode == 0, "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:]}

def compare_replay(base: dict, candidate: dict, max_token_ratio: float = 1.1) -> dict:
    quality = float(candidate.get("quality_score", 0)) >= float(base.get("quality_score", 0))
    token = not base.get("tokens") or float(candidate.get("tokens", 0)) <= float(base["tokens"]) * max_token_ratio
    return {"passed": quality and token, "quality_ok": quality, "token_ok": token, "base": base, "candidate": candidate}
