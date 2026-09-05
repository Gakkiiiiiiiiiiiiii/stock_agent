"""Single model boundary used by stock_agent business orchestration.

The gateway deliberately has no provider-specific dependency.  Providers are
represented by small callables, which keeps deterministic tests and replay
free of network access while retaining compatibility with OpenAI-compatible
clients.
"""

from .budget import BudgetExceededError, TokenCostBudget
from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from .gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelRequest,
    ModelResult,
    StructuredOutputError,
)
from .metrics import MetricsRecorder, TraceContext, global_metrics
from .retry import RetryPolicy, retry_call
from .routing import ModelRoute, ModelRouter

__all__ = [
    "BudgetExceededError",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "MetricsRecorder",
    "ModelGateway",
    "ModelGatewayError",
    "ModelRequest",
    "ModelResult",
    "ModelRoute",
    "ModelRouter",
    "RetryPolicy",
    "StructuredOutputError",
    "TokenCostBudget",
    "TraceContext",
    "global_metrics",
    "retry_call",
]
