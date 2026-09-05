"""Print the registered v1 endpoint inventory and deprecation state."""
from __future__ import annotations

import json


def inventory() -> list[dict[str, str]]:
    return [
        {"path": "/api/v1/analyze/stock", "replacement": "/api/v2/analysis/stock", "authority": "ANALYSIS_ONLY", "status": "deprecated"},
        {"path": "/api/v1/analyze/theme", "replacement": "/api/v2/analysis/theme", "authority": "ANALYSIS_ONLY", "status": "deprecated"},
        {"path": "/api/v1/decisions", "replacement": "/api/v2/decisions", "authority": "COMPATIBILITY_READ_ONLY", "status": "410"},
    ]


if __name__ == "__main__":
    print(json.dumps(inventory(), ensure_ascii=False, indent=2))
