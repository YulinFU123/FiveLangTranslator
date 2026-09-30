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
    # Cap on generated tokens. 0 = provider default (unbounded). Generation
    # dominates a local round-trip, so a tighter cap is often the cheapest win.
    max_tokens: int = 0


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
        options = {"temperature": self.config.temperature}
        if self.config.max_tokens and self.config.max_tokens > 0:
            # Ollama spells max_tokens as options.num_predict.
            options["num_predict"] = int(self.config.max_tokens)
        payload = {
            "model": self.config.model,
            "messages": build_messages(job),
            "stream": False,
            "think": False,
            "keep_alive": self.config.keep_alive,
            "options": options,
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
    # Cap on generated tokens. 0 = provider default (unbounded).
    max_tokens: int = 0
    # Decrypted key from the local secret store. Takes priority over the
    # environment variable so users can configure everything inside the UI.
    api_key: str = ""
    # Extra attempts after the first failure (network blips, 429 rate limits).
    retries: int = 2


class OpenAICompatibleTranslationProvider(BaseTranslationProvider):
    provider_id = "openai_compatible"

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                get_json,
                self.config.base_url.rstrip("/") + "/models",
                10.0,
                self._headers(),
            )
            return True
        except Exception:
            return False

    def _endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict:
        """Explicit (DPAPI-decrypted) key wins over the environment variable."""
        token = (self.config.api_key or "").strip() or os.environ.get(
            self.config.api_key_environment, ""
        )
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def translate(self, job) -> ProviderResult:
        started = monotonic()
        headers = self._headers()
        payload = {
            "model": self.config.model,
            "messages": build_messages(job),
            "stream": False,
            "temperature": self.config.temperature,
        }
        if self.config.max_tokens and self.config.max_tokens > 0:
            payload["max_tokens"] = int(self.config.max_tokens)
        attempts = max(1, int(self.config.retries) + 1)
        response: dict | None = None
        for attempt in range(attempts):
            try:
                response = await asyncio.to_thread(
                    post_json, self._endpoint(), payload, self.config.timeout, headers
                )
                break
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                if attempt >= attempts - 1:
                    raise RuntimeError(
                        f"{self.display_title or self.provider_id} 调用失败"
                        f"（已重试 {self.config.retries} 次）：{exc}"
                    ) from exc
                await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
        if not response:
            raise RuntimeError(f"{self.display_title or self.provider_id} 未返回内容")
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
