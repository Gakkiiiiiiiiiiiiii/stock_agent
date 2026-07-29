from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from financial_agent.config import load_yaml_config


VIDEO_COLLECTIONS = [
    "financial_video_durable_v1_bge_m3",
    "financial_video_timed_v1_bge_m3",
    "financial_video_action_v1_bge_m3",
]


def check_collections(*, dry_run: bool = False, timeout: float = 10.0) -> dict:
    qdrant_config = load_yaml_config("qdrant.yaml").get("qdrant") or {}
    retrieval_config = load_yaml_config("retrieval.yaml").get("retrieval") or {}
    configured = qdrant_config.get("collections") or {}
    manifests = retrieval_config.get("collection_manifests") or {}
    errors: list[str] = []
    warnings: list[str] = []
    items: list[dict] = []

    for collection in VIDEO_COLLECTIONS:
        collection_cfg = configured.get(collection)
        manifest = manifests.get(collection)
        if not collection_cfg:
            errors.append(f"{collection}: missing from config/qdrant.yaml")
            continue
        if not manifest:
            errors.append(f"{collection}: missing from config/retrieval.yaml collection_manifests")
            continue
        expected_dimension = int(collection_cfg.get("vector_size") or 0)
        manifest_dimension = int(manifest.get("dimension") or 0)
        expected_distance = str(collection_cfg.get("distance") or "").upper()
        if expected_dimension != manifest_dimension:
            errors.append(f"{collection}: qdrant vector_size {expected_dimension} != manifest dimension {manifest_dimension}")
        if expected_distance != "COSINE":
            warnings.append(f"{collection}: expected Cosine distance, got {expected_distance or 'empty'}")
        items.append(
            {
                "collection": collection,
                "expected_dimension": expected_dimension,
                "expected_distance": expected_distance,
                "manifest": manifest,
                "status": "dry_run" if dry_run else "pending_remote_check",
            }
        )

    if dry_run or errors:
        return {"ok": not errors, "dry_run": dry_run, "items": items, "errors": errors, "warnings": warnings}

    url = os.getenv("QDRANT_URL") or qdrant_config.get("url") or "http://localhost:6333"
    api_key = os.getenv("QDRANT_API_KEY") or qdrant_config.get("api_key")
    headers = {"api-key": api_key} if api_key else {}
    remote = _fetch_remote_collections(str(url).rstrip("/"), headers=headers, timeout=timeout)
    remote_by_name = {item["name"]: item for item in remote}
    for item in items:
        collection = item["collection"]
        remote_item = remote_by_name.get(collection)
        if remote_item is None:
            item["status"] = "missing"
            errors.append(f"{collection}: missing in Qdrant")
            continue
        actual_dimension = remote_item.get("dimension")
        actual_distance = str(remote_item.get("distance") or "").upper()
        item["actual_dimension"] = actual_dimension
        item["actual_distance"] = actual_distance
        if actual_dimension != item["expected_dimension"]:
            item["status"] = "dimension_mismatch"
            errors.append(f"{collection}: dimension mismatch {actual_dimension} != {item['expected_dimension']}")
            continue
        if actual_distance and actual_distance != item["expected_distance"]:
            item["status"] = "distance_mismatch"
            errors.append(f"{collection}: distance mismatch {actual_distance} != {item['expected_distance']}")
            continue
        item["status"] = "ok"

    return {"ok": not errors, "dry_run": False, "items": items, "errors": errors, "warnings": warnings}


def _fetch_remote_collections(url: str, *, headers: dict[str, str], timeout: float) -> list[dict[str, Any]]:
    response = httpx.get(f"{url}/collections", headers=headers, timeout=timeout)
    response.raise_for_status()
    names = [item["name"] for item in (response.json().get("result") or {}).get("collections", [])]
    result = []
    for name in names:
        detail = httpx.get(f"{url}/collections/{name}", headers=headers, timeout=timeout)
        detail.raise_for_status()
        payload = detail.json().get("result") or {}
        params = ((payload.get("config") or {}).get("params") or {}).get("vectors") or {}
        if isinstance(params, dict) and "size" not in params:
            first_vector = next(iter(params.values()), {})
            params = first_vector if isinstance(first_vector, dict) else {}
        result.append(
            {
                "name": name,
                "dimension": int(params.get("size")) if params.get("size") is not None else None,
                "distance": params.get("distance"),
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Qdrant collections required by video KnowledgeUnit indexing.")
    parser.add_argument("--dry-run", action="store_true", help="Only validate local qdrant/retrieval config consistency.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        result = check_collections(dry_run=args.dry_run, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for item in result["items"]:
                dimension = item.get("actual_dimension", item.get("expected_dimension"))
                distance = item.get("actual_distance", item.get("expected_distance"))
                print(f"{item['status'].upper()} {item['collection']} dimension={dimension} distance={distance}")
            for warning in result["warnings"]:
                print(f"WARN {warning}", file=sys.stderr)
            for error in result["errors"]:
                print(f"ERROR {error}", file=sys.stderr)
        return 0 if result["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
