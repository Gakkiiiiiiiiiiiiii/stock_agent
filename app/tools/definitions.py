from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolDefinition(BaseModel):
    """A domain tool whose JSON schema is generated from its input model."""

    name: str
    description: str
    input_model: type[BaseModel]
    executor: Callable[[dict[str, Any]], dict[str, Any]]
    category: str
    timeout_seconds: int = 30
    output_limit_bytes: int = 65536

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def anthropic_schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_model.model_json_schema()}
