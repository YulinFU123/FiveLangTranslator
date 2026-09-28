"""Qt dependent chain tests. Skipped automatically when PySide6 is unavailable."""

import asyncio

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for Qt service objects")

from app.core.models import SubtitleStatus  # noqa: E402
from app.translation.models import ProviderResult, TranslationJob  # noqa: E402
from app.translation.providers import BaseTranslationProvider  # noqa: E402
from app.translation.service import TranslationService  # noqa: E402


class FakeConfig:
    """Stand-in for provider config objects."""

    def __init__(self, model):
        self.model = model


class FakeProvider(BaseTranslationProvider):
    def __init__(self, name, model, result=None, error=None):
        self.preset_id = name
        self.display_title = name
        self.provider_id = name
        self.config = FakeConfig(model)
        self.result = result
        self.error = error
        self.calls = 0

    async def translate(self, job):
        self.calls += 1
        if self.error is not None:
            raise RuntimeError(self.error)
        return ProviderResult(self.result or "译文", self.preset_id, 42, self.config.model)

    async def health_check(self):
        return True


def make_service(providers):
    service = TranslationService()
    service.set_providers(providers)
    return service


def make_job():
    return TranslationJob("s1", 1, "I know the truth", "en", "zh", SubtitleStatus.FINAL, [], {}, "cinema", 2)


def run(service, job, is_final=True):
    """Runs _run() collecting translations, metrics and errors."""
    translations, metrics, errors = [], [], []
    service.translated.connect(translations.append)
    service.metrics_changed.connect(metrics.append)
    service.error.connect(errors.append)
    asyncio.run(service._run(job, {}, is_final))
    return translations, metrics, errors


def test_chain_answers_in_order_without_fallback():
    service = make_service([FakeProvider("deepseek", "deepseek-chat", result="我知道真相。")])
    translations, metrics, errors = run(service, make_job())
    assert translations[0].translated_text == "我知道真相。"
    assert translations[0].provider == "deepseek"
    assert metrics[0]["fallback"] is False
    assert metrics[0]["chain_position"] == 0
    assert not errors


def test_chain_falls_forward_until_one_succeeds():
    first = FakeProvider("deepseek", "deepseek-chat", error="HTTP 401")
    second = FakeProvider("doubao", "ep-20250102123456-abcde", error="HTTP 429")
    third = FakeProvider("ollama", "qwen3:4b", result="备用译文")
    service = make_service([first, second, third])

    translations, metrics, errors = run(service, make_job())

    assert (first.calls, second.calls, third.calls) == (1, 1, 1)
    assert translations[0].translated_text == "备用译文"
    assert translations[0].provider == "ollama"
    assert metrics[0]["fallback"] is True
    assert metrics[0]["chain_position"] == 2
    assert not errors


def test_every_provider_failing_reports_the_whole_chain():
    first = FakeProvider("deepseek", "deepseek-chat", error="HTTP 401")
    second = FakeProvider("ollama", "qwen3:4b", error="连接失败")
    service = make_service([first, second])

    translations, _metrics, errors = run(service, make_job())

    assert not translations
    assert len(errors) == 1
    assert "deepseek" in errors[0]
    assert "密钥认证失败" in errors[0]
    assert "本地服务未启动或无法连接" in errors[0]


def test_cache_hit_skips_the_whole_chain():
    primary = FakeProvider("deepseek", "deepseek-chat")
    secondary = FakeProvider("ollama", "qwen3:4b")
    service = make_service([primary, secondary])
    job = make_job()
    service.cache.put(service.cache.key(job, primary.provider_id, primary.config.model), "缓存译文")

    translations, metrics, _errors = run(service, job)

    assert primary.calls == 0 and secondary.calls == 0
    assert translations[0].translated_text == "缓存译文"
    assert translations[0].cache_hit is True
    assert metrics[0]["cached"] is True
    assert metrics[0]["latency_ms"] == 0


def test_successful_result_is_written_back_to_the_cache():
    provider = FakeProvider("deepseek", "deepseek-chat", result="我知道真相。")
    service = make_service([provider])
    job = make_job()

    run(service, job)

    key = service.cache.key(job, provider.provider_id, provider.config.model)
    assert service.cache.get(key) == "我知道真相。"


def test_set_providers_reports_the_ordered_chain():
    service = TranslationService()
    statuses = []
    service.status_changed.connect(statuses.append)
    service.set_providers([
        FakeProvider("deepseek", "deepseek-chat"),
        FakeProvider("ollama", "qwen3:4b"),
    ])
    assert [item.preset_id for item in service.chain] == ["deepseek", "ollama"]
    assert service.primary_id == "deepseek"
    assert "deepseek → ollama" in statuses[-1] or "DeepSeek" in statuses[-1]


def test_empty_chain_disables_the_service():
    service = make_service([FakeProvider("deepseek", "deepseek-chat")])
    assert service.enabled is True
    service.set_providers([])
    assert service.enabled is False
    assert service.chain == []
