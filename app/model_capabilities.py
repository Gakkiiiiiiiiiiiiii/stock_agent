from __future__ import annotations

import json
import os

from pydantic import BaseModel


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    vision: bool = False
    json_mode: bool = False
    json_schema: bool = False
    reasoning: bool = False
    streaming: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls, prefix: str = "ANALYSIS_MODEL", defaults: "ModelCapabilities | None" = None) -> "ModelCapabilities":
        raw = os.getenv(f"{prefix}_CAPABILITIES")
        if raw:
            try:
                return cls.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                pass
        base = (defaults or cls()).model_dump()
        for field, suffix in (("tool_calling", "TOOL_CALLING"), ("vision", "VISION"), ("json_mode", "JSON_MODE"), ("json_schema", "JSON_SCHEMA"), ("reasoning", "REASONING"), ("streaming", "STREAMING")):
            raw_value = os.getenv(f"{prefix}_{suffix}")
            if raw_value not in {None, ""}:
                base[field] = _env_bool(f"{prefix}_{suffix}")
        for field, suffix in (("context_window", "CONTEXT_WINDOW"), ("max_output_tokens", "MAX_OUTPUT_TOKENS")):
            value = _env_int(f"{prefix}_{suffix}")
            if value is not None:
                base[field] = value
        return cls.model_validate(base)


def _env_bool(name: str) -> bool:
    return str(os.getenv(name, "")).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return None
