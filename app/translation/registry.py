from __future__ import annotations

import re
from dataclasses import dataclass

from app.translation.providers import (
    OllamaConfig,
    OllamaTranslationProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleTranslationProvider,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# Volcano Ark identifies the model through an inference endpoint id: ep-20250102123456-abcde
ENDPOINT_ID_PATTERN = re.compile(r"^ep-[0-9A-Za-z_-]{6,}$")


@dataclass(slots=True)
class ProviderPreset:
    """One entry in the translation provider registry.

    OpenAI Compatible vendors do not get their own class: they are presets that
    instantiate the existing driver with a different endpoint.
    """

    provider_id: str
    driver: str  # ollama | openai_compatible
    title: str
    base_url: str
    model: str
    model_label: str = "模型"
    model_placeholder: str = ""
    requires_api_key: bool = False
    api_key_environment: str = ""
    requires_endpoint_id: bool = False
    note: str = ""

    def effective_base_url(self, base_url: str = "") -> str:
        return (base_url or self.base_url).strip().rstrip("/") or self.base_url

    def effective_model(self, model: str = "") -> str:
        return (model or self.model).strip() or self.model

    def validate(self, base_url: str = "", model: str = "") -> str:
        """Returns an empty string when the entry is usable, otherwise the reason."""
        if not (base_url or self.base_url).strip():
            return f"{self.title} 缺少服务地址"
        if self.requires_endpoint_id:
            endpoint = (model or "").strip()
            if not endpoint:
                return "请填写火山方舟 endpoint id（ep-xxxxxxxx）"
            if not ENDPOINT_ID_PATTERN.match(endpoint):
                return f"endpoint id 格式不正确：{endpoint}，应形如 ep-20250102123456-abcde"
            return ""
        if not (model or self.model).strip():
            return f"{self.title} 缺少模型名称"
        return ""


PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset(
        "ollama", "ollama", "Ollama 本地翻译",
        "http://127.0.0.1:11434", "qwen3:4b",
        model_label="模型", model_placeholder="qwen3:4b",
        note="本地推理，不需要密钥",
    ),
    "openai_compatible": ProviderPreset(
        "openai_compatible", "openai_compatible", "OpenAI Compatible",
        "http://127.0.0.1:1234/v1", "local-model",
        model_label="模型", model_placeholder="local-model",
        requires_api_key=True, api_key_environment="OPENAI_API_KEY",
        note="LM Studio / vLLM / 自建网关等",
    ),
    "deepseek": ProviderPreset(
        "deepseek", "openai_compatible", "DeepSeek",
        DEEPSEEK_BASE_URL, "deepseek-chat",
        model_label="模型", model_placeholder="deepseek-chat",
        requires_api_key=True, api_key_environment="DEEPSEEK_API_KEY",
        note="复用 OpenAI Compatible 适配层，密钥来自 DEEPSEEK_API_KEY",
    ),
    "doubao": ProviderPreset(
        "doubao", "openai_compatible", "豆包（火山方舟 Ark）",
        DOUBAO_BASE_URL, "",
        model_label="Endpoint ID", model_placeholder="ep-20250102123456-abcde",
        requires_api_key=True, api_key_environment="ARK_API_KEY",
        requires_endpoint_id=True,
        note="复用 OpenAI Compatible 适配层，模型位填方舟推理接入点 ID",
    ),
}

DEFAULT_CHAIN = ("ollama", "openai_compatible")

# Older per provider settings fields keep working by mapping into endpoints.
LEGACY_ENDPOINT_FIELDS = {
    "ollama": ("ollama_url", "ollama_model"),
    "openai_compatible": ("openai_compatible_url", "openai_compatible_model"),
}


class TranslationProviderRegistry:
    """Creates translation providers from presets and keeps their order."""

    def __init__(self, presets: dict[str, ProviderPreset] | None = None) -> None:
        self.presets = dict(presets or PRESETS)

    def identifiers(self) -> list[str]:
        return list(self.presets)

    def get(self, provider_id: str) -> ProviderPreset:
        try:
            return self.presets[provider_id]
        except KeyError as exc:
            raise KeyError(f"未知的翻译Provider：{provider_id}") from exc

    def create(self, provider_id: str, base_url: str = "", model: str = "", options: dict | None = None):
        preset = self.get(provider_id)
        settings = options or {}
        base = preset.effective_base_url(base_url)
        name = (model or "").strip() if preset.requires_endpoint_id else preset.effective_model(model)
        if preset.driver == "ollama":
            provider = OllamaTranslationProvider(OllamaConfig(
                base, name or preset.model,
                timeout=float(settings.get("timeout", 60.0)),
                keep_alive=str(settings.get("keep_alive", "15m")),
                temperature=float(settings.get("temperature", 0.2)),
            ))
        elif preset.driver == "openai_compatible":
            provider = OpenAICompatibleTranslationProvider(OpenAICompatibleConfig(
                base, name or preset.model,
                api_key_environment=str(settings.get("api_key_environment", preset.api_key_environment)),
                timeout=float(settings.get("timeout", 60.0)),
                temperature=float(settings.get("temperature", 0.2)),
            ))
        else:  # pragma: no cover - guards against a typo in future presets
            raise ValueError(f"未支持的 Provider 驱动：{preset.driver}")
        provider.preset_id = preset.provider_id
        provider.display_title = preset.title
        return provider

    def validate(self, provider_id: str, base_url: str = "", model: str = "") -> str:
        try:
            preset = self.get(provider_id)
        except KeyError as exc:
            return str(exc)
        return preset.validate(base_url, model)

    def build_chain(self, entries) -> list:
        """Instantiates an ordered chain. entries[0] is the primary provider."""
        providers = []
        for entry in entries:
            data = dict(entry)
            provider_id = str(data.pop("provider_id", "") or data.pop("id", ""))
            if not provider_id:
                continue
            providers.append(self.create(
                provider_id,
                str(data.pop("base_url", "") or ""),
                str(data.pop("model", "") or ""),
                data.pop("options", None) or data,
            ))
        return providers

    def validate_plan(self, entries) -> list[str]:
        problems = []
        for entry in entries:
            data = dict(entry)
            provider_id = str(data.get("provider_id", "") or data.get("id", ""))
            message = self.validate(
                provider_id,
                str(data.get("base_url", "") or ""),
                str(data.get("model", "") or ""),
            )
            if message:
                problems.append(message)
        return problems


def plan_from_settings(settings) -> list[dict]:
    """Builds the ordered provider plan from persisted settings.

    `translation_chain` drives the fallback order. Each provider takes its
    endpoint from `translation_endpoints`, falling back to the legacy per
    provider fields so existing settings.json files keep working.
    """
    chain = list(getattr(settings, "translation_chain", None) or [])
    if not chain:
        chain = [
            getattr(settings, "translation_provider", "ollama"),
            getattr(settings, "translation_fallback_provider", "openai_compatible"),
        ]
    endpoints = dict(getattr(settings, "translation_endpoints", None) or {})
    plan = []
    for provider_id in chain:
        override = endpoints.get(provider_id) or {}
        base_url = str(override.get("base_url", "") or "")
        model = str(override.get("model", "") or "")
        legacy = LEGACY_ENDPOINT_FIELDS.get(provider_id)
        if legacy:
            base_url = base_url or str(getattr(settings, legacy[0], "") or "")
            model = model or str(getattr(settings, legacy[1], "") or "")
        plan.append({"provider_id": provider_id, "base_url": base_url, "model": model})
    return plan
