"""Single model boundary used by stock_agent business orchestration.

The gateway deliberately has no provider-specific dependency.  Providers are
represented by small callables, which keeps deterministic tests and replay
free of network access while retaining compatibility with OpenAI-compatible
clients.
"""

from .budget import BudgetExceededError, TokenCostBudget
from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from .gateway import ModelGateway, ModelGatewayError, ModelRequest, ModelResult, StructuredOutputError
from .metrics import MetricsRecorder, TraceContext, global_metrics
from .retry import RetryPolicy, retry_call
from .routing import ModelRoute, ModelRouter

__all__ = [
    "BudgetExceededError", "TokenCostBudget", "CircuitBreaker", "CircuitOpenError",
    "CircuitState", "ModelGateway", "ModelGatewayError", "ModelRequest", "ModelResult",
    "StructuredOutputError", "MetricsRecorder", "TraceContext", "global_metrics", "RetryPolicy", "retry_call", "ModelRoute", "ModelRouter",
]
