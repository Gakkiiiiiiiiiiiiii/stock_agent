from __future__ import annotations
import shutil
from pathlib import Path
import yaml
from financial_agent.utils import project_root

class CandidateWorkspace:
    def __init__(self, proposal_id: str, root: Path | None = None) -> None:
        self.root = root or project_root() / "artifacts" / "skill_candidates" / proposal_id
    def create(self, slug: str) -> Path:
        source = project_root() / "skills" / slug
        if not source.exists(): raise FileNotFoundError(slug)
        if self.root.exists(): shutil.rmtree(self.root)
        shutil.copytree(source, self.root)
        return self.root
    def apply(self, yaml_patch: dict, markdown_patch: str | None = None) -> None:
        path = self.root / "SKILL.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.update(yaml_patch)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if markdown_patch is not None: (self.root / "SKILL.md").write_text(markdown_patch, encoding="utf-8")
