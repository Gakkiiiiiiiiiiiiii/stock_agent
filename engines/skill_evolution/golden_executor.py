"""Deterministic, fixture-backed execution for skill Golden cases.

Skills are declarative prompt/contract artifacts, not Python programs.  The
Golden executor therefore runs their executable contract against the same query,
context and tool fixtures for the active and candidate directories.  It records
the observed tool calls and structured response rather than treating a YAML
shape check as a replay.

Skill v3 cases are evaluated from evidence, specialist and governance records.
They deliberately do not name or simulate model tools; v2 cases retain the
legacy tool-fixture path for historical comparisons.
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
        if int(contract.get("version", 1)) >= 3:
            return SkillGoldenExecutor._execute_v3(contract, output_contract, case)
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

    @staticmethod
    def _execute_v3(contract: dict, output_contract: dict, case: dict) -> dict:
        """Evaluate a v3 contract from deterministic domain execution records."""

        structured = dict(case.get("structured_output") or {})
        required_evidence = set(contract.get("required_evidence") or [])
        required_specialists = {str(item).upper() for item in contract.get("required_specialists") or []}

        evidence_rows = case.get("evidence") or []
        observed_evidence: set[str] = set()
        execution_records: list[dict[str, Any]] = []
        for item in evidence_rows:
            if isinstance(item, str):
                evidence_type, status, payload = item, "VERIFIED", {}
            else:
                item = dict(item)
                evidence_type = str(item.get("evidence_type") or item.get("type") or "")
                status = str(item.get("quality_status") or item.get("status") or "VERIFIED").upper()
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if evidence_type and status not in {"REJECTED", "STALE"}:
                observed_evidence.add(evidence_type)
            execution_records.append({"kind": "evidence", "reference": evidence_type, "status": status})
            if isinstance(payload.get("decision_fields"), dict):
                structured.update(payload["decision_fields"])

        specialist_rows = case.get("specialists") or []
        observed_specialists: set[str] = set()
        for item in specialist_rows:
            if isinstance(item, str):
                role, status = item.upper(), "SUCCESS"
            else:
                item = dict(item)
                role = str(item.get("specialist") or item.get("role") or "").upper()
                status = str(item.get("status") or "SUCCESS").upper().split(".")[-1]
            if role and status not in {"FAILED", "BLOCKED", "DEGRADED"}:
                observed_specialists.add(role)
            execution_records.append({"kind": "specialist", "reference": role, "status": status})

        governance = case.get("governance") or {}
        risk_ok = SkillGoldenExecutor._governance_record_ok(governance.get("risk"))
        policy_ok = SkillGoldenExecutor._governance_record_ok(governance.get("policy"))
        execution_records.extend(
            [
                {"kind": "governance", "reference": "risk", "status": "VALIDATED" if risk_ok else "MISSING"},
                {"kind": "governance", "reference": "policy", "status": "VALIDATED" if policy_ok else "MISSING"},
            ]
        )

        freshness_checks = SkillGoldenExecutor._check_v3_freshness(contract.get("freshness") or {}, case.get("freshness") or {})
        sections = list(output_contract.get("required_sections") or [])
        required_sections = set(case.get("required_sections") or [])
        decision_fields = set(case.get("expected_decision_fields") or [])
        errors = list(case.get("errors") or [])
        governance_contract = contract.get("governance") or {}
        checks = {
            "required_evidence": required_evidence.issubset(observed_evidence),
            "required_specialists": required_specialists.issubset(observed_specialists),
            "risk_governance": not governance_contract.get("require_risk", False) or risk_ok,
            "policy_governance": not governance_contract.get("require_policy", False) or policy_ok,
            "freshness": not freshness_checks,
            "required_sections": required_sections.issubset(set(sections)),
            "decision_fields": decision_fields.issubset(set(structured)),
            "execution_errors": not errors,
        }
        rendered = json.dumps(
            {"sections": sections, "decision": structured, "execution_records": execution_records},
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "case_id": case.get("id") or case.get("query", "")[:80],
            "tool_calls": [],
            "execution_records": execution_records,
            "evidence": sorted(observed_evidence),
            "specialists": sorted(observed_specialists),
            "governance": {"risk": risk_ok, "policy": policy_ok},
            "freshness_errors": freshness_checks,
            "sections": sections,
            "structured_output": structured,
            "errors": errors,
            "checks": checks,
            "tokens": len(rendered.split()),
            "passed": all(checks.values()),
        }

    @staticmethod
    def _governance_record_ok(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            return False
        status = str(value.get("status") or "").upper()
        return status in {"VALIDATED", "OK", "PASS", "PASSED", "SUCCESS"} and not bool(value.get("veto", False))

    @staticmethod
    def _check_v3_freshness(contract: dict, observed: dict) -> list[str]:
        errors: list[str] = []
        for domain, policy in contract.items():
            policy = policy or {}
            value = observed.get(domain) or {}
            if policy.get("max_age_minutes") is not None:
                age = value.get("age_minutes")
                if age is None:
                    errors.append(f"{domain}:AGE_MISSING")
                elif float(age) > float(policy["max_age_minutes"]):
                    errors.append(f"{domain}:STALE")
            if policy.get("require_same_trading_day") and value.get("same_trading_day") is not True:
                errors.append(f"{domain}:NOT_SAME_TRADING_DAY")
            if policy.get("require_after_market_open") and value.get("after_market_open") is not True:
                errors.append(f"{domain}:BEFORE_MARKET_OPEN")
        return errors
