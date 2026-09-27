from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import monotonic

from app.translation.http import get_json, post_json
from app.translation.models import ProviderResult
from app.translation.prompt import build_messages, clean_translation


class BaseTranslationProvider(ABC):
    """Contract shared by every translation provider.

    Implementations own their HTTP details only: context injection, glossary,
    style, line budget, caching and revision checks stay in TranslationService.
    """

    provider_id = "base"
    # Set by the registry so the UI can show which preset answered.
    preset_id: str = "base"
    display_title: str = ""

    @abstractmethod
    async def translate(self, job) -> ProviderResult:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True

    def effective_provider_id(self) -> str:
        return self.preset_id or self.provider_id


@dataclass(slots=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b"
    timeout: float = 60.0
    keep_alive: str = "15m"
    temperature: float = .2


class OllamaTranslationProvider(BaseTranslationProvider):
    provider_id = "ollama"

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(get_json, self.config.base_url.rstrip("/") + "/api/tags", 5.0)
            return True
        except Exception:
            return False

    async def translate(self, job) -> ProviderResult:
        started = monotonic()
        payload = {
            "model": self.config.model,
            "messages": build_messages(job),
            "stream": False,
            "think": False,
            "keep_alive": self.config.keep_alive,
            "options": {"temperature": self.config.temperature},
        }
        response = await asyncio.to_thread(
            post_json,
            self.config.base_url.rstrip("/") + "/api/chat",
            payload,
            self.config.timeout,
        )
        message = response.get("message") or {}
        text = clean_translation(str(message.get("content") or ""))
        if not text:
            raise RuntimeError("Ollama返回空译文")
        return ProviderResult(
            text,
            self.provider_id,
            int((monotonic() - started) * 1000),
            self.config.model,
            {
                "total_duration_ns": response.get("total_duration"),
                "load_duration_ns": response.get("load_duration"),
                "eval_count": response.get("eval_count"),
            },
        )


@dataclass(slots=True)
class OpenAICompatibleConfig:
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    api_key_environment: str = "OPENAI_API_KEY"
    timeout: float = 60.0
    temperature: float = .2


class OpenAICompatibleTranslationProvider(BaseTranslationProvider):
    provider_id = "openai_compatible"

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(get_json, self.config.base_url.rstrip("/") + "/models", 5.0)
            return True
        except Exception:
            return False

    async def translate(self, job) -> ProviderResult:
        started = monotonic()
        token = os.environ.get(self.config.api_key_environment, "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        payload = {
            "model": self.config.model,
            "messages": build_messages(job),
            "stream": False,
            "temperature": self.config.temperature,
        }
        response = await asyncio.to_thread(
            post_json,
            self.config.base_url.rstrip("/") + "/chat/completions",
            payload,
            self.config.timeout,
            headers,
        )
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI Compatible服务未返回choices")
        message = choices[0].get("message") or {}
        text = clean_translation(str(message.get("content") or ""))
        if not text:
            raise RuntimeError("OpenAI Compatible服务返回空译文")
        return ProviderResult(
            text,
            self.provider_id,
            int((monotonic() - started) * 1000),
            self.config.model,
            {"usage": response.get("usage") or {}},
        )
