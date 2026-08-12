"""FastAPI 应用装配层（设计文档 P0-05 / §28）。

本模块只负责：FastAPI app 创建、lifespan、中间件、健康检查/metrics 端点，
以及 app.include_router(...) 挂载各域路由（app/routers/）。
业务路由处理函数已全部迁移到 app/routers/，共享服务对象在 app/dependencies.py。

向后兼容：orchestrator / admin_service / chat_history_service /
content_ingest_service 与 VALID_* 枚举在此再导出，
既有 ``from app.api import app, orchestrator`` 等用法继续可用。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from app.dependencies import (  # noqa: F401  (re-export，兼容旧引用)
    admin_service,
    chat_history_service,
    content_ingest_service,
    init_application,
    orchestrator,
)
from app.routers import admin, agent, content, decision, execution, factor, market, portfolio, regime, retrieval, stream
from app.routers._shared import (  # noqa: F401  (re-export，兼容旧引用)
    MAX_API_LIST_LIMIT,
    VALID_KNOWLEDGE_KINDS,
    VALID_LIFECYCLE_STATUSES,
    VALID_TEMPORAL_CLASSES,
    VALID_VERIFICATION_STATUSES,
)
from app.security import render_metrics, security_and_trace_middleware
from engines.market.qmt_bridge_client import QmtBridgeClient
from storage.db import session_scope

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_application()
    yield


app = FastAPI(title="Financial Analysis Agent", version="0.1.0", lifespan=lifespan)
app.middleware("http")(security_and_trace_middleware)

# 域路由挂载（§28）：routers 只做 参数解析 → 前置校验 → 调服务 → HTTP 响应。
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
    execution.router,
    stream.router,
):
    app.include_router(_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
    required = _required_ready_checks()
    checks = {"api": "ok"}
    checks["postgres"] = _check_postgres()
    checks["qdrant"] = _check_http(f"{os.getenv('QDRANT_URL', 'http://localhost:6333').rstrip('/')}/collections", api_key=os.getenv("QDRANT_API_KEY"))
    checks["redis"] = _check_redis(os.getenv("REDIS_URL", ""))
    checks["embedding"] = _check_embedding()
    checks["reranker"] = _check_http(f"{os.getenv('RERANKER_URL', 'http://localhost:8010').rstrip('/')}/health")
    if "qmt" in required or os.getenv("READY_CHECK_OPTIONAL_QMT", "false").lower() in {"1", "true", "yes"}:
        checks["qmt"] = _check_qmt()
    else:
        checks["qmt"] = "skipped"
    return checks


def _required_ready_checks() -> set[str]:
    configured = os.getenv("READY_REQUIRED_CHECKS")
    if configured is None:
        required = {"api", "postgres", "qdrant", "embedding", "reranker"}
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: postgres: %s", exc)
        return "failed"


def _check_http(url: str, api_key: str | None = None) -> str:
    headers = {"api-key": api_key} if api_key else None
    try:
        response = httpx.get(url, headers=headers, timeout=3)
        response.raise_for_status()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: http endpoint %s: %s", _redact_url(url), exc)
        return "failed"


def _check_embedding() -> str:
    base_url = os.getenv("EMBEDDING_BASE_URL", "http://localhost:8001/v1").rstrip("/")
    health_url = base_url[:-3] if base_url.endswith("/v1") else base_url
    return _check_http(f"{health_url}/health")


def _check_redis(redis_url: str) -> str:
    if not redis_url:
        return "skipped"
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        return "ok" if client.ping() else "failed"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: redis: %s", exc)
        return "failed"


def _check_qmt() -> str:
    try:
        QmtBridgeClient().healthcheck()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ready check failed: qmt: %s", exc)
        return "failed"


def _redact_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(password="***") if parsed.password else parsed)
    except Exception:
        return "<invalid-url>"
