from __future__ import annotations

from datetime import datetime, timezone


def build_data_boundary_snapshot(source: str = "market_data_sync") -> dict:
    return {"source": source, "data_cutoff_time": datetime.now(timezone.utc).isoformat()}

