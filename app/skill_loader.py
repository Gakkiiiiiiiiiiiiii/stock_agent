from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.skill_contract import SkillExecutionContract, SkillOutputContract

from financial_agent.utils import project_root


class SkillDefinition(BaseModel):
    slug: str
    name: str
    description: str
    content: str = ""
    version: int = 1
    skill_contract_hash: str | None = None
    skill_markdown_hash: str | None = None
    execution: SkillExecutionContract = Field(default_factory=SkillExecutionContract)
    output: SkillOutputContract = Field(default_factory=SkillOutputContract)

    @property
    def instructions(self) -> str:
        return self.content


class SkillDefinitionError(ValueError):
    pass


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    _, header, body = parts
    data: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data, body.strip()


def _contract_hash(contract: dict) -> str:
    canonical = yaml.safe_dump(contract, sort_keys=True, allow_unicode=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _markdown_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def load_skills(skill_root: Path | None = None) -> list[SkillDefinition]:
    root = skill_root or project_root() / "skills"
    skills: list[SkillDefinition] = []
    for path in sorted(root.glob("*/SKILL.md")):
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(raw)
        slug = path.parent.name
        contract_path = path.parent / "SKILL.yaml"
        if not contract_path.exists():
            raise SkillDefinitionError(f"Skill {slug} is missing required SKILL.yaml")
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        contract = contract or {}
        skills.append(
            SkillDefinition(
                slug=contract.get("slug", slug),
                name=contract.get("name", frontmatter.get("name", slug)),
                description=contract.get("description", frontmatter.get("description", "")),
                content=body,
                version=int(contract.get("version", 1)),
                skill_contract_hash=_contract_hash(contract),
                skill_markdown_hash=_markdown_hash(body),
                execution=contract.get("execution", {}),
                output=contract.get("output", {}),
            )
        )
    return skills


def format_skill_catalog(skills: list[SkillDefinition]) -> str:
    if not skills:
        return "No skills available."
    lines = []
    for skill in skills:
        lines.append(f"- {skill.slug}: {skill.description or skill.name}")
    return "\n".join(lines)
