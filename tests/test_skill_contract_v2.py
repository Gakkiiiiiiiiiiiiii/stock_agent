from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.skill_contract import (
    ConditionalRequirement,
    SkillContractValidator,
    SkillExecutionContract,
    SkillExecutionState,
    SkillOutputContract,
)
from app.skill_loader import SkillDefinition, load_skills


def make_skill(execution: SkillExecutionContract, sections: list[str] | None = None) -> SkillDefinition:
    return SkillDefinition(
        slug="v2-test",
        name="v2-test",
        description="",
        content="",
        execution=execution,
        output=SkillOutputContract(required_sections=sections or []),
    )


def validate(skill: SkillDefinition, state: SkillExecutionState, final_text: str = "") -> list[str]:
    return SkillContractValidator().validate(skill, state, final_text)


def test_required_any_fails_when_no_group_tool_succeeds():
    skill = make_skill(SkillExecutionContract(required_any={"recent_context": ["search_video_insights", "retrieve_relevant_context"]}))
    state = SkillExecutionState(skill_slug=skill.slug)
    violations = validate(skill, state)
    assert any(v.startswith("MISSING_REQUIRED_ANY:recent_context") for v in violations)


def test_required_any_passes_when_one_group_tool_succeeds():
    skill = make_skill(SkillExecutionContract(required_any={"recent_context": ["search_video_insights", "retrieve_relevant_context"]}))
    state = SkillExecutionState(skill_slug=skill.slug)
    state.record_tool_result("retrieve_relevant_context", {"contexts": []})
    assert validate(skill, state) == []


def test_required_any_ignores_failed_calls():
    skill = make_skill(SkillExecutionContract(required_any={"recent_context": ["search_video_insights", "retrieve_relevant_context"]}))
    state = SkillExecutionState(skill_slug=skill.slug)
    state.record_tool_result("search_video_insights", {"error": {"code": "UNAVAILABLE"}})
    violations = validate(skill, state)
    assert any(v.startswith("MISSING_REQUIRED_ANY:recent_context") for v in violations)


def test_conditional_requirements_untriggered_by_default():
    skill = make_skill(
        SkillExecutionContract(
            conditional_requirements=[ConditionalRequirement(when="intraday", require=["get_market_snapshot"], require_any=["get_kline", "get_sector_strength"])]
        )
    )
    state = SkillExecutionState(skill_slug=skill.slug)
    assert validate(skill, state) == []


def test_conditional_requirements_triggered_by_query_flag():
    skill = make_skill(
        SkillExecutionContract(
            conditional_requirements=[ConditionalRequirement(when="intraday", require=["get_market_snapshot"], require_any=["get_kline", "get_sector_strength"])]
        )
    )
    state = SkillExecutionState(skill_slug=skill.slug, query_flags={"intraday": True})
    violations = validate(skill, state)
    assert "MISSING_CONDITIONAL_TOOL:intraday:get_market_snapshot" in violations
    assert any(v.startswith("MISSING_CONDITIONAL_ANY:intraday") for v in violations)

    state.record_tool_result("get_market_snapshot", {"snapshot": {"as_of": "2026-08-07T09:30:00+08:00"}})
    state.record_tool_result("get_kline", {"records": []})
    assert validate(skill, state) == []


def test_existing_violation_codes_still_work():
    skill = make_skill(
        SkillExecutionContract(required_tools=["get_market_regime"], forbidden_tools=["walk_forward_validate"], min_tool_rounds=2),
        sections=["结论"],
    )
    state = SkillExecutionState(skill_slug=skill.slug, round_index=1)
    state.record_tool_result("walk_forward_validate", {"ok": True})
    violations = validate(skill, state, "no sections here")
    assert "MISSING_REQUIRED_TOOL:get_market_regime" in violations
    assert any(v.startswith("Forbidden tools were called") for v in violations)
    assert "At least 2 tool rounds are required." in violations
    assert "Final report is missing required section: 结论." in violations


def write_skill(root: Path, slug: str, yaml_text: str, md_text: str = "# body\n") -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.yaml").write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(md_text, encoding="utf-8")
    return skill_dir


def test_version_parsing(tmp_path):
    write_skill(tmp_path, "s1", "version: 2\nslug: s1\nname: S1\n")
    write_skill(tmp_path, "s2", "slug: s2\nname: S2\n")
    skills = {skill.slug: skill for skill in load_skills(tmp_path)}
    assert skills["s1"].version == 2
    assert skills["s2"].version == 1  # backward-compatible default during transition


def test_hash_stability_and_change_detection(tmp_path):
    yaml_text = "version: 2\nslug: s1\nname: S1\nexecution:\n  required_tools: [a, b]\n"
    write_skill(tmp_path, "s1", yaml_text)
    first = load_skills(tmp_path)[0]
    second = load_skills(tmp_path)[0]
    assert first.skill_contract_hash == second.skill_contract_hash
    assert len(first.skill_contract_hash) == 16

    # Key order in the source file does not change the canonical hash.
    (tmp_path / "s1" / "SKILL.yaml").write_text("version: 2\nname: S1\nslug: s1\nexecution:\n  required_tools: [a, b]\n", encoding="utf-8")
    assert load_skills(tmp_path)[0].skill_contract_hash == first.skill_contract_hash

    (tmp_path / "s1" / "SKILL.yaml").write_text(yaml_text.replace("[a, b]", "[a, b, c]"), encoding="utf-8")
    assert load_skills(tmp_path)[0].skill_contract_hash != first.skill_contract_hash

    (tmp_path / "s1" / "SKILL.md").write_text("# different body\n", encoding="utf-8")
    reloaded = load_skills(tmp_path)[0]
    assert reloaded.skill_markdown_hash != first.skill_markdown_hash
    assert len(reloaded.skill_markdown_hash) == 16


def test_real_skills_expose_version_and_hashes():
    skills = load_skills()
    assert skills, "expected the repository skills/ directory to be loaded"
    for skill in skills:
        assert skill.version >= 1
        assert skill.skill_contract_hash and len(skill.skill_contract_hash) == 16
        assert skill.skill_markdown_hash and len(skill.skill_markdown_hash) == 16


def test_decision_persists_skill_contract_identity(isolated_database):
    from engines.decision.decision_service import DecisionService

    service = DecisionService()
    saved = service.save_decision(
        query="q",
        skill_slug="daily-market-decision",
        skill_version=2,
        skill_contract_hash="0123456789abcdef",
        skill_markdown_hash="fedcba9876543210",
    )
    decision = service.get_decision(saved["decision_id"])["decision"]
    assert decision["skill_slug"] == "daily-market-decision"
    assert decision["skill_version"] == 2
    assert decision["skill_contract_hash"] == "0123456789abcdef"
    assert decision["skill_markdown_hash"] == "fedcba9876543210"


def test_skill_identity_proxy_injects_defaults_without_overriding_model_args():
    from app.claude_agent import _SkillIdentityToolProxy

    class FakeRegistry:
        def __init__(self):
            self.executed = []

        def openai_tools(self):
            return []

        def describe_tool(self, name):
            return name

        def execute(self, name, payload):
            self.executed.append((name, payload))
            return {"decision_id": "d1"}

    skill = SkillDefinition(slug="daily-market-decision", name="d", description="", version=2, skill_contract_hash="chash", skill_markdown_hash="mhash")
    registry = FakeRegistry()
    proxy = _SkillIdentityToolProxy(registry, skill)
    proxy.execute("save_investment_decision", {"query": "q"})
    proxy.execute("save_investment_decision", {"skill_version": 9})
    proxy.execute("get_market_snapshot", {})
    first, second, third = registry.executed
    assert first[1]["skill_slug"] == "daily-market-decision"
    assert first[1]["skill_version"] == 2
    assert first[1]["skill_contract_hash"] == "chash"
    assert first[1]["skill_markdown_hash"] == "mhash"
    assert second[1]["skill_version"] == 9  # model-supplied value wins
    assert third[1] == {}  # other tools untouched


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
