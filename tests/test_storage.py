from app.storage.database import Database
from app.storage.glossary import GlossaryRepository
from app.storage.history import HistoryRepository
from app.storage.cache_store import SqliteCacheStore
from app.translation.cache import PersistentTranslationCache
from app.translation.models import TranslationJob


def make_database(tmp_path):
    return Database(tmp_path / "storage" / "history.db")


def make_repository(tmp_path):
    return HistoryRepository(make_database(tmp_path))


def add_segment(repository, session_id, key="seg1", revision=1, status="final",
                start_ms=0, end_ms=1000, source_text="He left yesterday.",
                translated_text="他昨天离开了。"):
    return repository.upsert_segment(
        session_id, key, revision, status, start_ms, end_ms,
        source_text, translated_text, "en", "zh", "ollama", "system_audio",
    )


def test_session_lifecycle_and_counts(tmp_path):
    repository = make_repository(tmp_path)
    session = repository.create_session("abc", "测试会话", "system_audio", "zh", "cinema", "ollama")
    assert session.is_open
    assert repository.get_session("abc").name == "测试会话"

    add_segment(repository, "abc")
    sessions = repository.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].segment_count == 1
    assert repository.count_segments("abc") == 1

    repository.end_session("abc")
    assert repository.get_session("abc").is_open is False
    repository.delete_session("abc")
    assert repository.get_session("abc") is None
    assert repository.count_segments("abc") == 0


def test_final_segment_is_not_downgraded(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话", started_at=1000)
    add_segment(repository, "abc", revision=2, status="final", source_text="最终文本", translated_text="最终译文")
    accepted = add_segment(repository, "abc", revision=2, status="stable", source_text="回退文本", translated_text="回退译文")
    assert accepted is False
    row = repository.list_segments("abc")[0]
    assert row.source_text == "最终文本"
    assert row.status == "final"


def test_newer_revision_wins(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话")
    add_segment(repository, "abc", revision=1, status="stable", source_text="旧文本")
    add_segment(repository, "abc", revision=2, status="final", source_text="新文本")
    rows = repository.list_segments("abc")
    assert len(rows) == 1
    assert rows[0].source_text == "新文本"


def test_translation_survives_recognition_refresh(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话")
    add_segment(repository, "abc", revision=1, status="stable", source_text="Hello", translated_text="你好")
    add_segment(repository, "abc", revision=1, status="stable", source_text="Hello there", translated_text="")
    row = repository.list_segments("abc")[0]
    assert row.source_text == "Hello there"
    assert row.translated_text == "你好"


def test_empty_text_is_rejected(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话")
    assert add_segment(repository, "abc", source_text="   ") is False
    assert repository.count_segments("abc") == 0


def test_search_matches_source_and_translation(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话")
    add_segment(repository, "abc", key="a", source_text="New York is cold", translated_text="纽约很冷")
    add_segment(repository, "abc", key="b", start_ms=2000, end_ms=3000, source_text="Nothing", translated_text="没什么")
    assert len(repository.search_segments("纽约")) == 1
    assert len(repository.search_segments("nothing", "abc")) == 1
    assert len(repository.search_segments("不存在的词")) == 0


def test_clear_removes_everything(tmp_path):
    repository = make_repository(tmp_path)
    repository.create_session("abc", "会话")
    add_segment(repository, "abc")
    repository.clear()
    assert repository.list_sessions() == []
    assert repository.count_segments() == 0


def test_persistent_cache_survives_reopen(tmp_path):
    database = make_database(tmp_path)
    store = SqliteCacheStore(database, maximum_entries=5)
    job = TranslationJob("s1", 1, "I know the truth", "en", "zh", "final", [], {}, "cinema")
    cache = PersistentTranslationCache(store)
    key = cache.key(job, "ollama", "qwen3:4b")
    cache.put(key, "我知道真相。", job.text, "ollama", "qwen3:4b")
    assert store.count() == 1

    reopened = PersistentTranslationCache(SqliteCacheStore(make_database(tmp_path)))
    value, source = reopened.lookup(key)
    assert value == "我知道真相。"
    assert source == "sqlite"
    # second lookup goes through the memory layer
    assert reopened.lookup(key)[1] == "memory"


def test_cache_store_prunes_oldest(tmp_path):
    store = SqliteCacheStore(make_database(tmp_path), maximum_entries=2)
    for index in range(4):
        store.put(f"key{index}", f"原文{index}", f"译文{index}")
    assert store.count() == 2
    assert store.get("key0") is None
    assert store.get("key3") == "译文3"
    store.clear()
    assert store.count() == 0


def test_glossary_repository_roundtrip(tmp_path):
    glossary = GlossaryRepository(make_database(tmp_path))
    assert glossary.upsert("Dark Lord", "黑暗领主") is True
    assert glossary.upsert("Dark Lord", "黑暗领主·改") is True
    assert glossary.upsert("", "空术语") is False
    assert glossary.as_dict() == {"Dark Lord": "黑暗领主·改"}
    assert glossary.count() == 1

    raw = glossary.export_json()
    glossary.clear()
    assert glossary.count() == 0
    assert glossary.import_json(raw) == 1
    assert glossary.as_dict()["Dark Lord"] == "黑暗领主·改"

    glossary.replace_all({"New York": "纽约"})
    assert glossary.as_dict() == {"New York": "纽约"}
    glossary.delete("New York")
    assert glossary.as_dict() == {}


def test_glossary_changes_cache_key(tmp_path):
    cache = PersistentTranslationCache(SqliteCacheStore(make_database(tmp_path)))
    job = TranslationJob("s1", 1, "Dark Lord is here", "en", "zh", "final", [], {"Dark Lord": "黑暗领主"}, "cinema")
    first = cache.key(job, "ollama", "m")
    job.glossary = {"Dark Lord": "魔王"}
    assert cache.key(job, "ollama", "m") != first
