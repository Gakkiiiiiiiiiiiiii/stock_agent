"""FastAPI 应用装配层（设计文档 P0-05 / §28）。

本模块只负责：FastAPI app 创建、lifespan、中间件、健康检查/metrics 端点，
以及 app.include_router(...) 挂载各域路由（app/routers/）。
业务路由处理函数已全部迁移到 app/routers/，共享服务对象在 app/dependencies.py。

向后兼容：orchestrator / admin_service / chat_history_service 与
VALID_* 枚举在此再导出，既有 ``from app.api import app, orchestrator`` 等用法继续可用。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.capability_profile import CapabilityProfile, resolve_capability_profile
from app.security import render_metrics, security_and_trace_middleware
from storage.db import session_scope

logger = logging.getLogger(__name__)
capability_profile = resolve_capability_profile()

# Profile selection precedes every formal router/dependency import. Keep these
# imports inside the FULL branch so an unavailable Quant/Factor deployment can
# still import and start the knowledge-only health surface.
from app.dependencies import init_application

if capability_profile is CapabilityProfile.FULL:
    from app.dependencies import (  # noqa: F401
        admin_service,
        chat_history_service,
        orchestrator,
    )
    from app.routers import (
        admin,
        agent,
        analysis_v2,
        audit,
        compatibility,
        content,
        decision,
        factor,
        market,
        portfolio,
        readiness,
        regime,
        retrieval,
    )
else:
    from app.routers import knowledge_conclusion
    from app.routers._shared import (  # noqa: F401
        MAX_API_LIST_LIMIT,
        VALID_KNOWLEDGE_KINDS,
        VALID_LIFECYCLE_STATUSES,
        VALID_TEMPORAL_CLASSES,
        VALID_VERIFICATION_STATUSES,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_application()
    yield


app = FastAPI(title="Stock Agent Investment Decision Authority", version="0.2.0", lifespan=lifespan)
app.middleware("http")(security_and_trace_middleware)

if capability_profile is CapabilityProfile.KNOWLEDGE_ONLY:
    @app.exception_handler(RequestValidationError)
    async def _knowledge_validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/api/v2/knowledge-conclusions"):
            return JSONResponse(status_code=422, content={
                "code": "INVALID_REQUEST", "message": "knowledge conclusion request is invalid",
                "trace_id": getattr(request.state, "trace_id", "unknown"), "retryable": False,
            })
        return JSONResponse(status_code=422, content={"detail": "request validation failed"})

# 域路由挂载（§28）：knowledge-only does not expose an analysis or formal
# decision surface. Its conclusion router is intentionally added by SA-02.
if capability_profile is CapabilityProfile.FULL:
    for _router in (
        agent.router,
        market.router,
        regime.router,
        retrieval.router,
        portfolio.router,
        decision.router,
        factor.router,
        content.router,
        admin.router,
        analysis_v2.router,
        compatibility.router,
        readiness.router,
        audit.router,
    ):
        app.include_router(_router)
else:
    # Keep the knowledge-only route inventory flat: authority-boundary checks
    # inspect every path and this router has no prefix/dependency transform.
    app.router.routes.extend(knowledge_conclusion.router.routes)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


SERVICE_NAME = "stock_agent"
SERVICE_VERSION = "0.2.0"
FULL_CONTRACT_VERSIONS = [
    "evidence.v1", "decision-input.v1", "investment-proposal.v2", "investment-decision.v2",
    "decision.snapshot.v3", "replay.v1", "replay.v2", "decision-outcome.v1", "decision-review.v1",
    "decision-memory.v1", "market-data.v1", "factor.v1", "content.v1", "backtest.v1",
    "specialist-artifact.v2", "evidence-synthesis.v1", "decision-quality.v2",
    "formal-decision.v2", "execution-authorization.v1", "decision-lineage.v1",
]

CONTRACT_VERSIONS = FULL_CONTRACT_VERSIONS if capability_profile is CapabilityProfile.FULL else []


@app.get("/health/version")
def health_version() -> dict:
    # §106 Release Version：每个服务暴露自身版本与契约版本清单。
    return {
        "service": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "git_commit": os.getenv("AGENT_GIT_COMMIT", "unknown"),
        "profile": capability_profile.value,
        "contract_versions": CONTRACT_VERSIONS,
    }


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(response: Response) -> dict:
    checks = _ready_checks()
    required = _required_ready_checks()
    ready = all(checks.get(name) == "ok" for name in required)
    if not ready:
        response.status_code = 503
    return {"status": "ok" if ready else "degraded", "checks": checks}



@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(render_metrics(), media_type="text/plain")


def _ready_checks() -> dict[str, str]:
    checks = {"api": "ok"}
    checks["postgres"] = _check_postgres()
    checks["redis"] = _check_redis(os.getenv("REDIS_URL", ""))
    return checks


def _required_ready_checks() -> set[str]:
    configured = os.getenv("READY_REQUIRED_CHECKS")
    if configured is None:
        required = {"api", "postgres"}
        if os.getenv("REDIS_URL"):
            required.add("redis")
        return required
    raw = configured
    return {item.strip() for item in raw.split(",") if item.strip()}


def _check_postgres() -> str:
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return "ok"
    except SQLAlchemyError as exc:
        logger.warning("ready check failed: postgres: %s", exc)
        return "failed"


def _check_schema() -> str:
    try:
        from storage.bootstrap import verify_schema

        verify_schema()
        return "ok"
    except (OSError, RuntimeError, SQLAlchemyError) as exc:
        logger.warning("ready check failed: schema: %s", exc)
        return "failed"


def _check_http(url: str, api_key: str | None = None) -> str:
    headers = {"api-key": api_key} if api_key else None
    try:
        response = httpx.get(url, headers=headers, timeout=3)
        response.raise_for_status()
        return "ok"
    except httpx.HTTPError as exc:
        logger.warning("ready check failed: http endpoint %s: %s", _redact_url(url), exc)
        return "failed"


def _check_embedding() -> str:
    base_url = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8001/v1").rstrip("/")
    health_url = base_url.removesuffix("/v1")
    return _check_http(f"{health_url}/health")


def _check_redis(redis_url: str) -> str:
    if not redis_url:
        return "skipped"
    try:
        import redis
    except ImportError as exc:
        logger.warning("ready check failed: redis import: %s", exc)
        return "failed"
    try:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        return "ok" if client.ping() else "failed"
    except (OSError, redis.RedisError) as exc:
        logger.warning("ready check failed: redis: %s", exc)
        return "failed"


def _redact_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(password="***") if parsed.password else parsed)
    except (TypeError, ValueError):
        return "<invalid-url>"
