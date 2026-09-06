from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.application.readiness.contract_paths import (
    ContractPathError,
    resolve_contract_file,
    resolve_contract_reference,
)


@dataclass(frozen=True)
class FormalReadinessPolicy:
    policy_version: str = "formal-readiness.v1"
    max_snapshot_age_seconds: float = 300.0
    require_pit: bool = True
    required_contracts: tuple[str, ...] = ()
    expected_contracts: dict[str, str] = field(default_factory=dict)
    ttl_seconds: float = 30.0
    manifest_checksums: dict[str, str] = field(default_factory=dict)
    manifest_error: str | None = None

    @classmethod
    def from_manifest(cls, path: str | Path) -> FormalReadinessPolicy:
        repository_root = Path(__file__).resolve().parents[3]
        manifest_path = resolve_contract_file(repository_root, path)
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        contracts = document.get("contracts") or {}
        expected: dict[str, str] = {}
        checksums: dict[str, str] = {}
        components = {"quant": "market-data.v1", "stock_factor": "factor.v1", "stock_content": "content.v1"}
        for component, contract_name in components.items():
            item = contracts.get(contract_name)
            if not isinstance(item, dict) or not item.get("schema") or not item.get("checksum"):
                raise ValueError(f"CONTRACT_MANIFEST_MISSING:{contract_name}")
            try:
                schema_path = resolve_contract_reference(repository_root, item["schema"])
            except ContractPathError as exc:
                raise ValueError(f"{exc}:{contract_name}") from exc
            if not schema_path.is_file():
                raise ValueError(f"CONTRACT_SCHEMA_MISSING:{contract_name}")
            digest = "sha256:" + hashlib.sha256(schema_path.read_bytes()).hexdigest()
            if digest != item["checksum"]:
                raise ValueError(f"CONTRACT_CHECKSUM_MISMATCH:{contract_name}")
            sunset = item.get("sunset_at")
            if sunset and datetime.now(UTC).date().isoformat() > str(sunset):
                raise ValueError(f"CONTRACT_SUNSET:{contract_name}")
            expected[component] = contract_name
            checksums[contract_name] = digest
        return cls(expected_contracts=expected, required_contracts=tuple(expected.values()), manifest_checksums=checksums)
