from __future__ import annotations

from app.model_capabilities import ModelCapabilities
from app.model_capability_profiles import PROVIDER_PROFILES


class ModelCapabilityResolver:
    @staticmethod
    def resolve(provider: str | None, model: str | None, prefix: str) -> ModelCapabilities:
        # `model` is intentionally accepted for future documented profiles; no
        # capability is guessed solely from a marketing model name.
        _ = model
        profile = PROVIDER_PROFILES.get(str(provider or "").lower(), ModelCapabilities())
        return ModelCapabilities.from_env(prefix, defaults=profile)
