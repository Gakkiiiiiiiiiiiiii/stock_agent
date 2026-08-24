from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from time import monotonic, sleep
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .budget import BudgetExceededError, TokenCostBudget
from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .metrics import MetricsRecorder, TraceContext
from .retry import RetryPolicy, RetryableError, retry_call
from .routing import ModelRoute, ModelRouter


class ModelGatewayError(RuntimeError):
    code = "MODEL_GATEWAY_ERROR"


class StructuredOutputError(ModelGatewayError):
    code = "STRUCTURED_OUTPUT_INVALID"


@dataclass(frozen=True)
class ModelRequest:
    messages: list[dict[str, Any]] = field(default_factory=list)
    prompt: str | None = None
    system: str | None = None
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2048
    response_format: dict[str, Any] | None = None
    output_model: type[BaseModel] | None = None
    trace: TraceContext = field(default_factory=TraceContext)
    extra: dict[str, Any] = field(default_factory=dict)
    budget: TokenCostBudget | None = None

    def payload(self) -> dict[str, Any]:
        messages = list(self.messages)
        if self.system:
            messages.insert(0, {"role": "system", "content": self.system})
        if self.prompt is not None:
            messages.append({"role": "user", "content": self.prompt})
        payload: dict[str, Any] = {"messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if self.model:
            payload["model"] = self.model
        if self.response_format:
            payload["response_format"] = self.response_format
        payload.update(self.extra)
        return payload


@dataclass(frozen=True)
class ModelResult:
    route: str
    provider: str | None
    model: str | None
    model_version: str | None
    response: dict[str, Any]
    structured_output: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    cost: float
    trace: TraceContext

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.response,
            "route": self.route,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "structured_output": self.structured_output,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "total_tokens": self.input_tokens + self.output_tokens},
            "cost": self.cost,
            "trace_id": self.trace.trace_id,
        }


class ModelGateway:
    """Provider-neutral model boundary with fallback and deterministic controls."""

    def __init__(
        self,
        primary: ModelRoute | Callable[[dict[str, Any]], dict[str, Any]],
        fallback: ModelRoute | Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        fallback_circuit_breaker: CircuitBreaker | None = None,
        budget: TokenCostBudget | None = None,
        metrics: MetricsRecorder | None = None,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.router = ModelRouter(_route("primary", primary), _route("fallback", fallback) if fallback else None)
        self.provider = self.router.primary.provider
        self.model = self.router.primary.model
        self.model_version = self.router.primary.model_version
        self.retry_policy = retry_policy or RetryPolicy()
        breaker_clock = clock or monotonic
        self.breakers = {"primary": circuit_breaker or CircuitBreaker(clock=breaker_clock), "fallback": fallback_circuit_breaker or CircuitBreaker(clock=breaker_clock)}
        self.budget = budget or TokenCostBudget()
        self.metrics = metrics or MetricsRecorder()
        self.sleeper = sleeper

    def complete(self, request: ModelRequest | None = None, **kwargs: Any) -> ModelResult:
        request = request or ModelRequest(**{key: value for key, value in kwargs.items() if key in ModelRequest.__dataclass_fields__})
        payload = request.payload()
        payload["_trace_headers"] = request.trace.headers()
        input_tokens = _estimate_tokens(payload)
        budget = request.budget or self.budget
        last: Exception | None = None
        # Accounting spans every provider response in this complete call,
        # including structured-repair responses and any fallback response.
        call_input_tokens = 0
        call_output_tokens = 0
        call_cost = 0.0
        for route in self.router.routes():
            breaker = self.breakers[route.name]
            try:
                route_payload = dict(payload)
                if route.model:
                    route_payload["model"] = route.model
                estimated_cost = input_tokens * route.cost_per_input_token + max(0, request.max_tokens) * route.cost_per_output_token
                # Reserve capacity before each route is attempted. A failed
                # route has no usage charge; fallback is checked independently.
                budget.check(input_tokens=input_tokens, output_tokens=max(0, request.max_tokens), estimated_cost=estimated_cost)
                breaker.before_call()
                structured_attempts = 2 if request.output_model is not None else 1
                last_structured: StructuredOutputError | None = None
                for structured_attempt in range(structured_attempts):
                    attempt_input_tokens = _estimate_tokens(route_payload)
                    attempt_estimated_cost = attempt_input_tokens * route.cost_per_input_token + max(0, request.max_tokens) * route.cost_per_output_token
                    # Re-check before every provider attempt, including a
                    # structured-repair retry after an invalid response.
                    budget.check(input_tokens=attempt_input_tokens, output_tokens=max(0, request.max_tokens), estimated_cost=attempt_estimated_cost)
                    with self.metrics.timer("model_latency_seconds", model=route.name):
                        response = retry_call(
                            lambda: _invoke(route.endpoint, route_payload),
                            policy=self.retry_policy,
                            sleeper=self.sleeper,
                            retry_if=_retryable,
                        )
                    if not isinstance(response, dict):
                        raise ModelGatewayError("model response must be a JSON object")
                    actual_input_tokens = _response_input_tokens(response, fallback=attempt_input_tokens)
                    output_tokens = _response_output_tokens(response)
                    cost = actual_input_tokens * route.cost_per_input_token + output_tokens * route.cost_per_output_token
                    # Charge every provider response, including schema-invalid
                    # responses, so fallback cannot hide actual spend.
                    budget.charge(input_tokens=actual_input_tokens, output_tokens=output_tokens, cost=cost)
                    call_input_tokens += actual_input_tokens
                    call_output_tokens += output_tokens
                    call_cost += cost
                    self.metrics.observe("model_tokens_input", actual_input_tokens, model=route.name)
                    self.metrics.observe("model_tokens_output", output_tokens, model=route.name)
                    self.metrics.observe("model_cost", cost, model=route.name)
                    try:
                        structured = _validate_structured(response, request.output_model)
                    except StructuredOutputError as exc:
                        last_structured = exc
                        self.metrics.increment("model_errors_total", model=route.name, error=exc.code)
                        if structured_attempt + 1 < structured_attempts:
                            route_payload = _append_structured_repair(route_payload)
                            continue
                        raise
                    breaker.success()
                    self.metrics.increment("model_requests_total", model=route.name)
                    resolved_model = route.model or response.get("model")
                    resolved_version = _resolve_model_version(route, response)
                    return ModelResult(route=route.name, provider=route.provider, model=resolved_model, model_version=resolved_version, response=response, structured_output=structured, input_tokens=call_input_tokens, output_tokens=call_output_tokens, cost=call_cost, trace=request.trace)
                if last_structured is not None:
                    raise last_structured
            except BudgetExceededError:
                raise
            except CircuitOpenError as exc:
                # An OPEN breaker rejected this route before any provider
                # attempt. Do not count that rejection as a new failure: doing
                # so would refresh opened_at and postpone half-open recovery
                # under sustained traffic. The next route may still run.
                last = exc
                self.metrics.increment("model_errors_total", model=route.name, error=type(exc).__name__)
            except Exception as exc:  # fallback is intentionally bounded to the next route
                breaker.failure()
                last = exc
                self.metrics.increment("model_errors_total", model=route.name, error=type(exc).__name__)
        if isinstance(last, StructuredOutputError):
            raise last
        raise ModelGatewayError("all model routes failed") from last

    def create_chat_completion(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        request = ModelRequest(messages=messages, system=kwargs.pop("system", None), model=kwargs.pop("model", None), temperature=kwargs.pop("temperature", 0.2), max_tokens=kwargs.pop("max_tokens", 2048), response_format=kwargs.pop("response_format", None), trace=kwargs.pop("trace", TraceContext()))
        # Tool-calling callers may pass these OpenAI-compatible fields.
        payload = request.payload()
        for name in ("tools", "tool_choice"):
            if name in kwargs and kwargs[name] is not None:
                payload[name] = kwargs[name]
        result = self._complete_payload(request, payload)
        return result.response

    def complete_payload(self, payload: dict[str, Any], *, output_model: type[BaseModel] | None = None, trace: TraceContext | None = None) -> ModelResult:
        """Execute an already-formed compatible payload through the gateway."""
        return self._complete_payload(ModelRequest(output_model=output_model, trace=trace or TraceContext()), payload)

    def _complete_payload(self, request: ModelRequest, payload: dict[str, Any]) -> ModelResult:
        return self.complete(ModelRequest(messages=payload.get("messages", []), model=payload.get("model"), temperature=payload.get("temperature", 0.2), max_tokens=payload.get("max_tokens", 2048), response_format=payload.get("response_format"), output_model=request.output_model, trace=request.trace, extra={key: value for key, value in payload.items() if key not in {"messages", "model", "temperature", "max_tokens", "response_format"}}))


def _route(name: str, endpoint: ModelRoute | Callable[[dict[str, Any]], dict[str, Any]] | None) -> ModelRoute:
    if endpoint is None:
        raise ValueError("primary model route is required")
    return endpoint if isinstance(endpoint, ModelRoute) else ModelRoute(name=name, endpoint=endpoint)


def _invoke(endpoint: Callable[[dict[str, Any]], dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke a route endpoint according to its declared calling shape.

    A caught ``TypeError`` cannot distinguish a signature mismatch from an
    error raised after the provider has already done work. Inspect first so a
    real endpoint error is never followed by a duplicate call.
    """
    try:
        parameters = tuple(inspect.signature(endpoint).parameters.values())
    except (TypeError, ValueError):
        # Extension/builtin callables may not expose a signature. The route
        # contract is payload-callable, so preserve that public shape.
        return endpoint(payload)

    positional = tuple(parameter for parameter in parameters if parameter.kind in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    })
    # The payload-object contract has one positional slot (optionally with
    # ``**kwargs``). Multiple positional slots indicate a keyword-compatible
    # endpoint such as ``endpoint(messages, model=None, **extra)``.
    if len(positional) == 1 or any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        return endpoint(payload)
    if len(positional) > 1:
        return endpoint(**payload)  # type: ignore[arg-type]
    # Keyword-only and **kwargs endpoints receive the compatible payload as
    # keyword arguments. Runtime TypeError is propagated as the endpoint's
    # real error and is not retried under another calling convention.
    return endpoint(**payload)  # type: ignore[arg-type]


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, RetryableError) or getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}


def _estimate_tokens(payload: Any) -> int:
    return max(1, len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) // 4)


def _response_output_tokens(response: dict[str, Any]) -> int:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return int(usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("completionTokens") or 0)


def _response_input_tokens(response: dict[str, Any], *, fallback: int) -> int:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return int(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("promptTokens") or fallback)


def _resolve_model_version(route: ModelRoute, response: dict[str, Any]) -> str | None:
    """Return only identity observed on the selected route or response."""
    for value in (
        route.model_version,
        response.get("model_version"),
        response.get("system_fingerprint"),
        response.get("model"),
        route.model,
    ):
        if value is not None and str(value).strip():
            return str(value)
    return None


def _validate_structured(response: dict[str, Any], output_model: type[BaseModel] | None) -> dict[str, Any] | None:
    if output_model is None:
        return None
    content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        return output_model.model_validate(parsed).model_dump(mode="json")
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise StructuredOutputError("model output failed structured-output validation") from exc


def _append_structured_repair(payload: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    messages = list(updated.get("messages") or [])
    messages.append({"role": "system", "content": "Previous output failed the required structured schema. Return one valid JSON object matching every required field and type; no prose or Markdown."})
    updated["messages"] = messages
    return updated
