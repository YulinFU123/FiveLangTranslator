from app.core.models import SubtitleStatus
from app.translation.cache import TranslationCache
from app.translation.context import TranslationContext
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


def test_cache_key_changes_with_line_budget():
    cache = TranslationCache()
    assert cache.key(make_job(max_lines=2), "ollama", "m") != cache.key(make_job(max_lines=4), "ollama", "m")
    assert cache.key(make_job(max_lines=4), "ollama", "m") != cache.key(make_job(max_lines=5), "ollama", "m")


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
