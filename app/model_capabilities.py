from __future__ import annotations

import json
import os

from pydantic import BaseModel


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    vision: bool = False
    json_schema: bool = False
    reasoning: bool = False
    streaming: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls, prefix: str = "ANALYSIS_MODEL") -> "ModelCapabilities":
        raw = os.getenv(f"{prefix}_CAPABILITIES")
        if raw:
            try:
                return cls.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                pass
        return cls(
            tool_calling=_env_bool(f"{prefix}_TOOL_CALLING"),
            vision=_env_bool(f"{prefix}_VISION"),
            json_schema=_env_bool(f"{prefix}_JSON_SCHEMA"),
            reasoning=_env_bool(f"{prefix}_REASONING"),
            streaming=_env_bool(f"{prefix}_STREAMING"),
            context_window=_env_int(f"{prefix}_CONTEXT_WINDOW"),
            max_output_tokens=_env_int(f"{prefix}_MAX_OUTPUT_TOKENS"),
        )


def _env_bool(name: str) -> bool:
    return str(os.getenv(name, "")).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return None
