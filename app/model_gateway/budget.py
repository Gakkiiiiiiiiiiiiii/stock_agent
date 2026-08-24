from __future__ import annotations

from dataclasses import dataclass


class BudgetExceededError(RuntimeError):
    code = "MODEL_BUDGET_EXCEEDED"


@dataclass
class TokenCostBudget:
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None
    max_cost: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def check(self, *, input_tokens: int, output_tokens: int, estimated_cost: float = 0.0) -> None:
        """Validate a reservation without mutating accounting state."""
        if self.max_input_tokens is not None and self.input_tokens + input_tokens > self.max_input_tokens:
            raise BudgetExceededError("input token budget exceeded")
        if self.max_output_tokens is not None and self.output_tokens + output_tokens > self.max_output_tokens:
            raise BudgetExceededError("output token budget exceeded")
        if self.max_total_tokens is not None and self.total_tokens + input_tokens + output_tokens > self.max_total_tokens:
            raise BudgetExceededError("total token budget exceeded")
        if self.max_cost is not None and self.cost + estimated_cost > self.max_cost:
            raise BudgetExceededError("model cost budget exceeded")

    def reserve(self, *, input_tokens: int, output_tokens: int, estimated_cost: float = 0.0) -> None:
        self.check(input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.cost += max(0.0, float(estimated_cost))

    def charge(self, *, input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0) -> None:
        self.reserve(input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=cost)
