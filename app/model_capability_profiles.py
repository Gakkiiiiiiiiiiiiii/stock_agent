from __future__ import annotations

from app.model_capabilities import ModelCapabilities


# Profiles describe documented OpenAI-compatible API contracts, while explicit
# deployment settings remain the source of truth for provider-specific changes.
PROVIDER_PROFILES = {
    "openai_compatible": ModelCapabilities(tool_calling=True, json_mode=True),
    "deepseek": ModelCapabilities(tool_calling=True, json_mode=True),
}
