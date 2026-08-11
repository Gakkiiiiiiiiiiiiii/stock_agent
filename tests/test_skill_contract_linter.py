from __future__ import annotations

import textwrap
from pathlib import Path

from app.tool_registry import known_tool_names
from financial_agent.utils import project_root
from scripts.check_skill_contracts import lint_skills


def write_skill(root: Path, slug: str, yaml_text: str | None, md_text: str = "# ok\n") -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    if yaml_text is not None:
        (skill_dir / "SKILL.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(md_text, encoding="utf-8")


def check_ids(violations) -> set[str]:
    return {violation.check for violation in violations}


def test_real_skills_pass_linter():
    violations = lint_skills(project_root() / "skills", known_tool_names())
    assert violations == []


def test_drifted_skills_fail_with_expected_check_ids(tmp_path):
    write_skill(tmp_path, "no-yaml", None)
    write_skill(tmp_path, "wrong-slug", "version: 2\nslug: other-name\noutput:\n  required_sections: [结论]\n")
    write_skill(
        tmp_path,
        "unknown-tools",
        """\
        version: 2
        slug: unknown-tools
        execution:
          required_tools: [not_a_real_tool]
          optional_tools: [also_not_real]
        output:
          required_sections: [结论]
        """,
    )
    write_skill(
        tmp_path,
        "dup-tools",
        """\
        version: 2
        slug: dup-tools
        execution:
          required_tools: [get_market_snapshot]
          optional_tools: [get_market_snapshot]
        output:
          required_sections: [结论]
        """,
    )
    write_skill(tmp_path, "no-sections", "version: 2\nslug: no-sections\noutput:\n  required_sections: []\n")
    write_skill(tmp_path, "no-version", "slug: no-version\noutput:\n  required_sections: [结论]\n")
    write_skill(
        tmp_path,
        "md-drift",
        "version: 2\nslug: md-drift\noutput:\n  required_sections: [结论]\n",
        "# Skill\n\n## 必须调用的工具\n\n- `get_market_snapshot`\n",
    )
    violations = lint_skills(tmp_path, known_tool_names())
    assert check_ids(violations) == {
        "MISSING_YAML",
        "SLUG_MISMATCH",
        "UNKNOWN_REQUIRED_TOOL",
        "UNKNOWN_OPTIONAL_TOOL",
        "DUPLICATE_TOOL",
        "EMPTY_REQUIRED_SECTIONS",
        "MISSING_VERSION",
        "MD_TOOL_LIST",
    }
    md_violations = [v for v in violations if v.check == "MD_TOOL_LIST" and v.skill == "md-drift"]
    assert len(md_violations) == 2  # heading + bullet line


def test_clean_skill_passes(tmp_path):
    write_skill(
        tmp_path,
        "clean-skill",
        """\
        version: 2
        slug: clean-skill
        execution:
          required_tools: [get_market_snapshot]
          optional_tools: [get_kline]
        output:
          required_sections: [结论]
        """,
        "# Clean Skill\n\n方法论说明，不枚举工具清单。\n",
    )
    assert lint_skills(tmp_path, known_tool_names()) == []


def test_invalid_version_flagged(tmp_path):
    write_skill(tmp_path, "bad-version", "version: 0\nslug: bad-version\noutput:\n  required_sections: [结论]\n")
    violations = lint_skills(tmp_path, known_tool_names())
    assert check_ids(violations) == {"INVALID_VERSION"}
