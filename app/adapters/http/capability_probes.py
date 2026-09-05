from __future__ import annotations

from typing import Any

import httpx

from app.ports.upstream_capabilities import CapabilityStatus


class HttpCapabilityProbe:
    """Read-only capability probe; versions are never guessed or downgraded."""
    def __init__(self, component: str, base_url: str, *, timeout: float = 3.0):
        self.component, self.base_url, self.timeout = component, base_url.rstrip("/"), timeout

    def check(self) -> CapabilityStatus:
        try:
            response = httpx.get(f"{self.base_url}/capabilities", timeout=self.timeout)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return CapabilityStatus(
                self.component, True, contract=payload.get("contract"),
                snapshot_age_seconds=payload.get("snapshot_age_seconds"),
                quality=payload.get("quality"), pit=payload.get("pit"),
                reason_code=payload.get("reason_code"),
            )
        except (httpx.TimeoutException, TimeoutError):
            return CapabilityStatus(self.component, False, reason_code="UPSTREAM_TIMEOUT")
        except (httpx.HTTPError, ValueError, TypeError):
            return CapabilityStatus(self.component, False, reason_code="CAPABILITY_INVALID")
