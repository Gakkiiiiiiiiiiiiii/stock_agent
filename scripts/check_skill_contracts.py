"""Skill contract linter (P0-02: SKILL.yaml is the only machine-executable truth).

Checks, per skills/<slug>/ directory:
  1. MISSING_YAML          - SKILL.yaml is missing.
  2. SLUG_MISMATCH         - yaml `slug` != directory name.
  3. UNKNOWN_REQUIRED_TOOL - execution.required_tools entry not in the tool registry.
  4. UNKNOWN_OPTIONAL_TOOL - execution.optional_tools entry not in the tool registry.
  5. DUPLICATE_TOOL        - a tool appears more than once across (or within)
                             required/optional/forbidden lists.
  6. EMPTY_REQUIRED_SECTIONS - output.required_sections is missing or empty.
  7. MISSING_VERSION / INVALID_VERSION - yaml `version` absent or not an int >= 1.
  8. MD_TOOL_LIST          - SKILL.md carries a machine-constraint tool list.

MD_TOOL_LIST heuristic (deterministic):
  a) any markdown heading whose text matches /必须调用|工具清单|required tools|optional tools/i;
  b) any bullet line ("- ..." / "* ...") that consists of nothing but backticked
     spans (plus whitespace/punctuation) where at least one backticked span is a
     known registry tool name. Backticked non-tool identifiers (e.g. memory source
     types) do not trigger this rule.

Exit code: 0 when no violations, 1 otherwise. Usage:

    python scripts/check_skill_contracts.py [skill_root]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from financial_agent.utils import project_root

MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$")
MD_FORBIDDEN_HEADING_RE = re.compile(r"必须调用|工具清单|required tools|optional tools", re.IGNORECASE)
MD_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
BACKTICK_SPAN_RE = re.compile(r"`([^`]+)`")
ALLOWED_REMAINDER_RE = re.compile(r"^[\s，,。.、;；:：()（）]*$")


@dataclass
class LintViolation:
    check: str
    skill: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.skill}: {self.detail}"


def _md_tool_list_violations(slug: str, body: str, known_tools: set[str]) -> list[LintViolation]:
    violations: list[LintViolation] = []
    for line in body.splitlines():
        heading = MD_HEADING_RE.match(line)
        if heading and MD_FORBIDDEN_HEADING_RE.search(heading.group(1)):
            violations.append(LintViolation("MD_TOOL_LIST", slug, f"machine-constraint heading: {heading.group(1)}"))
            continue
        bullet = MD_BULLET_RE.match(line)
        if not bullet:
            continue
        text = bullet.group(1)
        spans = BACKTICK_SPAN_RE.findall(text)
        if not spans:
            continue
        remainder = BACKTICK_SPAN_RE.sub("", text)
        if ALLOWED_REMAINDER_RE.match(remainder) and any(span in known_tools for span in spans):
            violations.append(LintViolation("MD_TOOL_LIST", slug, f"bullet enumerates tool names: {line.strip()}"))
    return violations


def lint_skill(skill_dir: Path, known_tools: set[str]) -> list[LintViolation]:
    slug = skill_dir.name
    violations: list[LintViolation] = []
    yaml_path = skill_dir / "SKILL.yaml"
    if not yaml_path.exists():
        return [LintViolation("MISSING_YAML", slug, "SKILL.yaml is missing")]
    contract = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    if contract.get("slug") != slug:
        violations.append(LintViolation("SLUG_MISMATCH", slug, f"yaml slug is {contract.get('slug')!r}"))

    version = contract.get("version")
    if version is None:
        violations.append(LintViolation("MISSING_VERSION", slug, "yaml `version` is required"))
    elif not isinstance(version, int) or isinstance(version, bool) or version < 1:
        violations.append(LintViolation("INVALID_VERSION", slug, f"version must be an int >= 1, got {version!r}"))

    execution = contract.get("execution") or {}
    tool_lists = {
        "required_tools": list(execution.get("required_tools") or []),
        "optional_tools": list(execution.get("optional_tools") or []),
        "forbidden_tools": list(execution.get("forbidden_tools") or []),
    }
    for name in tool_lists["required_tools"]:
        if name not in known_tools:
            violations.append(LintViolation("UNKNOWN_REQUIRED_TOOL", slug, f"unknown tool: {name}"))
    for name in tool_lists["optional_tools"]:
        if name not in known_tools:
            violations.append(LintViolation("UNKNOWN_OPTIONAL_TOOL", slug, f"unknown tool: {name}"))
    seen: dict[str, str] = {}
    for list_name, names in tool_lists.items():
        for name in names:
            if name in seen:
                violations.append(LintViolation("DUPLICATE_TOOL", slug, f"{name} in both {seen[name]} and {list_name}"))
            else:
                seen[name] = list_name

    output = contract.get("output") or {}
    if not output.get("required_sections"):
        violations.append(LintViolation("EMPTY_REQUIRED_SECTIONS", slug, "output.required_sections must be non-empty"))

    md_path = skill_dir / "SKILL.md"
    if md_path.exists():
        violations.extend(_md_tool_list_violations(slug, md_path.read_text(encoding="utf-8"), known_tools))
    return violations


def lint_skills(skill_root: Path, known_tools: set[str]) -> list[LintViolation]:
    if (skill_root / "SKILL.yaml").exists():
        return lint_skill(skill_root, known_tools)
    violations: list[LintViolation] = []
    for skill_dir in sorted(path for path in skill_root.iterdir() if path.is_dir()):
        violations.extend(lint_skill(skill_dir, known_tools))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    skill_root = Path(args[0]) if args else project_root() / "skills"
    from app.tool_registry import known_tool_names

    violations = lint_skills(skill_root, known_tool_names())
    if violations:
        print(f"Skill contract lint FAILED: {len(violations)} violation(s)")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print(f"Skill contract lint OK: {skill_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
