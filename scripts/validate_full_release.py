"""Release gate registry. Required external gates must explicitly pass."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from financial_agent.utils import project_root

GATE_REGISTRY: dict[str, dict] = {
    "ConfigContract": {"command": [sys.executable, "scripts/check_skill_contracts.py"]},
    "UnitRegression": {"pytest": ["tests/test_api_routers.py", "tests/test_migrations.py"]},
    "HistoricalData": {"external_env": "RELEASE_HISTORICAL_DATA_CMD"},
    "DataQuality": {"external_env": "RELEASE_DATA_QUALITY_CMD"},
    "Retrieval": {"pytest": ["tests/test_retrieval_evaluation_v2.py", "tests/test_hybrid_retriever.py"]},
    "RegimeOpportunityPortfolio": {"pytest": ["tests/test_portfolio_v2.py", "tests/test_decision_replay.py"]},
    "Factor": {"pytest": ["tests/test_factor_statistical_validation.py", "tests/test_factor_lifecycle_second_round.py"]},
    "Backtest": {"pytest": ["tests/test_backtest_execution.py", "tests/test_backtest_execution_model_v2.py"]},
    "MultiAgent": {"pytest": ["tests/test_p2_automation.py"]},
    "Execution": {"pytest": ["tests/test_remaining_closure.py"]},
    "Streaming": {"pytest": ["tests/test_p2_automation.py"]},
    "SkillEvolution": {"pytest": ["tests/test_skill_evolution_full_gate.py"]},
    "StrategyFactory": {"pytest": ["tests/test_strategy_factory_full_gate.py"]},
    "DecisionReplay": {"pytest": ["tests/test_decision_replay_multi_agent.py"]},
    "FullRegression": {"pytest": ["-q"]},
    "FullStack": {"external_env": "RELEASE_FULL_STACK_CMD"},
    "Kubernetes": {"external_env": "RELEASE_KUBERNETES_CMD"},
}
REQUIRED = tuple(GATE_REGISTRY)


def _run_gate(name: str, spec: dict) -> dict:
    if "external_env" in spec:
        command = os.getenv(spec["external_env"])
        if not command:
            return {"gate": name, "status": "SKIP", "metrics": {}, "artifacts": [], "errors": [f"set {spec['external_env']} to run this required gate"]}
        completed = subprocess.run(command, cwd=project_root(), shell=True, capture_output=True, text=True, timeout=1800)
    elif "command" in spec:
        completed = subprocess.run(spec["command"], cwd=project_root(), capture_output=True, text=True, timeout=180)
    else:
        completed = subprocess.run([sys.executable, "-m", "pytest", *spec["pytest"], "-q", "--basetemp", ".pytest_tmp_release"], cwd=project_root(), capture_output=True, text=True, timeout=1800 if name == "FullRegression" else 300)
    return {"gate": name, "status": "PASS" if completed.returncode == 0 else "FAIL", "metrics": {}, "artifacts": [], "errors": [] if completed.returncode == 0 else [(completed.stdout + completed.stderr)[-4000:]]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", default="artifacts/release_validation")
    args = parser.parse_args()
    selected = list(GATE_REGISTRY) if args.all else [name for name in GATE_REGISTRY if name not in {"FullRegression", "FullStack", "Kubernetes"}]
    gates = [_run_gate(name, GATE_REGISTRY[name]) for name in selected]
    executed = {item["gate"] for item in gates}
    missing = sorted(set(REQUIRED) - executed)
    overall = "PASS" if not missing and all(item["status"] == "PASS" for item in gates) else "FAIL"
    report = {"required_gates": REQUIRED, "executed_gates": sorted(executed), "missing_required_gates": missing, "gates": gates, "overall": overall}
    output = project_root() / args.output
    output.mkdir(parents=True, exist_ok=True)
    (output / "release_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Release validation", "", f"Overall: **{overall}**", ""] + [f"- {item['gate']}: {item['status']}" for item in gates]
    if missing:
        lines.extend(["", f"Missing required gates: {', '.join(missing)}"])
    (output / "release_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
