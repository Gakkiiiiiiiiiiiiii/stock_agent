"""Minimal readiness proof for the three-repository integration Compose stack."""

from __future__ import annotations

import time
from urllib.error import URLError
from urllib.request import urlopen


ENDPOINTS = (
    "http://stock-agent-api:8000/health/ready",
    "http://stock-content-api:8100/healthz",
    "http://stock-factor-api:8200/healthz",
    "http://market-data-service:8012/health/live",
)


def main() -> None:
    deadline = time.monotonic() + 180
    pending = set(ENDPOINTS)
    while pending and time.monotonic() < deadline:
        for endpoint in tuple(pending):
            try:
                with urlopen(endpoint, timeout=3) as response:  # noqa: S310 - fixed local Compose endpoints
                    if response.status < 400:
                        pending.remove(endpoint)
            except URLError:
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit(f"services did not become ready: {sorted(pending)}")


if __name__ == "__main__":
    main()
