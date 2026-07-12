from pathlib import Path

from app.admin_service import AdminContentService


def test_admin_service_can_save_doc_and_skill(tmp_path: Path):
    (tmp_path / "knowledge_base" / "strategies").mkdir(parents=True)
    (tmp_path / "skills" / "demo-skill").mkdir(parents=True)
    (tmp_path / "storage").mkdir(parents=True)
    (tmp_path / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    service = AdminContentService(root=tmp_path)

    saved_doc = service.save_knowledge_doc("strategies/test.md", "# Test\n")
    assert saved_doc["title"] == "Test"
    assert service.get_knowledge_doc("strategies/test.md")["content"] == "# Test\n"

    saved_skill = service.save_skill(slug="demo-skill", name="demo-skill", description="updated", content="# Updated")
    assert saved_skill["description"] == "updated"
    assert "# Updated" in saved_skill["content"]


def test_admin_service_can_save_theme(tmp_path: Path):
    (tmp_path / "knowledge_base" / "themes").mkdir(parents=True)
    (tmp_path / "storage").mkdir(parents=True)
    service = AdminContentService(root=tmp_path)

    payload = {
        "theme_name": "测试主题",
        "aliases": ["别名A"],
        "core_thesis": "核心逻辑",
        "industry_chain": ["上游", "下游"],
        "catalysts": ["催化"],
        "monitor_keywords": ["关键词"],
        "trigger_rules": ["触发"],
        "invalidation_rules": ["证伪"],
        "risks": ["风险"],
        "related_stocks": [{"symbol": "000001", "name": "平安银行", "relation": "示例", "sensitivity_score": 60, "certainty_score": 70}],
    }

    saved = service.save_theme(payload)
    assert saved["theme_name"] == "测试主题"
    assert "核心逻辑" in saved["markdown_content"]
