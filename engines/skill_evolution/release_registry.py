from __future__ import annotations
import json, shutil
from pathlib import Path
from financial_agent.utils import project_root

class SkillReleaseRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root() / "skills" / "releases"
    def promote(self, slug: str, version: int, candidate: Path) -> Path:
        target = self.root / slug / str(version)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(candidate, target)
        self._pointer(slug).write_text(json.dumps({"active_version": version, "previous_stable_version": self.active_version(slug)}), encoding="utf-8")
        return target
    def active_version(self, slug: str) -> int | None:
        path = self._pointer(slug)
        return json.loads(path.read_text(encoding="utf-8")).get("active_version") if path.exists() else None
    def rollback(self, slug: str) -> int:
        data = json.loads(self._pointer(slug).read_text(encoding="utf-8")); previous = data.get("previous_stable_version")
        if previous is None: raise ValueError("no previous stable version")
        data["active_version"], data["previous_stable_version"] = previous, data["active_version"]
        self._pointer(slug).write_text(json.dumps(data), encoding="utf-8"); return previous
    def _pointer(self, slug: str) -> Path:
        path = self.root / slug / "active.json"; path.parent.mkdir(parents=True, exist_ok=True); return path
