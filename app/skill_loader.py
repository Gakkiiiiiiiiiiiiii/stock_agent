from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from financial_agent.utils import project_root


@dataclass(frozen=True)
class SkillDefinition:
    slug: str
    name: str
    description: str
    content: str


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


def load_skills(skill_root: Path | None = None) -> list[SkillDefinition]:
    root = skill_root or project_root() / "skills"
    skills: list[SkillDefinition] = []
    for path in sorted(root.glob("*/SKILL.md")):
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(raw)
        slug = path.parent.name
        skills.append(
            SkillDefinition(
                slug=slug,
                name=frontmatter.get("name", slug),
                description=frontmatter.get("description", ""),
                content=body,
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

