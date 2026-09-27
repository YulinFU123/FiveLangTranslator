from app.core.models import RecognitionResult, SourceType, SubtitleStatus
from app.streaming.language_manager import LanguageManager
from app.streaming.stabilizer import TranscriptStabilizer


def result(revision, text, language="en", status=SubtitleStatus.DRAFT):
    return RecognitionResult("segment", revision, text, language, .9, 0, 1000, status, "test", SourceType.SYSTEM_AUDIO)


def test_english_common_prefix_becomes_stable():
    stabilizer = TranscriptStabilizer(confirmations=2)
    stabilizer.update(result(1, "I think he"))
    updated, state = stabilizer.update(result(2, "I think he knows"))
    assert updated.status == SubtitleStatus.STABLE
    assert state.committed == "I think he "
    assert state.draft == "knows"


def test_cjk_common_prefix_is_character_based():
    stabilizer = TranscriptStabilizer(confirmations=2)
    stabilizer.update(result(1, "我觉得他", "zh"))
    _, state = stabilizer.update(result(2, "我觉得他知道", "zh"))
    assert state.committed == "我觉得他"
    assert state.draft == "知道"


def test_final_clears_draft_state():
    stabilizer = TranscriptStabilizer()
    stabilizer.update(result(1, "hello"))
    updated, state = stabilizer.update(result(2, "hello world", status=SubtitleStatus.FINAL))
    assert updated.status == SubtitleStatus.FINAL
    assert state.committed == "hello world"
    assert state.draft == ""


def test_language_locks_after_three_observations():
    manager = LanguageManager("auto", lock_after=3)
    manager.observe("ja")
    manager.observe("ja")
    state = manager.observe("ja")
    assert state.locked is True
    assert state.detected == "ja"
    assert manager.inference_language == "ja"


def test_fixed_language_stays_locked():
    manager = LanguageManager("de")
    state = manager.observe("en", .95)
    assert state.locked is True
    assert state.detected == "de"
