"""Report required main-branch protection settings without mutating GitHub."""
from __future__ import annotations

import argparse
import json

REQUIRED = {
    "required_status_checks": ["lint", "unit-tests", "architecture", "contract", "decision-replay", "integration", "docker-build", "k8s-smoke"],
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": True,
    "enforce_admins": True,
    "allow_force_pushes": False,
    "allow_deletions": False,
}


def report(actual: dict | None = None) -> dict:
    actual = actual or {}
    missing = [key for key, value in REQUIRED.items() if actual.get(key) != value]
    return {"repository": "stock_agent", "branch": "main", "mutation_performed": False, "required": REQUIRED, "missing": missing, "status": "PASS" if not missing else "REPORT_ONLY"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", help="Optional JSON returned by GitHub API")
    args = parser.parse_args()
    if args.actual:
        with open(args.actual, encoding="utf-8") as handle:
            actual = json.load(handle)
    else:
        actual = None
    print(json.dumps(report(actual), indent=2))
