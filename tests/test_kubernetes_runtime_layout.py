"""Static guardrails for the Helm workloads exercised by the Kind smoke job."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = ROOT / "deploy" / "helm" / "stock-agent" / "values.yaml"
CI_VALUES = ROOT / "deploy" / "helm" / "stock-agent" / "ci-smoke-values.yaml"
DEPLOYMENTS = ROOT / "deploy" / "helm" / "stock-agent" / "templates" / "deployments.yaml"
CRONJOBS = ROOT / "deploy" / "helm" / "stock-agent" / "templates" / "cronjobs.yaml"
K8S_SMOKE = ROOT / ".github" / "workflows" / "k8s-smoke.yml"
RENDERED_RESOURCES = ROOT / "scripts" / "ci" / "rendered_resources.py"


def _load_rendered_resources():
    spec = importlib.util.spec_from_file_location("rendered_resources", RENDERED_RESOURCES)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enabled_workloads_preserve_readiness_singleton_and_cron_boundaries() -> None:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    assert values["services"]["api"]["enabled"] is True
    assert values["services"]["api"]["probe"] == {
        "type": "http",
        "path": "/health/ready",
        "port": 8000,
    }
    assert values["services"]["worker"]["enabled"] is True
    assert values["services"]["worker"]["singleton"] is True
    assert values["services"]["worker"]["replicas"] == 1
    assert values["cronJobs"][0]["command"] == ["python", "-m", "workers.job_worker", "--once"]

    deployments = DEPLOYMENTS.read_text(encoding="utf-8")
    cron_jobs = CRONJOBS.read_text(encoding="utf-8")
    assert "type: {{ if $service.singleton }}Recreate{{ else }}RollingUpdate{{ end }}" in deployments
    assert "rollingUpdate: {maxUnavailable: 0, maxSurge: 1}" in deployments
    assert "readinessProbe:" in deployments and "/health/ready" in deployments
    assert "concurrencyPolicy: Forbid" in cron_jobs
    assert "kind: Job" not in cron_jobs


def test_ci_smoke_values_enable_exactly_the_rendered_components() -> None:
    values = yaml.safe_load(CI_VALUES.read_text(encoding="utf-8"))
    assert values["services"]["api"]["enabled"] is True
    assert values["services"]["worker"]["enabled"] is True
    assert values["services"]["analysis"]["enabled"] is False
    assert values["cronJobs"]


def test_rendered_validator_rejects_invalid_rollout_boundaries(tmp_path: Path) -> None:
    manifest = tmp_path / "rendered.yaml"
    manifest.write_text(
        """
apiVersion: apps/v1
kind: Deployment
metadata: {name: stock-agent-api, labels: {app.kubernetes.io/component: api}}
spec:
  strategy: {type: RollingUpdate, rollingUpdate: {maxUnavailable: 0, maxSurge: 1}}
  template:
    spec:
      containers: [{name: api, command: [uvicorn], readinessProbe: {httpGet: {path: /health/ready, port: 8000}}}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: stock-agent-worker, labels: {app.kubernetes.io/component: worker}}
spec:
  replicas: 1
  strategy: {type: Recreate}
  template: {spec: {containers: [{name: worker, command: [python, -m, workers.job_worker]}]}}
---
apiVersion: batch/v1
kind: CronJob
metadata: {name: stock-agent-outcome-evaluation}
spec:
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers: [{name: outcome-evaluation, command: [python, -m, workers.job_worker, --once]}]
""".lstrip(),
        encoding="utf-8",
    )
    validator = _load_rendered_resources()
    assert validator.validate_workloads(manifest) == []

    manifest.write_text(manifest.read_text(encoding="utf-8").replace("type: Recreate", "type: RollingUpdate"), encoding="utf-8")
    assert "singleton worker must use Recreate" in validator.validate_workloads(manifest)


def test_kind_smoke_renders_three_times_and_exercises_upgrade_and_rollback() -> None:
    workflow = K8S_SMOKE.read_text(encoding="utf-8")
    assert "for attempt in 1 2 3" in workflow
    assert "helm upgrade --install" in workflow
    assert 'helm rollback "$HELM_RELEASE" 1' in workflow
    assert "--validate" in workflow
