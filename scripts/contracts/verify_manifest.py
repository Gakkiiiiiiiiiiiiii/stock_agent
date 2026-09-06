"""Verify registered contract schemas and checksums (no guessed compatibility)."""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.application.readiness.contract_paths import (
    ContractPathError,
    resolve_contract_file,
    resolve_contract_reference,
)


def verify(root: Path, manifest: Path) -> list[str]:
    try:
        manifest_path = resolve_contract_file(root, manifest)
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (ContractPathError, OSError, yaml.YAMLError) as exc:
        return [f"manifest: {exc}"]
    errors: list[str] = []
    for name, item in (data.get("contracts") or {}).items():
        try:
            schema = resolve_contract_reference(root, item.get("schema", ""))
        except ContractPathError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if not schema.is_file():
            errors.append(f"{name}: schema missing: {schema}")
            continue
        actual = "sha256:" + hashlib.sha256(schema.read_bytes()).hexdigest()
        if item.get("checksum") != actual:
            errors.append(f"{name}: checksum mismatch (manifest={item.get('checksum')}, actual={actual})")
        sunset = item.get("sunset_at")
        if sunset and datetime.fromisoformat(str(sunset)) <= datetime.now(UTC):
            errors.append(f"{name}: contract is sunset")
        if not item.get("producer") or not item.get("consumers"):
            errors.append(f"{name}: producer and consumers are required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    manifest = args.manifest or args.root / "contracts/platform-manifest.yaml"
    errors = verify(args.root, manifest)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
