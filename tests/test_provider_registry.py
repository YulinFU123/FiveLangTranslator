from app.settings import Settings
from app.translation.providers import OllamaTranslationProvider, OpenAICompatibleTranslationProvider
from app.translation.registry import (
    DEFAULT_CHAIN,
    DEEPSEEK_BASE_URL,
    DOUBAO_BASE_URL,
    TranslationProviderRegistry,
    plan_from_settings,
)


def test_registry_exposes_four_presets():
    registry = TranslationProviderRegistry()
    assert set(registry.identifiers()) == {"ollama", "openai_compatible", "deepseek", "doubao"}
    assert DEFAULT_CHAIN == ("ollama", "openai_compatible")


def test_unknown_provider_id_raises():
    registry = TranslationProviderRegistry()
    try:
        registry.get("nope")
    except KeyError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown provider should raise")


def test_deepseek_reuses_openai_compatible_driver():
    provider = TranslationProviderRegistry().create("deepseek")
    assert isinstance(provider, OpenAICompatibleTranslationProvider)
    assert provider.config.base_url == DEEPSEEK_BASE_URL
    assert provider.config.model == "deepseek-flash"
    assert provider.config.api_key_environment == "DEEPSEEK_API_KEY"
    assert provider.preset_id == "deepseek"
    assert provider.display_title == "DeepSeek"


def test_doubao_reuses_openai_compatible_driver_with_endpoint_id():
    provider = TranslationProviderRegistry().create("doubao", model="ep-20250102123456-abcde")
    assert isinstance(provider, OpenAICompatibleTranslationProvider)
    assert provider.config.base_url == DOUBAO_BASE_URL
    assert provider.config.model == "ep-20250102123456-abcde"
    assert provider.config.api_key_environment == "ARK_API_KEY"


def test_ollama_still_uses_its_own_driver():
    provider = TranslationProviderRegistry().create("ollama", model="qwen3:1.7b")
    assert isinstance(provider, OllamaTranslationProvider)
    assert provider.config.model == "qwen3:1.7b"
    assert provider.preset_id == "ollama"


def test_doubao_requires_endpoint_id():
    registry = TranslationProviderRegistry()
    assert "endpoint id" in registry.validate("doubao", DOUBAO_BASE_URL, "")
    assert "格式不正确" in registry.validate("doubao", DOUBAO_BASE_URL, "gpt-4o")
    assert registry.validate("doubao", DOUBAO_BASE_URL, "ep-20250102123456-abcde") == ""


def test_presets_fall_back_to_their_default_model():
    registry = TranslationProviderRegistry()
    # An empty model field means "use the preset default".
    assert registry.validate("deepseek", DEEPSEEK_BASE_URL, "") == ""
    assert registry.validate("deepseek", DEEPSEEK_BASE_URL, "deepseek-chat") == ""
    assert registry.create("deepseek", model="").config.model == "deepseek-flash"
    # A blank address is always a configuration problem.
    assert "缺少服务地址" in registry.validate("ollama", "   ", "")


def test_build_chain_keeps_configured_order():
    registry = TranslationProviderRegistry()
    chain = registry.build_chain([
        {"provider_id": "deepseek", "model": "deepseek-chat"},
        {"provider_id": "doubao", "model": "ep-20250102123456-abcde"},
        {"provider_id": "ollama", "model": "qwen3:1.7b"},
    ])
    assert [item.preset_id for item in chain] == ["deepseek", "doubao", "ollama"]
    assert isinstance(chain[0], OpenAICompatibleTranslationProvider)
    assert isinstance(chain[2], OllamaTranslationProvider)


def test_validate_plan_collects_every_problem():
    registry = TranslationProviderRegistry()
    problems = registry.validate_plan([
        {"provider_id": "deepseek", "base_url": "   "},
        {"provider_id": "doubao", "model": ""},
    ])
    assert len(problems) == 2
    assert all(problems)


def test_plan_from_settings_uses_chain_then_endpoints():
    settings = Settings()
    settings.translation_chain = ["deepseek", "ollama"]
    settings.translation_endpoints = {"deepseek": {"model": "deepseek-chat"}}
    plan = plan_from_settings(settings)
    assert [item["provider_id"] for item in plan] == ["deepseek", "ollama"]
    assert plan[0]["model"] == "deepseek-chat"


def test_plan_from_settings_falls_back_to_legacy_fields():
    settings = Settings()
    settings.translation_chain = []
    settings.translation_provider = "ollama"
    settings.translation_fallback_provider = "openai_compatible"
    settings.ollama_model = "qwen3:4b"
    settings.openai_compatible_model = "local-model"
    plan = plan_from_settings(settings)
    assert [item["provider_id"] for item in plan] == ["ollama", "openai_compatible"]
    assert plan[0]["model"] == "qwen3:4b"
    assert plan[1]["model"] == "local-model"
