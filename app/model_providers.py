from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.model_capabilities import ModelCapabilities
from app.model_capability_resolver import ModelCapabilityResolver


class ModelCapabilityError(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalysisModelSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    capabilities: ModelCapabilities | None = None

    @classmethod
    def from_env(cls) -> "AnalysisModelSettings":
        model = os.getenv("ANALYSIS_MODEL_NAME")
        api_key = os.getenv("ANALYSIS_MODEL_API_KEY") or os.getenv("VISUAL_MODEL_API_KEY")
        if str(model or "").lower() in {"k3", "kimi-k3", "kimi_k3"}:
            api_key = os.getenv("VISUAL_MODEL_API_KEY") or api_key
        return cls(
            provider=os.getenv("ANALYSIS_MODEL_PROVIDER", "none"),
            model=model,
            base_url=os.getenv("ANALYSIS_MODEL_BASE_URL"),
            api_key=api_key,
            temperature=_optional_float(os.getenv("ANALYSIS_MODEL_TEMPERATURE")),
            capabilities=ModelCapabilityResolver.resolve(os.getenv("ANALYSIS_MODEL_PROVIDER", "none"), model, "ANALYSIS_MODEL"),
        )


class AnalysisModelClient:
    def __init__(self, settings: AnalysisModelSettings | None = None, http_client: httpx.Client | None = None) -> None:
        self.settings = settings or AnalysisModelSettings.from_env()
        self.capabilities = self.settings.capabilities or ModelCapabilityResolver.resolve(self.settings.provider, self.settings.model, "ANALYSIS_MODEL")
        self.http_client = http_client or httpx.Client(timeout=180)

    def available(self) -> bool:
        return (
            self.settings.provider in {"openai_compatible", "deepseek"}
            and bool(self.settings.model)
            and bool(self.settings.base_url)
            and bool(self.settings.api_key)
        )

    def supports(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
        output_model: type[BaseModel] | None = None,
    ) -> dict[str, Any]:
        if not self.available():
            return {
                "available": False,
                "provider": self.settings.provider,
                "message": "analysis model is not configured",
            }
        if output_model is not None and response_format is None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "schema": output_model.model_json_schema(),
                    "strict": True,
                },
            }
        payload = {
            "model": self.settings.model,
            "temperature": self._effective_temperature(temperature),
            "max_tokens": max_tokens,
            "messages": [],
        }
        is_json_object = (response_format or {}).get("type") == "json_object"
        native_structured = self.supports("json_mode" if is_json_object else "json_schema")
        structured_output_fallback = response_format is not None and not native_structured
        if structured_output_fallback:
            system = ((system or "") + "\nReturn strictly valid JSON only. Do not use Markdown fences or prose.").strip()
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})
        if response_format is not None and native_structured:
            payload["response_format"] = response_format
        data = self._post_chat_completion(payload)
        validated_output: BaseModel | None = None
        if structured_output_fallback:
            for attempt in range(2):
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                try:
                    parsed = json.loads(content)
                    validated_output = output_model.model_validate(parsed) if output_model is not None else None
                    break
                except (json.JSONDecodeError, ValidationError):
                    if attempt:
                        raise StructuredOutputError("STRUCTURED_OUTPUT_INVALID_JSON_OR_SCHEMA")
                    payload["messages"].append({"role": "system", "content": "Previous output was invalid JSON or did not match the requested schema. Retry with one valid JSON object matching every required field and type."})
                    data = self._post_chat_completion(payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "available": True,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "content": message.get("content", ""),
            "finish_reason": choice.get("finish_reason"),
            "raw": data,
            "structured_output_fallback": structured_output_fallback,
            "structured_output": validated_output.model_dump(mode="json") if validated_output is not None else None,
        }

    def _effective_temperature(self, requested: float) -> float:
        """Kimi K3 currently accepts only temperature=1 on its compatible API."""
        model_name = str(self.settings.model or "").lower()
        if model_name in {"k3", "kimi-k3", "kimi_k3"}:
            return 1.0
        return self.settings.temperature if self.settings.temperature is not None else requested

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError("Primary agent model is not configured")
        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        payload: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": payload_messages,
            "temperature": self._effective_temperature(temperature),
            "max_tokens": max_tokens,
        }
        if tools and not self.supports("tool_calling"):
            raise ModelCapabilityError("TOOL_CALLING_UNSUPPORTED")
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        return self._post_chat_completion(payload)

    def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = (self.settings.base_url or "").rstrip("/")
        url = f"{base_url}/chat/completions"
        response = self.http_client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True)
class AgentModelSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    capabilities: ModelCapabilities | None = None

    @classmethod
    def from_env(cls) -> "AgentModelSettings":
        return cls(
            provider=os.getenv("AGENT_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")),
            model=os.getenv("AGENT_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")),
            base_url=os.getenv("AGENT_MODEL_BASE_URL", os.getenv("ANALYSIS_MODEL_BASE_URL")),
            api_key=os.getenv("AGENT_MODEL_API_KEY", os.getenv("ANALYSIS_MODEL_API_KEY")),
            capabilities=ModelCapabilityResolver.resolve(os.getenv("AGENT_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")), os.getenv("AGENT_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")), "AGENT_MODEL"),
        )


class AgentModelClient(AnalysisModelClient):
    def __init__(self, settings: AgentModelSettings | None = None, http_client: httpx.Client | None = None) -> None:
        resolved = settings or AgentModelSettings.from_env()
        super().__init__(
            settings=AnalysisModelSettings(
                provider=resolved.provider,
                model=resolved.model,
                base_url=resolved.base_url,
                api_key=resolved.api_key,
                temperature=None,
                capabilities=resolved.capabilities
                or ModelCapabilityResolver.resolve(resolved.provider, resolved.model, "AGENT_MODEL"),
            ),
            http_client=http_client,
        )


@dataclass(frozen=True)
class VisualModelSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    capabilities: ModelCapabilities | None = None

    @classmethod
    def from_env(cls) -> "VisualModelSettings":
        return cls(
            provider=os.getenv("VISUAL_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")),
            model=os.getenv("VISUAL_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")),
            base_url=os.getenv("VISUAL_MODEL_BASE_URL", os.getenv("ANALYSIS_MODEL_BASE_URL")),
            api_key=os.getenv("VISUAL_MODEL_API_KEY", os.getenv("ANALYSIS_MODEL_API_KEY")),
            capabilities=ModelCapabilityResolver.resolve(os.getenv("VISUAL_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")), os.getenv("VISUAL_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")), "VISUAL_MODEL"),
        )


class VisualModelClient(AnalysisModelClient):
    def __init__(self, settings: VisualModelSettings | None = None, http_client: httpx.Client | None = None) -> None:
        resolved = settings or VisualModelSettings.from_env()
        super().__init__(
            settings=AnalysisModelSettings(
                provider=resolved.provider,
                model=resolved.model,
                base_url=resolved.base_url,
                api_key=resolved.api_key,
                temperature=None,
                capabilities=resolved.capabilities
                or ModelCapabilityResolver.resolve(resolved.provider, resolved.model, "VISUAL_MODEL"),
            ),
            http_client=http_client,
        )


def _optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
