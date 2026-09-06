"""Select and run the deterministic safety closure required by release CI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "ci.safety-closure.v1"

# Categories are deliberately pattern based: a newly added safety test that
# follows the repository naming convention is included without editing CI.
CLOSURE_PATTERNS: dict[str, tuple[str, ...]] = {
    "formal_authority": (
        "tests/test_decision_authority*.py",
        "tests/architecture/test_decision_authority*.py",
        "tests/test_no_dual_decision_path.py",
    ),
    "unit_of_work": ("tests/test_migrations.py",),
    "readiness": ("tests/test_formal_readiness.py", "tests/test_readiness*.py"),
    "replay": ("tests/test_*replay*.py",),
    "outcome": ("tests/test_*outcome*.py",),
    "ci": ("tests/architecture/test_ci_*.py",),
    "kubernetes": (
        "tests/test_kubernetes_runtime_layout.py",
        "tests/test_docker_runtime_layout.py",
    ),
}


def closure_manifest() -> dict[str, object]:
    """Return a complete, non-empty test manifest for every safety category."""
    categories: dict[str, list[str]] = {}
    for category, patterns in CLOSURE_PATTERNS.items():
        selected = sorted(
            {
                path.relative_to(ROOT).as_posix()
                for pattern in patterns
                for path in ROOT.glob(pattern)
                if path.is_file()
            }
        )
        if not selected:
            raise ValueError(f"safety closure category {category!r} selected no tests")
        categories[category] = selected
    return {"schema_version": SCHEMA_VERSION, "categories": categories}


def selected_tests(manifest: dict[str, object]) -> list[str]:
    categories = manifest["categories"]
    assert isinstance(categories, dict)
    return sorted(
        {
            path
            for paths in categories.values()
            if isinstance(paths, list)
            for path in paths
            if isinstance(path, str)
        }
    )


def write_manifest(path: Path) -> dict[str, object]:
    manifest = closure_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--basetemp", required=True)
    args = parser.parse_args()

    try:
        manifest = write_manifest(args.output) if args.command == "verify" else closure_manifest()
        if args.command == "run":
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-m",
                    "not integration",
                    f"--basetemp={args.basetemp}",
                    *selected_tests(manifest),
                ],
                cwd=ROOT,
                check=False,
            )
            return result.returncode
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
