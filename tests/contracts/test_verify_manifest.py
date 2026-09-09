from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFY_MANIFEST_PATH = REPOSITORY_ROOT / "scripts" / "contracts" / "verify_manifest.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_manifest", VERIFY_MANIFEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


verify = _load_verifier()


def test_tracked_contract_artifacts_require_lf_checkout() -> None:
    manifest_path = REPOSITORY_ROOT / "contracts" / "platform-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    schemas = [item["schema"] for item in manifest["contracts"].values()]

    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *schemas],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [f"{schema}: eol: lf" for schema in schemas]


def test_manifest_verifier_audits_schema_bytes_without_newline_canonicalization(tmp_path: Path) -> None:
    root = tmp_path / "root"
    contracts = root / "contracts"
    contracts.mkdir(parents=True)
    schema = contracts / "example.json"
    lf_schema = b'{\n  "contract": "example"\n}\n'
    schema.write_bytes(lf_schema)
    checksum = "sha256:" + hashlib.sha256(lf_schema).hexdigest()
    manifest = contracts / "platform-manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "contracts": {
                    "example.v1": {
                        "schema": "contracts/example.json",
                        "checksum": checksum,
                        "producer": "stock_agent",
                        "consumers": ["quant"],
                    }
                }
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert verify(root, manifest) == []

    schema.write_bytes(lf_schema.replace(b"\n", b"\r\n"))

    assert verify(root, manifest) == [
        f"example.v1: checksum mismatch (manifest={checksum}, actual=sha256:{hashlib.sha256(schema.read_bytes()).hexdigest()})"
    ]


def test_current_contract_manifest_verifies() -> None:
    manifest = REPOSITORY_ROOT / "contracts" / "platform-manifest.yaml"

    assert verify(REPOSITORY_ROOT, manifest) == []


def test_content_knowledge_bundle_v2_consumer_manifest_is_checksum_locked() -> None:
    manifest = yaml.safe_load((REPOSITORY_ROOT / "contracts" / "platform-manifest.yaml").read_text(encoding="utf-8"))
    entry = manifest["contracts"]["content-knowledge-bundle.v2"]

    assert entry["producer"] == "stock_content"
    assert entry["consumers"] == ["stock_agent"]
    assert entry["canonicalization_version"] == "content-bundle-c14n-v2"
    # This is the upstream Content schema lock.  The local vendored consumer
    # schema is synchronized in a separate packet, so do not accidentally
    # rewrite the producer declaration to match an older local copy here.
    assert entry["checksum"].casefold() == "sha256:23c1d9c6be131cba8f270f01f7f45eb5d3148ee219edf43d689f1c5707115800"
