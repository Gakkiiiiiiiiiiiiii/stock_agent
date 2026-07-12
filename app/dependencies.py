from __future__ import annotations

import time

from engines.retrieval.qdrant_client import FinancialQdrantClient
from storage.bootstrap import create_all


def init_application() -> None:
    last_error = None
    for _ in range(30):
        try:
            create_all()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    if last_error is not None:
        raise last_error
    try:
        FinancialQdrantClient().ensure_collections()
    except Exception:
        # Local tests or first boot without Qdrant should still allow the API to start.
        pass
