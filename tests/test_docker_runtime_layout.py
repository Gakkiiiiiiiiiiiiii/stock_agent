import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERIGNORE = ROOT / ".dockerignore"
INVENTORY = ROOT / "docs" / "maintenance" / "repository-inventory.yaml"
VALUES = ROOT / "deploy" / "helm" / "stock-agent" / "values.yaml"
CI_VALUES = ROOT / "deploy" / "helm" / "stock-agent" / "ci-smoke-values.yaml"


def _target_block(dockerfile: str, target: str) -> str:
    marker = f"FROM python:3.11-slim AS {target}\n"
    block = dockerfile.split(marker, maxsplit=1)[1]
    return block.split("\nFROM ", maxsplit=1)[0]


def test_role_images_use_explicit_source_boundaries():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert all(f"FROM python:3.11-slim AS {role}" in dockerfile for role in ("api", "worker", "analysis"))
    assert all(f"COPY --from={role}-deps /install /usr/local" in dockerfile for role in ("api", "worker", "analysis"))
    assert "COPY knowledge_base ./knowledge_base" in dockerfile

    for historical_root in ("engines", "services", "workers", "storage"):
        assert f"COPY {historical_root} ./{historical_root}" not in dockerfile
    assert "mcp_servers" not in dockerfile
    assert "market_stream_worker" not in dockerfile
    assert "qmt_bridge_client" not in dockerfile

    api = _target_block(dockerfile, "api")
    worker = _target_block(dockerfile, "worker")
    analysis = _target_block(dockerfile, "analysis")
    assert "COPY engines/decision ./engines/decision" in api
    assert "COPY services/evidence ./services/evidence" in api
    assert "COPY storage/models ./storage/models" in api
    assert "workers/job_worker.py" in worker
    assert "workers/vector_index_worker.py" in worker
    assert 'CMD ["python", "-m", "workers.job_worker"]' in worker
    assert "workers/embedding_api.py" in analysis
    assert "workers/reranker_api.py" in analysis


def test_api_runtime_contains_only_current_formal_decision_migrations():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    api = _target_block(dockerfile, "api")
    worker = _target_block(dockerfile, "worker")
    analysis = _target_block(dockerfile, "analysis")

    migration_sources = set(re.findall(r"storage/migrations/([0-9]{3}_[A-Za-z0-9_]+\.sql)", api))
    assert migration_sources == {
        "039_decision_unit_of_work.sql",
        "040_replay_outcome_runs.sql",
        "041_decision_run_final_response.sql",
    }
    assert "COPY storage/migrations ./storage/migrations" not in api
    assert "COPY storage/migrations/024_execution_order_events.sql" not in api
    assert "COPY storage/migrations/026_execution_runtime_state.sql" not in api
    assert "storage/migrations" not in worker
    assert "storage/migrations" not in analysis

    inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    migrations = next(entry for entry in inventory["entries"] if entry["path"] == "storage/migrations")
    assert migrations["status"] == "retained-selective-api-schema"
    assert migrations["runtime_role"] == "api-only"
    assert migrations["runtime_allowlist"] == [
        "039_decision_unit_of_work.sql",
        "040_replay_outcome_runs.sql",
        "041_decision_run_final_response.sql",
    ]
    assert migrations["excluded_from_runtime"] == [
        "024_execution_order_events.sql",
        "026_execution_runtime_state.sql",
    ]


def test_dockerignore_excludes_python_caches_recursively():
    ignored = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert "**/__pycache__/" in ignored
    assert "**/*.pyc" in ignored
    assert "**/*.pyo" in ignored


def test_runtime_data_is_ignored_and_untracked():
    """Runtime state stays local; fixtures and source remain the only tracked data."""
    ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {"data/", "storage/audit/", "storage/chat_sessions/*.json", "storage/runtime/"} <= ignored

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    runtime_roots = (
        "data/",
        "storage/audit/",
        "storage/chat_sessions/",
        "storage/content/",
        "knowledge_base/video_summaries/",
    )
    runtime_files = [
        path for path in tracked
        if path.startswith(runtime_roots) or path.endswith((".db", ".sqlite", ".sqlite3"))
    ]
    assert runtime_files == []
    assert [path for path in tracked if path.startswith("storage/runtime/")] == ["storage/runtime/.gitkeep"]


def test_config_directory_has_one_yaml_loading_authority():
    """Manifests may be read locally, but shared YAML configuration has one loader."""
    expected_yaml_readers = {
        "financial_agent/config.py",
        "app/application/readiness/formal_policy.py",
        "app/routers/decision.py",
        "app/skill_loader.py",
        "engines/skill_evolution/candidate_workspace.py",
        "engines/skill_evolution/golden_executor.py",
    }
    roots = (
        ROOT / "app",
        ROOT / "agent",
        ROOT / "clients",
        ROOT / "engines",
        ROOT / "financial_agent",
        ROOT / "services",
        ROOT / "workers",
    )
    yaml_readers = {
        path.relative_to(ROOT).as_posix()
        for root in roots
        for path in root.rglob("*.py")
        if "yaml.safe_load" in path.read_text(encoding="utf-8")
    }
    assert yaml_readers == expected_yaml_readers

    direct_config_roots = {
        path.relative_to(ROOT).as_posix()
        for root in roots
        for path in root.rglob("*.py")
        if 'project_root() / "config"' in path.read_text(encoding="utf-8")
    }
    assert direct_config_roots == {"financial_agent/config.py"}

    inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in inventory["entries"]}
    assert entries["config"] == {
        "path": "config",
        "owner": "financial_agent.config",
        "status": "single-yaml-authority",
        "replacement": None,
    }


def test_launchers_do_not_restore_legacy_producers():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    launchers = "\n".join(
        (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for name in ("start-api.ps1", "start-docker-stack.ps1")
    )
    assert "app.api:app" in dockerfile
    assert "app.api:app" in launchers
    for legacy_producer in ("mcp_servers", "engines.execution", "qmt_bridge_client", "app.routers.decision"):
        assert legacy_producer not in dockerfile
        assert legacy_producer not in launchers


def test_compose_does_not_mount_role_source_trees():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    role_services = {"embedding", "reranker", "api", "vector_worker", "job_worker", "analysis"}
    source_prefixes = ("./app", "./agent", "./config", "./engines", "./financial_agent", "./knowledge_base", "./scripts", "./skills", "./storage", "./workers", "./pyproject.toml", "./README.md")
    for name in role_services:
        for volume in compose["services"].get(name, {}).get("volumes", []):
            source = str(volume).split(":", maxsplit=1)[0]
            assert not source.startswith(source_prefixes), f"{name} mounts source tree {volume}"

    allowed_host_data = {"./data/postgres", "./data/qdrant", "./data/redis", "./storage/migrations"}
    for name in ("postgres", "qdrant", "redis"):
        for volume in compose["services"][name].get("volumes", []):
            source = str(volume).split(":", maxsplit=1)[0]
            assert source in allowed_host_data


def test_worker_targets_use_module_startup_everywhere():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    ci_values = yaml.safe_load(CI_VALUES.read_text(encoding="utf-8"))

    assert 'CMD ["python", "workers/job_worker.py"]' not in dockerfile
    assert 'CMD ["python", "workers/vector_index_worker.py"]' not in dockerfile
    assert compose["services"]["vector_worker"]["command"] == "python -m workers.vector_index_worker"
    assert compose["services"]["job_worker"]["command"] == "python -m workers.job_worker"
    assert values["services"]["worker"]["command"] == ["python", "-m", "workers.job_worker"]
    assert values["cronJobs"][0]["command"] == ["python", "-m", "workers.job_worker", "--once"]
    assert ci_values["cronJobs"][0]["command"] == ["python", "-m", "workers.job_worker", "--once"]


def test_inventory_records_retained_and_deleted_execution_boundaries():
    inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in inventory["entries"]}
    assert entries["engines/backtest"]["status"] == "retained-calculation-only"
    assert entries["engines/backtest/execution.py"]["status"] == "retained-calculation-only"
    assert entries["engines/backtest/execution_model.py"]["status"] == "retained-calculation-only"
    assert entries["app/domain/decision/execution_authorization.py"]["status"] == "retained-contract-only"
    assert entries["contracts/execution-authorization.v1.json"]["status"] == "retained-contract-only"
    assert entries["storage/migrations"]["status"] == "retained-selective-api-schema"
    assert entries["config/execution.yaml"]["status"] == "deleted-no-callers"
    assert entries["clients/quant_execution_client.py"]["status"] == "deleted-no-callers"
