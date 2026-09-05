from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CreateDecisionCommand:
    portfolio_id: str
    idempotency_key: str
    payload: dict[str, Any]

    @property
    def request_hash(self) -> str:
        encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()
