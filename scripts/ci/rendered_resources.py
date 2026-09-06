"""Discover enabled workload names from rendered Helm YAML."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


def workloads(path: Path) -> dict[str, list[str]]:
    result = {"Deployment": [], "CronJob": []}
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not doc or doc.get("kind") not in result:
            continue
        result[doc["kind"]].append(str(doc.get("metadata", {}).get("name", "")))
    return result


def _documents(path: Path) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _component(document: dict[str, Any]) -> str:
    return str(document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component", ""))


def _container(document: dict[str, Any]) -> dict[str, Any]:
    return document["spec"]["template"]["spec"]["containers"][0]


def validate_workloads(path: Path) -> list[str]:
    """Return rendered-workload errors for the enabled API, worker, and CronJob."""
    documents = _documents(path)
    deployments = {
        _component(document): document
        for document in documents
        if document.get("kind") == "Deployment"
    }
    errors: list[str] = []

    api = deployments.get("api")
    if api is None:
        errors.append("enabled api Deployment is missing")
    else:
        strategy = api.get("spec", {}).get("strategy", {})
        rolling_update = strategy.get("rollingUpdate", {})
        probe = _container(api).get("readinessProbe", {}).get("httpGet", {})
        command = _container(api).get("command", [])
        if strategy.get("type") != "RollingUpdate":
            errors.append("api must use RollingUpdate")
        if rolling_update.get("maxUnavailable") != 0 or rolling_update.get("maxSurge") != 1:
            errors.append("api rolling update must keep maxUnavailable=0 and maxSurge=1")
        if probe.get("path") != "/health/ready" or probe.get("port") != 8000:
            errors.append("api readiness probe must target /health/ready on port 8000")
        if any("migration" in str(part).lower() for part in command):
            errors.append("api command must not run a migration job")

    worker = deployments.get("worker")
    if worker is None:
        errors.append("enabled worker Deployment is missing")
    else:
        if worker.get("spec", {}).get("replicas") != 1:
            errors.append("singleton worker must have exactly one replica")
        if worker.get("spec", {}).get("strategy", {}).get("type") != "Recreate":
            errors.append("singleton worker must use Recreate")

    cron_jobs = [document for document in documents if document.get("kind") == "CronJob"]
    if not cron_jobs:
        errors.append("enabled CronJob is missing")
    for cron_job in cron_jobs:
        spec = cron_job.get("spec", {})
        pod_spec = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
        containers = pod_spec.get("containers", [])
        command = containers[0].get("command", []) if containers else []
        if spec.get("concurrencyPolicy") != "Forbid":
            errors.append(f"CronJob/{cron_job.get('metadata', {}).get('name', '')} must forbid overlap")
        if pod_spec.get("restartPolicy") != "OnFailure":
            errors.append(f"CronJob/{cron_job.get('metadata', {}).get('name', '')} must restart OnFailure")
        if any("migration" in str(part).lower() for part in command):
            errors.append(f"CronJob/{cron_job.get('metadata', {}).get('name', '')} must not run a migration job")

    if any(document.get("kind") == "Job" for document in documents):
        errors.append("chart must not create a standalone migration Job")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        errors = validate_workloads(args.manifest)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            raise SystemExit(1)
    for kind, names in workloads(args.manifest).items():
        for name in names:
            print(f"{kind}/{name}")
