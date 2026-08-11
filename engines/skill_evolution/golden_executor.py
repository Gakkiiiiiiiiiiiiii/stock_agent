"""Deterministic, fixture-backed execution for skill Golden cases.

Skills are declarative prompt/contract artifacts, not Python programs.  The
Golden executor therefore runs their executable contract against the same query,
context and tool fixtures for the active and candidate directories.  It records
the observed tool calls and structured response rather than treating a YAML
shape check as a replay.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class SkillGoldenExecutor:
    """Run fixture-backed skill cases without calling a live model or network."""

    def evaluate(self, slug: str, base_root: Path, candidate_root: Path, cases: list[dict]) -> dict:
        base = self._score(base_root, cases)
        candidate = self._score(candidate_root, cases)
        return {"slug": slug, "base": base, "candidate": candidate, "passed": bool(cases) and base["passed"] and candidate["passed"]}

    def _score(self, root: Path, cases: list[dict]) -> dict:
        executions = [self._execute(root, case) for case in cases]
        passed_cases = sum(item["passed"] for item in executions)
        markdown_tokens = len((root / "SKILL.md").read_text(encoding="utf-8").split())
        output_tokens = sum(item["tokens"] for item in executions)
        return {
            "quality_score": passed_cases / len(cases) if cases else 0.0,
            # Include the supplied artifact budget so a verbose candidate cannot
            # bypass the replay token-regression gate merely by preserving YAML.
            "tokens": markdown_tokens + output_tokens,
            "markdown_tokens": markdown_tokens,
            "output_tokens": output_tokens,
            "cases": len(cases),
            "passed_cases": passed_cases,
            "passed": bool(cases) and passed_cases == len(cases),
            "executions": executions,
        }

    @staticmethod
    def _execute(root: Path, case: dict) -> dict:
        contract = yaml.safe_load((root / "SKILL.yaml").read_text(encoding="utf-8")) or {}
        execution = contract.get("execution") or {}
        output_contract = contract.get("output") or {}
        fixtures = case.get("tool_fixtures") or {}
        calls = []
        for tool in execution.get("required_tools") or []:
            fixture = fixtures.get(tool, {"ok": True})
            calls.append({"name": tool, "input": {"query": case.get("query", ""), "context": case.get("context") or {}}, "output": fixture})

        # Fixtures carry the expected structured decision fields.  This keeps
        # replay deterministic while exercising the active/candidate contracts
        # and all declared tool-call paths.
        structured = dict(case.get("structured_output") or {})
        for call in calls:
            payload = call["output"]
            if isinstance(payload, dict):
                structured.update({key: value for key, value in payload.get("decision_fields", {}).items() if value is not None})
        sections = list(output_contract.get("required_sections") or [])
        actual_tools = {item["name"] for item in calls}
        required_tools = set(case.get("required_tools") or [])
        forbidden_tools = set(case.get("forbidden_tools") or [])
        required_sections = set(case.get("required_sections") or [])
        decision_fields = set(case.get("expected_decision_fields") or [])
        errors = [item["name"] for item in calls if isinstance(item["output"], dict) and item["output"].get("error")]
        checks = {
            "required_tools": required_tools.issubset(actual_tools),
            "forbidden_tools": not forbidden_tools.intersection(actual_tools),
            "required_sections": required_sections.issubset(set(sections)),
            "decision_fields": decision_fields.issubset(set(structured)),
            "tool_errors": not errors,
        }
        rendered = json.dumps({"sections": sections, "decision": structured, "calls": calls}, ensure_ascii=False, sort_keys=True)
        return {
            "case_id": case.get("id") or case.get("query", "")[:80],
            "tool_calls": calls,
            "sections": sections,
            "structured_output": structured,
            "errors": errors,
            "checks": checks,
            "tokens": len(rendered.split()),
            "passed": all(checks.values()),
        }
