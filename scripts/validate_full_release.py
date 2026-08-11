"""Release gate orchestrator. It reports missing external infrastructure as SKIP,
which is deliberately non-success for required gates."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
from financial_agent.utils import project_root

REQUIRED = ("HistoricalFeatures", "FactorStatistics", "BacktestTimeline", "MultiAgent", "Execution", "Streaming", "SkillEvolution", "StrategyFactory", "RepositoryRegression")

def _pytest_gate(name: str, tests: list[str]) -> dict:
    completed = subprocess.run([sys.executable, "-m", "pytest", *tests, "-q", "--basetemp", ".pytest_tmp_release"], cwd=project_root(), capture_output=True, text=True, timeout=180)
    return {"gate": name, "status": "PASS" if completed.returncode == 0 else "FAIL", "metrics": {}, "artifacts": [], "errors": [] if completed.returncode == 0 else [completed.stdout[-3000:] + completed.stderr[-1000:]]}

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--all", action="store_true"); parser.add_argument("--output", default="artifacts/release_validation"); args = parser.parse_args()
    gates = [
        _pytest_gate("HistoricalFeatures", ["tests/test_market_feature_history.py"]),
        _pytest_gate("FactorStatistics", ["tests/test_factor_statistical_validation.py"]),
        _pytest_gate("BacktestTimeline", ["tests/test_backtest_execution_model_v2.py"]),
        _pytest_gate("MultiAgent", ["tests/test_p2_automation.py"]),
        _pytest_gate("Execution", ["tests/test_p2_automation.py"]),
        _pytest_gate("Streaming", ["tests/test_p2_automation.py"]),
        _pytest_gate("SkillEvolution", ["tests/test_p2_automation.py"]),
        _pytest_gate("StrategyFactory", ["tests/test_p2_automation.py"]),
    ]
    if args.all:
        # A release gate must execute the entire repository suite, not a
        # hand-picked smoke subset.
        gates.append(_pytest_gate("FullRegression", ["-q"]))
    report = {"required_gates": REQUIRED, "gates": gates, "overall": "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"}
    output = project_root() / args.output; output.mkdir(parents=True, exist_ok=True)
    (output / "release_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Release validation", "", f"Overall: **{report['overall']}**", ""] + [f"- {item['gate']}: {item['status']}" for item in gates]
    (output / "release_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if report["overall"] == "PASS" else 1

if __name__ == "__main__": raise SystemExit(main())
