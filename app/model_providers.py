from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AnalysisModelSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "AnalysisModelSettings":
        return cls(
            provider=os.getenv("ANALYSIS_MODEL_PROVIDER", "none"),
            model=os.getenv("ANALYSIS_MODEL_NAME"),
            base_url=os.getenv("ANALYSIS_MODEL_BASE_URL"),
            api_key=os.getenv("ANALYSIS_MODEL_API_KEY"),
        )


class AnalysisModelClient:
    def __init__(self, settings: AnalysisModelSettings | None = None, http_client: httpx.Client | None = None) -> None:
        self.settings = settings or AnalysisModelSettings.from_env()
        self.http_client = http_client or httpx.Client(timeout=60)

    def available(self) -> bool:
        return (
            self.settings.provider in {"openai_compatible", "deepseek"}
            and bool(self.settings.model)
            and bool(self.settings.base_url)
            and bool(self.settings.api_key)
        )

    def complete(self, prompt: str, system: str | None = None, temperature: float = 0.2) -> dict[str, Any]:
        if not self.available():
            return {
                "available": False,
                "provider": self.settings.provider,
                "message": "analysis model is not configured",
            }
        payload = {
            "model": self.settings.model,
            "temperature": temperature,
            "messages": [],
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})
        data = self._post_chat_completion(payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "available": True,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "content": message.get("content", ""),
            "raw": data,
        }

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
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
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

    @classmethod
    def from_env(cls) -> "AgentModelSettings":
        return cls(
            provider=os.getenv("AGENT_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")),
            model=os.getenv("AGENT_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")),
            base_url=os.getenv("AGENT_MODEL_BASE_URL", os.getenv("ANALYSIS_MODEL_BASE_URL")),
            api_key=os.getenv("AGENT_MODEL_API_KEY", os.getenv("ANALYSIS_MODEL_API_KEY")),
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
            ),
            http_client=http_client,
        )


@dataclass(frozen=True)
class VisualModelSettings:
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "VisualModelSettings":
        return cls(
            provider=os.getenv("VISUAL_MODEL_PROVIDER", os.getenv("ANALYSIS_MODEL_PROVIDER", "none")),
            model=os.getenv("VISUAL_MODEL_NAME", os.getenv("ANALYSIS_MODEL_NAME")),
            base_url=os.getenv("VISUAL_MODEL_BASE_URL", os.getenv("ANALYSIS_MODEL_BASE_URL")),
            api_key=os.getenv("VISUAL_MODEL_API_KEY", os.getenv("ANALYSIS_MODEL_API_KEY")),
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
            ),
            http_client=http_client,
        )
