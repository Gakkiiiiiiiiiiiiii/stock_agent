"""Discover enabled workload names from rendered Helm YAML."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def workloads(path: Path) -> dict[str, list[str]]:
    result = {"Deployment": [], "CronJob": []}
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not doc or doc.get("kind") not in result:
            continue
        result[doc["kind"]].append(str(doc.get("metadata", {}).get("name", "")))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    for kind, names in workloads(args.manifest).items():
        for name in names:
            print(f"{kind}/{name}")
