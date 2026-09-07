"""SA-E2E-P0-07: content conclusions never acquire decision authority."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.knowledge_conclusion import KnowledgeConclusion
from tests.test_knowledge_conclusion import _conclusion

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_CONCLUSION_ROOTS = (
    ROOT / "app" / "application" / "knowledge_conclusion",
    ROOT / "app" / "domain" / "knowledge_conclusion.py",
    ROOT / "app" / "domain" / "knowledge_conclusion_lineage.py",
    ROOT / "app" / "domain" / "knowledge_conclusion_run.py",
    ROOT / "app" / "ports" / "knowledge_conclusion_model.py",
    ROOT / "app" / "ports" / "knowledge_conclusion_metrics.py",
    ROOT / "app" / "ports" / "knowledge_conclusion_repository.py",
    ROOT / "app" / "adapters" / "postgres" / "knowledge_conclusion_repository.py",
)
FORBIDDEN_IMPORT_PARTS = (
    "execution",
    "broker",
    "oms",
    "order",
    "authorization",
    "portfolio",
    "app.application.decision",
    "app.domain.decision",
    "app.ports.decision",
    "app.adapters.postgres.decision",
    "formal_decision",
)
FORBIDDEN_CALL_NAMES = {
    "authorize_execution",
    "cancel_order",
    "create_decision",
    "create_order",
    "mutate_portfolio",
    "place_order",
    "save_decision",
    "save_investment_decision",
    "submit_order",
}


def _knowledge_conclusion_sources() -> tuple[Path, ...]:
    paths: list[Path] = []
    for root in KNOWLEDGE_CONCLUSION_ROOTS:
        paths.extend(root.rglob("*.py") if root.is_dir() else (root,))
    return tuple(paths)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.lower())
    return modules


def test_knowledge_conclusion_layers_have_no_decision_or_execution_imports() -> None:
    """Every conclusion layer is dependency-isolated, not merely the service."""
    for path in _knowledge_conclusion_sources():
        imported = _imported_modules(path)
        blocked = {
            module
            for module in imported
            if any(part in module for part in FORBIDDEN_IMPORT_PARTS)
        }
        assert not blocked, f"{path.relative_to(ROOT)} imports authority path(s): {sorted(blocked)}"


def test_knowledge_conclusion_layers_have_no_authority_entrypoint_calls() -> None:
    for path in _knowledge_conclusion_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert not calls.intersection(FORBIDDEN_CALL_NAMES), path.relative_to(ROOT)


def test_knowledge_conclusion_contract_authority_markers_are_non_overridable() -> None:
    payload = _conclusion().model_dump()
    for field, invalid_value in (("scope", "FORMAL_DECISION"), ("execution_eligible", True)):
        invalid = {**payload, field: invalid_value}
        with pytest.raises(ValidationError):
            KnowledgeConclusion.model_validate(invalid)


def test_knowledge_conclusion_json_schema_hard_fixes_content_only_non_execution_scope() -> None:
    schema_path = ROOT / "contracts" / "knowledge-conclusion.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["scope"] == {"const": "CONTENT_ONLY_RESEARCH"}
    assert schema["properties"]["execution_eligible"] == {"const": False}


def test_postgres_adapter_public_exports_remain_available_for_full_composition() -> None:
    from app.adapters.postgres import (
        PostgresDecisionOutbox,
        PostgresDecisionRepository,
        PostgresKnowledgeConclusionRepository,
    )

    assert PostgresDecisionOutbox.__name__ == "PostgresDecisionOutbox"
    assert PostgresDecisionRepository.__name__ == "PostgresDecisionRepository"
    assert PostgresKnowledgeConclusionRepository.__name__ == "PostgresKnowledgeConclusionRepository"


def test_knowledge_only_runtime_constructs_conclusions_with_authority_modules_blocked(tmp_path) -> None:
    """A fresh process proves construction has no hidden formal side effect."""
    database_url = f"sqlite:///{tmp_path / 'knowledge-authority.db'}"
    script = '''
import builtins
import importlib.abc
import sys
from datetime import datetime, timezone

from storage.bootstrap import create_all
create_all()

blocked = (
    "app.composition.formal_decision_composition", "app.application.decision",
    "app.domain.decision", "app.ports.decision", "app.adapters.postgres.decision",
    "app.adapters.postgres.decision_outbox",
    "app.routers.decision", "app.routers.portfolio", "engines.execution",
    "broker", "oms", "order", "authorization", "portfolio",
)
class AuthorityImportSentinel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            raise AssertionError(f"blocked authority import: {fullname}")
sys.meta_path.insert(0, AuthorityImportSentinel())
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
        raise AssertionError(f"blocked authority import: {name}")
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import

from app.adapters.postgres.knowledge_conclusion_repository import PostgresKnowledgeConclusionRepository
assert PostgresKnowledgeConclusionRepository.__name__ == "PostgresKnowledgeConclusionRepository"

from app.domain.knowledge_conclusion import (
    ConclusionVerdict, Finding, KnowledgeConclusion, KnowledgeConclusionRequest, ModelIdentity,
)
finding = Finding(text="内容显示需求改善仍待订单确认。", knowledge_ids=("ko-1",), evidence_ids=("ev-1",), confidence=0.8)
result = KnowledgeConclusion.construct(
    request=KnowledgeConclusionRequest(content_snapshot_id="snapshot-1", query="内容支持什么研究结论？"),
    conclusion_id="conclusion-1", content_bundle_id="bundle-1", verdict=ConclusionVerdict.SUPPORTED,
    market_stance="UNCERTAIN", summary=finding, findings=(finding,),
    model=ModelIdentity(mode="FALLBACK", provider="fixture", model="fixture"),
    created_at=datetime.now(timezone.utc),
)
assert result.scope == "CONTENT_ONLY_RESEARCH"
assert result.execution_eligible is False

from fastapi.testclient import TestClient
from app.api import app, capability_profile
assert capability_profile.value == "knowledge-only"
for route in app.routes:
    assert not any(
        part in route.path.lower()
        for part in ("decision", "portfolio", "execution", "order", "broker", "authorization")
    )
openapi = app.openapi()
presentation = " ".join(
    str(value)
    for value in (
        openapi.get("info", {}).get("title", ""),
        openapi.get("info", {}).get("description", ""),
        *(operation.get("summary", "") for item in openapi.get("paths", {}).values() for operation in item.values()),
    )
)
assert not any(term in presentation for term in ("交易建议", "买入信号", "下单"))
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "STOCK_AGENT_PROFILE": "knowledge-only", "DATABASE_URL": database_url},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
