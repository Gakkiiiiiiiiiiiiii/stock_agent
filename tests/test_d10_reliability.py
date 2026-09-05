from __future__ import annotations

from datetime import UTC, datetime, time

import httpx
import pytest
from pydantic import BaseModel

from app.model_gateway import (
    CircuitBreaker,
    MetricsRecorder,
    ModelGateway,
    ModelRoute,
    RetryPolicy,
    StructuredOutputError,
    TokenCostBudget,
    TraceContext,
)
from app.model_gateway.budget import BudgetExceededError
from app.model_gateway.metrics import render_global_metrics
from clients._http import (
    CONTRACT_MISMATCH,
    DEPENDENCY_TIMEOUT,
    DEPENDENCY_UNAVAILABLE,
    INVALID_SNAPSHOT,
    STALE_DATA,
    DependencyError,
    SubsystemHttpClient,
)
from engines.market.trading_clock import (
    CalendarSession,
    QuantTradingCalendarAdapter,
    SnapshotTradingCalendar,
    TradingClock,
)


class _Output(BaseModel):
    score: float


def test_gateway_retries_then_uses_primary_and_validates_structured_output():
    calls = []

    def primary(payload):
        calls.append(payload)
        if len(calls) == 1:
            from app.model_gateway.retry import RetryableError
            raise RetryableError("busy", status_code=429, retry_after=0)
        return {"choices": [{"message": {"content": '{"score":0.8}'}}], "usage": {"completion_tokens": 3}}

    metrics = MetricsRecorder()
    gateway = ModelGateway(primary, retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0), metrics=metrics)
    result = gateway.complete(prompt="score", output_model=_Output, max_tokens=20, trace=TraceContext(decision_id="d1"))
    assert result.structured_output == {"score": 0.8}
    assert len(calls) == 2
    assert calls[0]["_trace_headers"]["X-Decision-Id"] == "d1"
    assert any(item.name == "model_tokens_output" for item in metrics.events)


def test_gateway_structured_validation_rejects_invalid_payload():
    gateway = ModelGateway(lambda _: {"choices": [{"message": {"content": '{"bad":1}'}}]}, retry_policy=RetryPolicy(max_attempts=1))
    with pytest.raises(StructuredOutputError) as exc:
        gateway.complete(prompt="x", output_model=_Output, max_tokens=1)
    assert "structured" in str(exc.value).lower()


def test_gateway_primary_schema_invalid_falls_back_to_valid_route():
    primary_calls = []

    def primary(_payload):
        primary_calls.append(True)
        return {"choices": [{"message": {"content": '{"bad":1}'}}], "usage": {"completion_tokens": 2}}

    def fallback(_payload):
        return {"choices": [{"message": {"content": '{"score":0.9}'}}], "usage": {"completion_tokens": 3}}

    gateway = ModelGateway(
        ModelRoute(name="primary", provider="p1", model="m1", endpoint=primary),
        ModelRoute(name="fallback", provider="p2", model="m2", endpoint=fallback),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    result = gateway.complete(prompt="x", output_model=_Output, max_tokens=4)
    assert len(primary_calls) == 2
    assert result.route == "fallback" and result.structured_output == {"score": 0.9}
    assert result.as_dict()["provider"] == "p2" and result.as_dict()["model"] == "m2"


def test_gateway_result_accounts_primary_repairs_and_fallback_usage_cost():
    primary_responses = iter([
        {"choices": [{"message": {"content": '{"bad":1}'}}], "usage": {"prompt_tokens": 11, "completion_tokens": 2}},
        {"choices": [{"message": {"content": '{"still_bad":1}'}}], "usage": {"prompt_tokens": 13, "completion_tokens": 3}},
    ])
    metrics = MetricsRecorder()
    metrics.clear()
    gateway = ModelGateway(
        ModelRoute(name="primary", provider="p1", model="m1", endpoint=lambda _payload: next(primary_responses), cost_per_input_token=0.01, cost_per_output_token=0.02),
        ModelRoute(name="fallback", provider="p2", model="m2", endpoint=lambda _payload: {"choices": [{"message": {"content": '{"score":0.9}'}}], "usage": {"prompt_tokens": 17, "completion_tokens": 5}}, cost_per_input_token=0.01, cost_per_output_token=0.02),
        retry_policy=RetryPolicy(max_attempts=1), metrics=metrics,
    )
    result = gateway.complete(prompt="x", output_model=_Output, max_tokens=8)
    assert result.input_tokens == 41
    assert result.output_tokens == 10
    assert result.cost == pytest.approx(0.61)
    assert gateway.budget.input_tokens == 41 and gateway.budget.output_tokens == 10
    assert gateway.budget.cost == pytest.approx(0.61)
    input_events = [event for event in metrics.snapshot() if event.name == "model_tokens_input"]
    output_events = [event for event in metrics.snapshot() if event.name == "model_tokens_output"]
    assert sum(event.value for event in input_events) == 41
    assert sum(event.value for event in output_events) == 10


def test_gateway_resolves_auditable_model_version_from_success_response():
    gateway = ModelGateway(
        ModelRoute(name="primary", provider="p", model="route-model", endpoint=lambda _payload: {"model": "served-model", "choices": [{"message": {"content": "ok"}}]}),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    result = gateway.complete(prompt="x", max_tokens=1)
    assert result.model == "route-model"
    assert result.model_version == "served-model"


def test_gateway_prefers_explicit_route_or_response_version_identity():
    explicit = ModelGateway(
        ModelRoute(name="primary", provider="p", model="route-model", model_version="route-v2", endpoint=lambda _payload: {"model": "served-model", "system_fingerprint": "fp", "choices": [{"message": {"content": "ok"}}]}),
        retry_policy=RetryPolicy(max_attempts=1),
    ).complete(prompt="x", max_tokens=1)
    fingerprint = ModelGateway(
        ModelRoute(name="primary", provider="p", model="route-model", endpoint=lambda _payload: {"model": "served-model", "system_fingerprint": "fp", "choices": [{"message": {"content": "ok"}}]}),
        retry_policy=RetryPolicy(max_attempts=1),
    ).complete(prompt="x", max_tokens=1)
    assert explicit.model_version == "route-v2"
    assert fingerprint.model_version == "fp"


def test_gateway_budget_blocks_structured_repair_before_second_provider_call():
    calls = []
    def invalid(_payload):
        calls.append(True)
        return {"choices": [{"message": {"content": '{"bad":1}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    budget = TokenCostBudget(max_total_tokens=60)
    gateway = ModelGateway(invalid, retry_policy=RetryPolicy(max_attempts=1), budget=budget)
    with pytest.raises(BudgetExceededError):
        gateway.complete(prompt="x", output_model=_Output, max_tokens=4)
    assert len(calls) == 1


def test_metrics_are_aggregated_and_rendered_as_one_series():
    metrics = MetricsRecorder()
    metrics.clear()
    metrics.increment("dependency_degraded_total", dependency="quant")
    metrics.increment("dependency_degraded_total", dependency="quant")
    assert len([item for item in metrics.snapshot() if item.name == "dependency_degraded_total"]) == 1
    rendered = render_global_metrics()
    assert "dependency_degraded_total" in rendered


def test_gateway_falls_back_after_primary_failure():
    seen = []

    def broken(_):
        from app.model_gateway.retry import RetryableError
        raise RetryableError("down", status_code=503)

    def fallback(payload):
        seen.append(payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    gateway = ModelGateway(ModelRoute(name="primary", provider="p1", model="m1", endpoint=broken), ModelRoute(name="fallback", provider="p2", model="m2", endpoint=fallback), retry_policy=RetryPolicy(max_attempts=1))
    result = gateway.complete(prompt="x", max_tokens=1)
    assert result.route == "fallback"
    assert result.provider == "p2" and result.model == "m2"
    assert seen


def test_gateway_budget_is_checked_before_provider_call():
    called = []
    gateway = ModelGateway(lambda _: called.append(True) or {})
    gateway.budget = TokenCostBudget(max_total_tokens=1)
    with pytest.raises(BudgetExceededError):
        gateway.complete(prompt="a long prompt that exceeds one token", max_tokens=1)
    assert not called


def test_gateway_cost_budget_preflights_route_rates_without_call():
    called = []
    route = ModelRoute(name="primary", provider="p", model="m", endpoint=lambda _: called.append(True) or {}, cost_per_input_token=1.0, cost_per_output_token=1.0)
    gateway = ModelGateway(route, budget=TokenCostBudget(max_cost=0.1), retry_policy=RetryPolicy(max_attempts=1))
    with pytest.raises(BudgetExceededError):
        gateway.complete(prompt="costly", max_tokens=2)
    assert called == []


def test_http_retry_after_and_trace_headers_are_injected():
    responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"contract_version": "market.v1", "data": {"ok": True}})]
    seen = []

    def request(method, url, **kwargs):
        seen.append(kwargs)
        response = responses.pop(0)
        response.request = httpx.Request(method, url)
        return response

    sleeps = []
    client = SubsystemHttpClient("http://quant", retries=1, request_fn=request, sleeper=sleeps.append)
    result = client.request("GET", "/snapshot", trace_context={"trace_id": "t", "decision_id": "d", "bundle_id": "b", "agent_run_id": "a", "proposal_id": "p", "snapshot_id": "s"}, contract_version="market.v1")
    assert result["data"]["ok"] is True
    assert sleeps == [0.0]
    headers = seen[0]["headers"]
    assert headers["X-Trace-Id"] == "t"
    assert headers["X-Decision-Id"] == "d"
    assert headers["X-Bundle-Id"] == "b"
    assert headers["X-Contract-Version"] == "market.v1"


def test_http_circuit_breaker_normalizes_unavailable():
    def request(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    client = SubsystemHttpClient("http://quant", retries=0, request_fn=request, circuit_breaker=CircuitBreaker(failure_threshold=1))
    with pytest.raises(DependencyError) as exc:
        client.request("GET", "/snapshot")
    assert exc.value.code == DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize("body,expected,kwargs", [
    ({"status": "STALE"}, STALE_DATA, {}),
    ({"contract_version": "wrong"}, CONTRACT_MISMATCH, {"contract_version": "market.v1"}),
    ({"snapshot_id": "other"}, INVALID_SNAPSHOT, {"expected_snapshot_id": "wanted"}),
])
def test_http_normalizes_contract_staleness_and_snapshot_errors(body, expected, kwargs):
    def request(method, url, **request_kwargs):
        response = httpx.Response(200, json=body, request=httpx.Request(method, url))
        return response
    client = SubsystemHttpClient("http://quant", retries=0, request_fn=request)
    with pytest.raises(DependencyError) as exc:
        client.request("GET", "/snapshot", **kwargs)
    assert exc.value.code == expected


def test_http_timeout_code_and_breaker_half_open():
    now = [0.0]
    def request(*_args, **_kwargs):
        raise httpx.ReadTimeout("read timeout")
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=1, clock=lambda: now[0])
    client = SubsystemHttpClient("http://quant", retries=0, request_fn=request, circuit_breaker=breaker, clock=lambda: now[0])
    with pytest.raises(DependencyError) as exc:
        client.request("GET", "/snapshot")
    assert exc.value.code == DEPENDENCY_TIMEOUT
    assert not breaker.allow()
    now[0] = 2.0
    assert breaker.allow() and breaker.state.value == "HALF_OPEN"


def test_remote_clients_forward_trace_context():
    from clients.content_client import RemoteContentClient
    from clients.factor_client import RemoteFactorClient
    from clients.quant_client import RemoteQuantClient

    seen = []
    def fake(self, method, path, **kwargs):
        seen.append(kwargs.get("trace_context"))
        return {"data": {}}
    for client, call in ((RemoteQuantClient("http://q"), lambda c: c.get_market_snapshot("s", trace_context={"trace_id": "t"})), (RemoteFactorClient("http://f"), lambda c: c.get_factor("f", trace_context={"trace_id": "t"})), (RemoteContentClient("http://c"), lambda c: c.search_video_knowledge("q", trace_context={"trace_id": "t"}))):
        client.request = fake.__get__(client, type(client))
        call(client)
    assert seen == [{"trace_id": "t"}, {"trace_id": "t"}, {"trace_id": "t"}]


def test_remote_clients_propagate_trace_headers_through_real_http_adapter():
    from clients.content_client import RemoteContentClient
    from clients.factor_client import RemoteFactorClient
    from clients.quant_client import RemoteQuantClient

    captured = []
    def request(method, url, **kwargs):
        captured.append(kwargs["headers"])
        if "/snapshots/" in url:
            body = {"contract_version": "market-data.v1", "snapshot_id": "s", "data": {"as_of": "2026-08-24T00:00:00Z", "available_at": "2026-08-24T00:00:00Z"}}
        else:
            contract = "factor.v1" if "/factors/" in url else "content.v1"
            body = {"contract_version": contract, "data": {"as_of": "2026-08-24T00:00:00Z", "available_at": "2026-08-24T00:00:00Z"}}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))

    context = {"trace_id": "trace-1", "decision_id": "decision-1", "bundle_id": "bundle-1", "agent_run_id": "agent-1", "proposal_id": "proposal-1", "snapshot_id": "s"}
    RemoteQuantClient("http://q", request_fn=request).get_market_snapshot("s", trace_context=context)
    RemoteFactorClient("http://f", request_fn=request).get_factor("factor-1", trace_context=context)
    RemoteContentClient("http://c", request_fn=request).search_video_knowledge("query", trace_context=context)
    assert all(headers["X-Trace-Id"] == "trace-1" and headers["X-Decision-Id"] == "decision-1" and headers["X-Bundle-Id"] == "bundle-1" and headers["X-Agent-Run-Id"] == "agent-1" and headers["X-Proposal-Id"] == "proposal-1" and headers["X-Snapshot-Id"] == "s" for headers in captured)


def test_evidence_gateway_preserves_dependency_error_codes_and_trace_metadata():
    from services.evidence.gateway import EvidenceGateway
    class Quant:
        def get_market_snapshot(self, **kwargs):
            assert kwargs["trace_context"]["bundle_id"] == "b"
            raise DependencyError(STALE_DATA, "stale")
    _, statuses = EvidenceGateway(quant_client=Quant(), clock=lambda: datetime(2026, 8, 24, tzinfo=UTC)).collect(
        [{"source_system": "quant", "operation": "market_snapshot", "evidence_type": "MARKET_SNAPSHOT"}],
        decision_time=datetime(2026, 8, 24, tzinfo=UTC), trace_context={"bundle_id": "b"},
    )
    assert statuses[0].status.value == "STALE" and statuses[0].reason_codes == [STALE_DATA]


def test_analysis_client_passes_formal_trace_to_gateway_transport():
    from app.model_providers import AnalysisModelClient, AnalysisModelSettings
    captured = {}
    def handler(request):
        captured.update(request.headers)
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]})
    client = AnalysisModelClient(
        settings=AnalysisModelSettings(provider="openai_compatible", model="m", base_url="http://model", api_key="key"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.complete("x", trace=TraceContext(trace_id="t", decision_id="d", bundle_id="b"))
    assert captured["x-trace-id"] == "t" and captured["x-decision-id"] == "d" and captured["x-bundle-id"] == "b"
def test_business_clock_has_no_qmt_calendar_dependency():
    clock = TradingClock(now_fn=lambda: datetime(2026, 8, 24, 1, tzinfo=UTC))
    assert clock.current_trading_session("CN_A").isoformat() == "2026-08-24"
    from app import skill_contract
    assert skill_contract.ExchangeTradingCalendar.__module__ == "engines.market.trading_clock"


def test_injected_calendar_handles_holiday_half_day_and_phases():
    calendar = SnapshotTradingCalendar([
        CalendarSession(datetime(2026, 8, 24, tzinfo=UTC).date(), open_time=time(9, 30), close_time=time(11, 30)),
        CalendarSession(datetime(2026, 8, 25, tzinfo=UTC).date(), is_open=False),
        CalendarSession(datetime(2026, 8, 26, tzinfo=UTC).date()),
    ])
    clock = TradingClock(calendar=calendar, now_fn=lambda: datetime(2026, 8, 24, 1, 0, tzinfo=UTC))
    assert clock.current_trading_session("CN_A").isoformat() == "2026-08-24"
    assert clock.session_phase() == "PRE_OPEN"
    assert clock.session_phase(datetime(2026, 8, 24, 3, 0, tzinfo=UTC)) == "OPEN"
    assert clock.session_phase(datetime(2026, 8, 24, 5, 0, tzinfo=UTC)) == "AFTER_CLOSE"
    assert clock.current_trading_session("CN_A", datetime(2026, 8, 25, 2, tzinfo=UTC)).isoformat() == "2026-08-24"
    assert clock.session_phase(datetime(2026, 8, 25, 2, tzinfo=UTC)) == "CLOSED"


def test_quant_calendar_adapter_uses_contract_shape_and_caches_fetch():
    class Quant:
        def __init__(self):
            self.calls = 0
        def get_trading_calendar(self, start, end, *, market_code="CN_A"):
            self.calls += 1
            return {"contract_version": "market-calendar.v1", "items": [{"date": "2026-08-24", "is_open": True}, {"date": "2026-08-25", "is_open": False}]}
    quant = Quant()
    adapter = QuantTradingCalendarAdapter(quant)
    clock = TradingClock(calendar=adapter, now_fn=lambda: datetime(2026, 8, 25, 2, tzinfo=UTC))
    assert clock.session_phase() == "CLOSED"
    assert clock.session_phase(datetime(2026, 8, 25, 5, tzinfo=UTC)) == "CLOSED"
    assert quant.calls == 1


def test_calendar_session_navigation_skips_holiday_and_weekend():
    calendar = SnapshotTradingCalendar([
        CalendarSession(datetime(2026, 8, 21, tzinfo=UTC).date()),
        CalendarSession(datetime(2026, 8, 24, tzinfo=UTC).date()),
        CalendarSession(datetime(2026, 8, 25, tzinfo=UTC).date(), is_open=False),
        CalendarSession(datetime(2026, 8, 26, tzinfo=UTC).date()),
    ])
    assert calendar.next_session(datetime(2026, 8, 21, tzinfo=UTC).date()).isoformat() == "2026-08-24"
    assert calendar.previous_session(datetime(2026, 8, 26, tzinfo=UTC).date()).isoformat() == "2026-08-24"
    assert calendar.advance_sessions(datetime(2026, 8, 21, tzinfo=UTC).date(), 2).isoformat() == "2026-08-26"


def test_formal_model_adapter_accepts_only_bundle_and_returns_canonical_proposal():
    from app.claude_agent import ClaudeAgent
    from contracts.decision_input import build_bundle
    from contracts.evidence import Evidence, EvidenceQuality, EvidenceType, SourceSystem

    now = datetime.now(UTC)
    evidence = Evidence(evidence_type=EvidenceType.MARKET_SNAPSHOT, source_system=SourceSystem.QUANT, subject_type="market", subject_key="CN_A", as_of=now, available_at=now, contract_version="market.v1", payload={"value": 1}, quality_status=EvidenceQuality.VERIFIED)
    bundle = build_bundle(created_at=now, decision_time=now, task_type="daily", objective="x", evidence=[evidence])

    class FakeModel:
        settings = type("Settings", (), {"provider": "fake", "model": "deterministic"})()

        def complete(self, **_kwargs):
            return {"structured_output": {"proposal": {"subject_type": "THEME", "subject_key": "theme", "action": "BUY", "target_weight": 0.1, "confidence": 0.8, "horizon": {"period": "1M"}, "thesis": [{"statement": "supported", "evidence_refs": [evidence.evidence_id]}]}, "report": "supported"}}

    result = ClaudeAgent(client=FakeModel()).run_formal(user_query="x", context={"bundle_only": True, "allow_external_evidence_calls": False, "decision_input_bundle": bundle.model_dump(mode="json")})
    assert result["proposal"]["schema_version"] == "investment-proposal.v2"
    assert result["tool_calls"] == 0


def test_http_payload_trace_fields_are_not_promoted_to_audit_headers():
    captured = {}

    def request(method, url, **kwargs):
        captured.update(kwargs["headers"])
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    forged = {
        "trace_id": "forged-trace",
        "decision_id": "forged-decision",
        "bundle_id": "forged-bundle",
        "agent_run_id": "forged-agent",
        "proposal_id": "forged-proposal",
        "snapshot_id": "forged-snapshot",
    }
    SubsystemHttpClient("http://dependency", retries=0, request_fn=request).request("POST", "/read", payload=forged)

    assert captured["X-Trace-Id"] != "forged-trace"
    assert all(key not in captured for key in (
        "X-Decision-Id", "X-Bundle-Id", "X-Agent-Run-Id", "X-Proposal-Id", "X-Snapshot-Id",
    ))


def test_gateway_endpoint_typeerror_is_not_reinvoked_and_fallback_runs_once():
    primary_calls = []
    fallback_calls = []

    def primary(payload):
        primary_calls.append(payload)
        raise TypeError("provider implementation failure")

    def fallback(payload):
        fallback_calls.append(payload)
        return {"model": "fallback", "choices": [{"message": {"content": "ok"}}]}

    result = ModelGateway(
        primary,
        fallback,
        retry_policy=RetryPolicy(max_attempts=1),
    ).complete(prompt="hello")

    assert result.route == "fallback"
    assert len(primary_calls) == 1
    assert len(fallback_calls) == 1


def test_gateway_invokes_declared_kwargs_endpoint_without_positional_guessing():
    observed = {}

    def kwargs_endpoint(messages, model=None, **payload):
        payload["messages"] = messages
        payload["model"] = model
        observed.update(payload)
        return {"model": "kwargs-model", "choices": [{"message": {"content": "ok"}}]}

    result = ModelGateway(kwargs_endpoint, retry_policy=RetryPolicy(max_attempts=1)).complete(prompt="hello")

    assert result.model == "kwargs-model"
    assert observed["messages"][-1] == {"role": "user", "content": "hello"}


def test_gateway_open_breaker_rejections_do_not_extend_recovery_and_allow_fallback():
    now = [0.0]
    primary_calls = []
    fallback_calls = []
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=10.0, clock=lambda: now[0])

    def primary(_payload):
        primary_calls.append(now[0])
        if len(primary_calls) == 1:
            raise RuntimeError("primary unavailable")
        return {"model": "primary", "choices": [{"message": {"content": "ok"}}]}

    def fallback(_payload):
        fallback_calls.append(now[0])
        return {"model": "fallback", "choices": [{"message": {"content": "ok"}}]}

    gateway = ModelGateway(
        ModelRoute(name="primary", provider="p1", model="primary", endpoint=primary),
        ModelRoute(name="fallback", provider="p2", model="fallback", endpoint=fallback),
        circuit_breaker=breaker,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    assert gateway.complete(prompt="first").route == "fallback"
    assert breaker.state.value == "OPEN"
    opened_at = breaker.opened_at

    now[0] = 5.0
    assert gateway.complete(prompt="during-open-1").route == "fallback"
    now[0] = 9.0
    assert gateway.complete(prompt="during-open-2").route == "fallback"
    assert primary_calls == [0.0]
    assert fallback_calls == [0.0, 5.0, 9.0]
    assert breaker.opened_at == opened_at == 0.0

    now[0] = 10.0
    assert gateway.complete(prompt="half-open").route == "primary"
    assert primary_calls == [0.0, 10.0]
    assert breaker.state.value == "CLOSED"
