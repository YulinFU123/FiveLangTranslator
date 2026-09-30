from app.core.models import SubtitleStatus
from app.translation.cache import TranslationCache
from app.translation.context import DEFAULT_CONTEXT_SENTENCES, TranslationContext
from app.translation.models import TranslationJob
from app.translation.prompt import build_messages, clamp_line_budget, clean_translation


def make_job(text="I know the truth", max_lines=2):
    return TranslationJob(
        "s1", 1, text, "en", "zh", SubtitleStatus.FINAL,
        ["Source: He left.\nTranslation: 他离开了。"],
        {"New York": "纽约"}, "cinema", max_lines,
    )


def test_prompt_contains_context_and_glossary():
    messages = build_messages(make_job())
    assert messages[0]["role"] == "system"
    assert "movie-subtitle" in messages[0]["content"]
    assert "New York => 纽约" in messages[1]["content"]
    assert "He left" in messages[1]["content"]


def test_clean_translation_removes_label():
    assert clean_translation("Translation: 我知道真相。") == "我知道真相。"
    assert clean_translation('"我知道真相。"') == "我知道真相。"


def test_cache_key_changes_with_model():
    cache = TranslationCache()
    job = make_job()
    assert cache.key(job, "ollama", "a") != cache.key(job, "ollama", "b")


def test_cache_key_is_stable_across_line_budget():
    """Resizing the box must not invalidate stored translations.

    The line budget is only a soft wording hint, so keeping max_lines out of the
    key lets an already translated sentence survive a box resize instead of
    forcing a fresh (and visibly slow) provider round-trip.
    """
    cache = TranslationCache()
    base = cache.key(make_job(max_lines=2), "ollama", "m")
    assert base == cache.key(make_job(max_lines=4), "ollama", "m")
    assert base == cache.key(make_job(max_lines=5), "ollama", "m")
    # Genuinely different text must still get its own entry.
    assert base != cache.key(make_job(text="Something else"), "ollama", "m")


def test_context_window_defaults_to_two_and_is_configurable():
    assert DEFAULT_CONTEXT_SENTENCES == 2
    assert TranslationContext().sentences.maxlen == 2
    assert TranslationContext(5).sentences.maxlen == 5


def test_prompt_context_is_trimmed_to_window():
    job = make_job()
    job.context = [f"Source: s{index}" for index in range(6)]
    user_content = build_messages(job)[1]["content"]
    # Only the newest pairs may reach the prompt (s4/s5 -> trimmed to 2).
    assert "s5" in user_content and "s4" in user_content
    assert "s3" not in user_content


def test_registry_threads_max_tokens_into_provider_configs():
    from app.translation.registry import TranslationProviderRegistry

    registry = TranslationProviderRegistry()
    assert registry.create("ollama", options={"max_tokens": 64}).config.max_tokens == 64
    assert registry.create("openai_compatible", options={"max_tokens": 96}).config.max_tokens == 96
    # Default stays unbounded so behaviour is unchanged when unset.
    assert registry.create("ollama", options={}).config.max_tokens == 0


def test_prompt_injects_line_budget():
    messages = build_messages(make_job(max_lines=4))
    system = messages[0]["content"]
    assert "at most 4 lines" in system
    assert "within 4 lines" in system


def test_prompt_omits_line_budget_for_default_and_missing_values():
    assert "at most" not in build_messages(make_job(max_lines=2))[0]["content"]
    assert "at most" not in build_messages(make_job(max_lines=0))[0]["content"]


def test_cinema_style_has_no_hardcoded_two_line_rule():
    system = build_messages(make_job())[0]["content"]
    assert "movie-subtitle" in system
    assert "two short lines" not in system
    assert "two lines" not in system.lower()


def test_line_budget_is_clamped():
    assert clamp_line_budget(0) == 1
    assert clamp_line_budget(-3) == 1
    assert clamp_line_budget(99) == 8
    assert clamp_line_budget(3) == 3
    assert clamp_line_budget(None) == 2


def test_cache_evicts_oldest():
    cache = TranslationCache(2)
    cache.put("a", "1"); cache.put("b", "2"); cache.put("c", "3")
    assert cache.get("a") is None
    assert cache.get("c") == "3"


def test_context_keeps_latest_sentences():
    context = TranslationContext(2)
    context.commit("one", "一")
    context.commit("two", "二")
    context.commit("three", "三")
    snapshot = context.snapshot()
    assert len(snapshot) == 2
    assert "three" in snapshot[-1]
