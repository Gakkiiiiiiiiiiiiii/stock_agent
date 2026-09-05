"""Bundle assembly port: freeze inputs before formal calculation."""
from __future__ import annotations

import hashlib
import json
from typing import Any


class EvidenceBundleAssembler:
    def freeze(self, *, market: Any, factor: Any, content: Any, lineage: dict[str, Any]) -> dict[str, Any]:
        value = {"market": market, "factor": factor, "content": content, "lineage": lineage}
        value["bundle_hash"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        return value
