"""Legacy formal-decision composition, loaded only for the FULL profile."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.admin_service import AdminContentService
from app.agent_orchestrator import AgentOrchestrator
from app.application.audit.lineage_graph import LineageGraph
from app.application.decision.orchestrator import DecisionApplicationService
from app.chat_history_service import ChatHistoryService
from app.decision_runtime import DecisionRuntime
from app.fallback_orchestrator import LocalFallbackOrchestrator
from engines.market.trading_clock import (
    QuantTradingCalendarAdapter,
    TradingClock,
    configure_default_clock,
    get_default_clock,
)
from services.evidence.fixture_gateway import DeterministicFixtureEvidenceGateway
from services.evidence.gateway import EvidenceGateway
from services.subsystems import get_content_client, get_factor_client, get_quant_client
from storage.db import session_scope


@dataclass(frozen=True)
class FormalDecisionComponents:
    content_client: Any
    factor_client: Any
    quant_client: Any
    orchestrator: AgentOrchestrator
    admin_service: AdminContentService
    chat_history_service: ChatHistoryService
    lineage_graph: LineageGraph
    shared_clock: TradingClock


def build_formal_decision_components() -> FormalDecisionComponents:
    content_client = get_content_client()
    factor_client = get_factor_client()
    quant_client = get_quant_client()

    def quant_calendar_fetch(start: date, end: date) -> dict:
        return quant_client.get_trading_calendar(start.isoformat(), end.isoformat(), market_code="CN_A")

    # This existing explicit test/replay escape hatch is retained for FULL
    # only. It is not a knowledge-only global offline mode.
    offline = os.getenv("STOCK_AGENT_OFFLINE_MODE") == "1"
    configure_default_clock(TradingClock() if offline else TradingClock(calendar=QuantTradingCalendarAdapter(quant_calendar_fetch)))
    shared_clock = get_default_clock()
    application_service = DecisionApplicationService()
    evidence_gateway = (
        DeterministicFixtureEvidenceGateway()
        if os.getenv("STOCK_AGENT_DETERMINISTIC_FIXTURE") == "1"
        else EvidenceGateway(quant_client=quant_client, factor_client=factor_client, content_client=content_client, clock=shared_clock)
    )
    orchestrator = AgentOrchestrator(
        runtime=DecisionRuntime(
            clock=shared_clock,
            evidence_gateway=evidence_gateway,
            fallback=LocalFallbackOrchestrator(clock=shared_clock),
            application_service=application_service,
        )
    )
    return FormalDecisionComponents(
        content_client=content_client,
        factor_client=factor_client,
        quant_client=quant_client,
        orchestrator=orchestrator,
        admin_service=AdminContentService(),
        chat_history_service=ChatHistoryService(),
        lineage_graph=LineageGraph(persistent=True, session_factory=session_scope),
        shared_clock=shared_clock,
    )


def configure_trading_clock(*, offline: bool = False) -> None:
    """Retained public helper for FULL-profile formal safety regression tests."""

    from app import dependencies

    if offline:
        configure_default_clock(TradingClock())
        return
    configure_default_clock(
        TradingClock(
            calendar=QuantTradingCalendarAdapter(
                lambda start, end: dependencies.quant_client.get_trading_calendar(
                    start.isoformat(), end.isoformat(), market_code="CN_A"
                )
            )
        )
    )
