from __future__ import annotations

import os

from fastapi import APIRouter, Response

from app.adapters.http.capability_probes import HttpCapabilityProbe
from app.application.readiness.formal_policy import FormalReadinessPolicy
from app.application.readiness.service import FormalReadinessService
from financial_agent.utils import project_root

router = APIRouter(tags=["health"])
_SERVICE: FormalReadinessService | None = None
_SERVICE_MODE: tuple[str, str, str, str] | None = None


def _service() -> FormalReadinessService:
    global _SERVICE, _SERVICE_MODE
    mode = (
        os.getenv("STOCK_AGENT_DETERMINISTIC_FIXTURE", ""),
        os.getenv("QUANT_SERVICE_URL", ""),
        os.getenv("FACTOR_SERVICE_URL", ""),
        os.getenv("CONTENT_SERVICE_URL", ""),
    )
    if _SERVICE is not None and _SERVICE_MODE == mode:
        return _SERVICE
    # URLs are configuration, not fallback contracts. Missing URLs produce a
    # deterministic CAPABILITY_PROBE_MISSING result and therefore fail closed.
    probes = {
        "quant": HttpCapabilityProbe("quant", os.getenv("QUANT_SERVICE_URL", "")) if os.getenv("QUANT_SERVICE_URL") else None,
        "stock_factor": HttpCapabilityProbe("stock_factor", os.getenv("FACTOR_SERVICE_URL", "")) if os.getenv("FACTOR_SERVICE_URL") else None,
        "stock_content": HttpCapabilityProbe("stock_content", os.getenv("CONTENT_SERVICE_URL", "")) if os.getenv("CONTENT_SERVICE_URL") else None,
    }
    try:
        policy = FormalReadinessPolicy.from_manifest(project_root() / "contracts" / "platform-manifest.yaml")
    except ValueError as exc:
        policy = FormalReadinessPolicy(manifest_error=str(exc))
    expected = {
        "quant": os.getenv("QUANT_CONTRACT", "market-data.v1"),
        "stock_factor": os.getenv("FACTOR_CONTRACT", "factor.v1"),
        "stock_content": os.getenv("CONTENT_CONTRACT", "content.v1"),
    }
    if not policy.manifest_error:
        policy = FormalReadinessPolicy(
            policy_version=policy.policy_version, max_snapshot_age_seconds=policy.max_snapshot_age_seconds,
            require_pit=policy.require_pit, required_contracts=policy.required_contracts,
            expected_contracts={**policy.expected_contracts, **expected}, ttl_seconds=policy.ttl_seconds,
            manifest_checksums=policy.manifest_checksums,
        )
    _SERVICE = FormalReadinessService(
        {k: v for k, v in probes.items() if v is not None},
        policy,
        deterministic_fixture=os.getenv("STOCK_AGENT_DETERMINISTIC_FIXTURE") == "1",
    )
    _SERVICE_MODE = mode
    return _SERVICE


@router.get("/health/formal-decision-ready")
def formal_decision_ready(response: Response) -> dict:
    result = _service().check()
    if not result.ready:
        response.status_code = 503
    return result.as_dict()


@router.get("/health/analysis-ready")
def analysis_ready() -> dict:
    # Analysis is available as a degraded, explicitly non-authoritative view.
    result = _service().check()
    return {"ready": True, "degraded": not result.ready, "authority": "ANALYSIS_ONLY",
            "reason_codes": list(result.reason_codes), "components": result.components,
            "policy_version": result.policy_version}
