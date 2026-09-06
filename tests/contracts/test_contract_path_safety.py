from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

from app.application.readiness.formal_policy import FormalReadinessPolicy
from app.application.readiness.service import FormalReadinessService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFY_MANIFEST_PATH = REPOSITORY_ROOT / "scripts" / "contracts" / "verify_manifest.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_manifest", VERIFY_MANIFEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


verify = _load_verifier()


def test_formal_policy_loads_the_repository_contract_manifest() -> None:
    policy = FormalReadinessPolicy.from_manifest(
        REPOSITORY_ROOT / "contracts" / "platform-manifest.yaml",
    )

    assert policy.expected_contracts == {
        "quant": "market-data.v1",
        "stock_factor": "factor.v1",
        "stock_content": "content.v1",
    }


def _write_manifest(root: Path, schema_reference: str) -> Path:
    contracts = root / "contracts"
    contracts.mkdir(parents=True)
    schema = contracts / "market.py"
    schema.write_bytes(b"market contract\n")
    checksum = "sha256:" + hashlib.sha256(schema.read_bytes()).hexdigest()
    manifest = contracts / "platform-manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "contracts": {
                    "market-data.v1": {
                        "schema": schema_reference,
                        "checksum": checksum,
                        "producer": "quant",
                        "consumers": ["stock_agent"],
                    }
                }
            },
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize(
    ("schema_reference", "error"),
    [
        ("C:/outside/market.py", "CONTRACT_PATH_ABSOLUTE"),
        ("contracts/../outside/market.py", "CONTRACT_PATH_TRAVERSAL"),
        ("contracts-private/market.py", "CONTRACT_PATH_OUTSIDE_ROOT"),
    ],
)
def test_verifier_rejects_uncontained_manifest_references(
    tmp_path: Path, schema_reference: str, error: str,
) -> None:
    root = tmp_path / "repository"
    manifest = _write_manifest(root, schema_reference)

    assert verify(root, manifest) == [f"market-data.v1: {error}"]


def test_verifier_rejects_schema_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest = _write_manifest(root, "contracts/market.py")
    outside_schema = tmp_path / "outside.py"
    outside_schema.write_bytes(b"outside contract\n")
    schema = root / "contracts" / "market.py"
    schema.unlink()
    try:
        schema.symlink_to(outside_schema)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    assert verify(root, manifest) == ["market-data.v1: CONTRACT_PATH_OUTSIDE_ROOT"]


def test_verifier_rejects_a_manifest_outside_the_repository_contracts_root(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    outside_manifest = _write_manifest(tmp_path / "outside", "contracts/market.py")

    assert verify(root, outside_manifest) == ["manifest: CONTRACT_PATH_OUTSIDE_ROOT"]


def test_formal_readiness_denies_an_uncontained_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest = _write_manifest(root, "contracts/market.py")

    with pytest.raises(ValueError, match="CONTRACT_PATH_OUTSIDE_ROOT"):
        FormalReadinessPolicy.from_manifest(manifest)

    result = FormalReadinessService(
        {}, FormalReadinessPolicy(manifest_error="CONTRACT_PATH_OUTSIDE_ROOT"),
    ).check()
    assert not result.ready
    assert "CONTRACT_MANIFEST_INVALID" in result.reason_codes
