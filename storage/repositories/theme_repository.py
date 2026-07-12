from __future__ import annotations

import json
from pathlib import Path

from financial_agent.models import ThemeLogic
from financial_agent.utils import project_root


class ThemeRepository:
    def __init__(
        self,
        index_path: Path | None = None,
        markdown_dir: Path | None = None,
        seed_path: Path | None = None,
        root: Path | None = None,
    ) -> None:
        root = root or project_root()
        self.index_path = index_path or root / "storage" / "theme_logic.json"
        self.seed_path = seed_path or root / "storage" / "theme_seed.json"
        self.markdown_dir = markdown_dir or root / "knowledge_base" / "themes"
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_seed_data()

    def _load_all(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_all(self, data: dict[str, dict]) -> None:
        self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def search(self, theme_name: str) -> ThemeLogic | None:
        data = self._load_all()
        item = data.get(theme_name)
        if item:
            return ThemeLogic.model_validate(item)
        query = self._normalize_key(theme_name)
        for raw in data.values():
            theme = ThemeLogic.model_validate(raw)
            candidates = {
                self._normalize_key(theme.theme_name),
                *(self._normalize_key(alias) for alias in theme.aliases),
                *(self._normalize_key(stock.symbol) for stock in theme.related_stocks),
                *(self._normalize_key(stock.name) for stock in theme.related_stocks if stock.name),
            }
            if query in candidates:
                return theme
        return None

    def list_themes(self) -> list[ThemeLogic]:
        return [ThemeLogic.model_validate(item) for item in self._load_all().values()]

    def upsert(self, theme: ThemeLogic) -> ThemeLogic:
        data = self._load_all()
        data[theme.theme_name] = theme.model_dump()
        self._save_all(data)
        self._write_markdown(theme)
        return theme

    def _write_markdown(self, theme: ThemeLogic) -> None:
        rows = [
            f"# {theme.theme_name}",
            "",
            "## 0. 别名",
            "\n".join(f"- {item}" for item in theme.aliases) or "无",
            "",
            "## 1. 核心结论",
            theme.core_thesis or "待补充",
            "",
            "## 2. 核心逻辑",
            theme.core_thesis or "待补充",
            "",
            "## 3. 产业链拆解",
            "\n".join(f"- {item}" for item in theme.industry_chain) or "待补充",
            "",
            "## 4. 受益标的",
            "| 标的 | 名称 | 环节 | 弹性 | 确定性 |",
            "|---|---|---|---:|---:|",
        ]
        rows.extend(
            f"| {stock.symbol} | {stock.name} | {stock.relation} | {stock.sensitivity_score} | {stock.certainty_score} |"
            for stock in theme.related_stocks
        )
        rows.extend(
            [
                "",
                "## 5. 催化因素",
                "\n".join(f"- {item}" for item in theme.catalysts) or "待补充",
                "",
                "## 6. 监控关键词",
                "\n".join(f"- {item}" for item in theme.monitor_keywords) or "待补充",
                "",
                "## 7. 触发规则",
                "\n".join(f"- {item}" for item in theme.trigger_rules) or "待补充",
                "",
                "## 8. 证伪条件",
                "\n".join(f"- {item}" for item in theme.invalidation_rules) or "待补充",
                "",
                "## 9. 风险因素",
                "\n".join(f"- {item}" for item in theme.risks) or "待补充",
                "",
                "## 10. 历史案例",
                "待补充",
                "",
                "## 11. 更新记录",
                "- 系统自动生成/更新",
            ]
        )
        (self.markdown_dir / f"{theme.theme_name}.md").write_text("\n".join(rows), encoding="utf-8")

    def _bootstrap_seed_data(self) -> None:
        if not self.seed_path.exists():
            return
        existing = self._load_all()
        seed_data = json.loads(self.seed_path.read_text(encoding="utf-8"))
        changed = False
        for theme_name, payload in seed_data.items():
            if theme_name in existing:
                continue
            theme = ThemeLogic.model_validate(payload)
            existing[theme.theme_name] = theme.model_dump()
            self._write_markdown(theme)
            changed = True
        if changed:
            self._save_all(existing)

    @staticmethod
    def _normalize_key(value: str) -> str:
        return "".join(str(value).strip().lower().split())
