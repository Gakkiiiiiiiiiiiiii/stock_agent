from __future__ import annotations

from pathlib import Path

from financial_agent.models import ThemeLogic
from financial_agent.utils import project_root
from storage.repositories.theme_repository import ThemeRepository


class AdminContentService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or project_root()).resolve()
        self.knowledge_root = (self.root / "knowledge_base").resolve()
        self.skills_root = (self.root / "skills").resolve()
        self.theme_repository = ThemeRepository(
            root=self.root,
            index_path=self.root / "storage" / "theme_logic.json",
            seed_path=self.root / "storage" / "theme_seed.json",
            markdown_dir=self.knowledge_root / "themes",
        )

    def list_themes(self) -> list[dict]:
        items = []
        for theme in sorted(self.theme_repository.list_themes(), key=lambda item: item.theme_name):
            items.append(
                {
                    "theme_name": theme.theme_name,
                    "aliases": theme.aliases,
                    "related_stock_count": len(theme.related_stocks),
                    "catalyst_count": len(theme.catalysts),
                }
            )
        return items

    def get_theme(self, theme_name: str) -> dict:
        theme = self.theme_repository.search(theme_name)
        if not theme:
            raise FileNotFoundError(theme_name)
        markdown_path = self.knowledge_root / "themes" / f"{theme.theme_name}.md"
        markdown_content = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
        payload = theme.model_dump()
        payload["markdown_content"] = markdown_content
        return payload

    def save_theme(self, payload: dict) -> dict:
        theme = ThemeLogic.model_validate(payload)
        saved = self.theme_repository.upsert(theme)
        return self.get_theme(saved.theme_name)

    def list_knowledge_docs(self) -> list[dict]:
        items = []
        for path in sorted(self.knowledge_root.rglob("*.md")):
            relative = path.relative_to(self.knowledge_root).as_posix()
            if relative.startswith("themes/"):
                continue
            items.append(
                {
                    "path": relative,
                    "section": relative.split("/", 1)[0] if "/" in relative else "root",
                    "title": self._extract_title(path),
                }
            )
        return items

    def get_knowledge_doc(self, relative_path: str) -> dict:
        path = self._resolve_within(self.knowledge_root, relative_path)
        if relative_path.startswith("themes/"):
            raise ValueError("theme markdown is generated from structured theme entries")
        return {"path": relative_path, "content": path.read_text(encoding="utf-8"), "title": self._extract_title(path)}

    def save_knowledge_doc(self, relative_path: str, content: str) -> dict:
        path = self._resolve_within(self.knowledge_root, relative_path)
        if relative_path.startswith("themes/"):
            raise ValueError("theme markdown is generated from structured theme entries")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": relative_path, "content": content, "title": self._extract_title(path)}

    def delete_knowledge_doc(self, relative_path: str) -> dict:
        path = self._resolve_within(self.knowledge_root, relative_path)
        if relative_path.startswith("themes/"):
            raise ValueError("theme markdown is generated from structured theme entries")
        if not path.exists():
            raise FileNotFoundError(relative_path)
        title = self._extract_title(path)
        path.unlink()
        return {"deleted": True, "path": relative_path, "title": title}

    def list_skills(self) -> list[dict]:
        items = []
        for path in sorted(self.skills_root.glob("*/SKILL.md")):
            slug = path.parent.name
            raw = path.read_text(encoding="utf-8")
            frontmatter, body = self._parse_frontmatter(raw)
            items.append(
                {
                    "slug": slug,
                    "name": frontmatter.get("name", slug),
                    "description": frontmatter.get("description", ""),
                }
            )
        return items

    def get_skill(self, slug: str) -> dict:
        path = self._skill_path(slug)
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(raw)
        return {
            "slug": slug,
            "name": frontmatter.get("name", slug),
            "description": frontmatter.get("description", ""),
            "content": body,
            "raw": raw,
        }

    def save_skill(self, slug: str, name: str, description: str, content: str) -> dict:
        path = self._skill_path(slug, create=True)
        raw = self._format_frontmatter({"name": name or slug, "description": description}, content)
        path.write_text(raw, encoding="utf-8")
        return self.get_skill(slug)

    def _skill_path(self, slug: str, create: bool = False) -> Path:
        safe_slug = slug.strip()
        if not safe_slug:
            raise ValueError("slug is required")
        folder = self._resolve_within(self.skills_root, f"{safe_slug}/SKILL.md")
        if create:
            folder.parent.mkdir(parents=True, exist_ok=True)
        elif not folder.exists():
            raise FileNotFoundError(safe_slug)
        return folder

    @staticmethod
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

    @staticmethod
    def _format_frontmatter(meta: dict[str, str], body: str) -> str:
        lines = ["---"]
        for key, value in meta.items():
            lines.append(f"{key}: {value}")
        lines.extend(["---", "", body.strip(), ""])
        return "\n".join(lines)

    @staticmethod
    def _extract_title(path: Path) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return path.stem

    @staticmethod
    def _resolve_within(base: Path, relative_path: str) -> Path:
        candidate = (base / relative_path).resolve()
        if base not in candidate.parents and candidate != base:
            raise ValueError("path escapes base directory")
        if candidate.suffix != ".md":
            raise ValueError("only markdown files are supported")
        return candidate
