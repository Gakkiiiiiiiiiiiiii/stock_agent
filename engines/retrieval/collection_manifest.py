from __future__ import annotations

from engines.retrieval.embedder import EmbeddingMetadata
from financial_agent.config import load_yaml_config


class CollectionManifestError(ValueError):
    pass


def expected_manifest(collection: str) -> dict:
    retrieval = load_yaml_config("retrieval.yaml").get("retrieval") or {}
    manifests = retrieval.get("collection_manifests") or {}
    return dict(manifests.get(collection) or {})


def validate_embedding_manifest(collection: str, metadata: EmbeddingMetadata) -> None:
    manifest = expected_manifest(collection)
    if not manifest:
        return
    mismatches = []
    for field, actual in {
        "provider": metadata.provider,
        "model": metadata.model,
        "dimension": metadata.dimension,
        "semantic": metadata.semantic,
    }.items():
        expected = manifest.get(field)
        if expected != actual:
            mismatches.append(f"{field}: expected {expected!r}, got {actual!r}")
    if mismatches:
        raise CollectionManifestError(f"embedding manifest mismatch for {collection}: " + "; ".join(mismatches))
