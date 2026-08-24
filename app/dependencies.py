from __future__ import annotations

import os
import time
from datetime import date

from app.admin_service import AdminContentService
from app.agent_orchestrator import AgentOrchestrator
from app.chat_history_service import ChatHistoryService
from app.decision_runtime import DecisionRuntime
from app.fallback_orchestrator import LocalFallbackOrchestrator
from engines.market.trading_clock import (
    QuantTradingCalendarAdapter,
    TradingClock,
    configure_default_clock,
    get_default_clock,
)
from services.evidence.gateway import EvidenceGateway
from services.subsystems import get_content_client, get_factor_client, get_quant_client
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
    # Legacy Qdrant/retrieval initialization is intentionally not part of the
    # Decision Authority startup path. Content evidence is read through the
    # remote content client; DecisionMemory is owned by its dedicated repository.


# Shared service singletons (P0-05 / §28)：app.api 与各 app.routers 统一从这里取。
# 注意：routers 必须在调用时通过 ``app.dependencies`` 模块属性读取这些对象，
# 以便测试可以整体替换（monkeypatch.setattr("app.dependencies.<name>", fake)）。
content_client = get_content_client()
factor_client = get_factor_client()
quant_client = get_quant_client()


def _quant_calendar_fetch(start: date, end: date) -> dict:
    """Fetch calendar data from quant, the sole production calendar authority."""

    return quant_client.get_trading_calendar(
        start.isoformat(),
        end.isoformat(),
        market_code="CN_A",
    )


def configure_trading_clock(*, offline: bool = False) -> None:
    """Install the process-wide clock used by decision contracts.

    Production composition always uses the read-only quant calendar adapter. A
    degraded weekday calendar is available only when an offline/test caller
    explicitly opts in; a failed quant fetch is never silently replaced.
    """

    if offline:
        configure_default_clock(TradingClock())
        return
    configure_default_clock(TradingClock(calendar=QuantTradingCalendarAdapter(_quant_calendar_fetch)))


# Configure before contract objects are constructed. Offline mode is explicit
# and intended for tests/local replay only; normal deployment remains quant-backed.
configure_trading_clock(offline=os.getenv("STOCK_AGENT_OFFLINE_MODE") == "1")

# Construct consumers only after the shared clock has been installed. This
# prevents an orchestrator/runtime from retaining a private degraded clock.
_shared_clock = get_default_clock()
evidence_gateway = EvidenceGateway(
    quant_client=quant_client,
    factor_client=factor_client,
    content_client=content_client,
    clock=_shared_clock,
)
orchestrator = AgentOrchestrator(
    runtime=DecisionRuntime(
        clock=_shared_clock,
        evidence_gateway=evidence_gateway,
        fallback=LocalFallbackOrchestrator(clock=_shared_clock),
    )
)
admin_service = AdminContentService()
chat_history_service = ChatHistoryService()
