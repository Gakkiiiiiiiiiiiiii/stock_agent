"""SA-E2E-P0-01 profile isolation regressions."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_knowledge_only_starts_without_formal_modules_or_quant_factor_clients(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'knowledge-profile.db'}"
    script = """
import builtins
from storage.bootstrap import create_all
create_all()
blocked = (
    'app.composition.formal_decision_composition',
    'app.routers.decision',
    'clients.quant_client',
    'clients.factor_client',
    'engines.market.trading_clock',
)
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked):
        raise ImportError(f'blocked formal dependency: {name}')
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import
from fastapi.testclient import TestClient
from app.api import app, capability_profile
assert capability_profile.value == 'knowledge-only'
paths = {route.path for route in app.routes}
assert '/health/knowledge-conclusion-ready' in paths
assert '/api/v2/decisions' not in paths
assert '/api/v1/risk/portfolio' not in paths
with TestClient(app) as client:
    assert client.get('/health').status_code == 200
    readiness = client.get('/health/knowledge-conclusion-ready')
    assert readiness.status_code == 503
    assert readiness.json()['status'] == 'not_ready'
    assert readiness.json()['profile'] == 'knowledge-only'
    assert readiness.json()['checks']['content_service'] == 'failed'
    assert client.post('/api/v2/decisions', json={}).status_code == 404
"""
    environment = {
        **os.environ,
        "STOCK_AGENT_PROFILE": "knowledge-only",
        "DATABASE_URL": database_url,
        "QUANT_SERVICE_URL": "http://unresolvable-quant.invalid:8011",
        "FACTOR_SERVICE_URL": "http://unresolvable-factor.invalid:8200",
    }
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=environment, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_knowledge_composition_has_no_formal_decision_imports():
    source = ROOT / "app" / "composition" / "knowledge_composition.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(
        name.startswith(("app.domain.decision", "app.application.decision", "engines.market", "services.evidence"))
        for name in imports
    )


def test_api_runtime_uses_verification_not_runtime_schema_creation():
    dependencies = (ROOT / "app" / "dependencies.py").read_text(encoding="utf-8")
    migration_owner = (ROOT / "scripts" / "migrate_schema.py").read_text(encoding="utf-8")
    assert "create_all" not in dependencies
    assert "verify_schema" in dependencies
    assert "create_all" in migration_owner
