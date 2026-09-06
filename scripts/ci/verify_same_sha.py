"""Create and verify immutable CI provenance for the checked-out commit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _checkout_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
    ).strip()


def _require_sha(value: str | None, source: str) -> str:
    if not value:
        raise ValueError(f"{source} is required")
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value.lower()
    ):
        raise ValueError(f"{source} must be a full Git SHA")
    return value.lower()


def _require_run_value(value: str | None, source: str) -> str:
    if not value or not value.isdecimal():
        raise ValueError(f"{source} is required and must be numeric")
    return value


def provenance(github_sha: str | None = None) -> dict[str, str]:
    """Return provenance only when the runner checkout is the event SHA."""
    event_sha = _require_sha(github_sha or os.getenv("GITHUB_SHA"), "GITHUB_SHA")
    checkout_sha = _require_sha(_checkout_sha(), "checked-out SHA")
    if checkout_sha != event_sha:
        raise ValueError(
            f"checkout SHA {checkout_sha} does not match GITHUB_SHA {event_sha}"
        )
    return {
        "schema_version": "ci.provenance.v1",
        "commit_sha": event_sha,
        "github_run_id": _require_run_value(
            os.getenv("GITHUB_RUN_ID"), "GITHUB_RUN_ID"
        ),
        "github_run_attempt": _require_run_value(
            os.getenv("GITHUB_RUN_ATTEMPT"), "GITHUB_RUN_ATTEMPT"
        ),
    }


def write_provenance(path: Path, github_output: Path | None = None) -> None:
    payload = provenance()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as output:
            output.write(f"commit_sha={payload['commit_sha']}\n")


def verify_provenance(path: Path, expected_sha: str | None = None) -> None:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    expected = provenance(expected_sha)
    if payload != expected:
        raise ValueError(
            "CI provenance must match this checkout, event SHA, and workflow run"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write")
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--github-output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--expected-sha")
    args = parser.parse_args()
    try:
        if args.command == "write":
            write_provenance(args.output, args.github_output)
        else:
            verify_provenance(args.artifact, args.expected_sha)
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
