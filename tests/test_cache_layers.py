import time

from app.core.models import SourceType
from app.storage.cache_store import SqliteCacheStore
from app.storage.database import Database, now_ms
from app.storage.history import HistoryRepository, canonical_source_type
from app.translation.cache import PersistentTranslationCache, TranslationCache
from app.translation.models import TranslationJob

JOB = TranslationJob("s1", 1, "I know the truth", "en", "zh", "final", [], {}, "cinema")


def make_store(tmp_path, **kwargs):
    return SqliteCacheStore(Database(tmp_path / "cache.db"), **kwargs)


def test_l1_ttl_expires_and_l2_answers(tmp_path):
    cache = PersistentTranslationCache(make_store(tmp_path), ttl_seconds=0.05)
    key = cache.key(JOB, "ollama", "qwen3:4b")
    cache.put(key, "我知道真相。", JOB.text, "ollama", "qwen3:4b")
    assert cache.lookup(key) == ("我知道真相。", "memory")

    time.sleep(0.12)
    assert cache.memory.get(key) is None, "L1 entry should be dropped once its TTL elapses"
    assert cache.lookup(key) == ("我知道真相。", "sqlite")
    assert cache.lookup(key)[1] == "memory", "an L2 hit repopulates L1"


def test_memory_cache_keeps_working_without_ttl():
    cache = TranslationCache(2)
    cache.put("a", "1")
    time.sleep(0.02)
    assert cache.get("a") == "1"


def test_write_through_reaches_both_layers(tmp_path):
    store = make_store(tmp_path)
    cache = PersistentTranslationCache(store)
    key = cache.key(JOB, "ollama", "qwen3:4b")
    cache.put(key, "持久化译文", JOB.text, "ollama", "qwen3:4b")

    assert len(cache.memory) == 1
    row = store.recent(10)[0]
    assert row["translated_text"] == "持久化译文"
    assert row["provider"] == "ollama"
    assert row["model"] == "qwen3:4b"
    assert row["source_text"] == JOB.text


def test_warm_up_prefills_l1_from_sqlite(tmp_path):
    store = make_store(tmp_path)
    writer = PersistentTranslationCache(store)
    key = writer.key(JOB, "ollama", "qwen3:4b")
    writer.put(key, "预热译文", JOB.text, "ollama", "qwen3:4b")

    reader = PersistentTranslationCache(store)
    assert len(reader.memory) == 0
    assert reader.warm_up(50) == 1
    assert reader.lookup(key) == ("预热译文", "memory")


def test_sqlite_layer_honours_expiry(tmp_path):
    store = make_store(tmp_path)
    store.put("k1", "原文", "已过期", expires_at=now_ms() - 1000)
    store.put("k2", "原文", "仍有效", expires_at=now_ms() + 60_000)

    assert store.get("k1") is None
    assert store.get("k2") == "仍有效"
    assert store.count() == 1
    assert store.purge_expired() == 0


def test_store_ttl_stamps_expiry(tmp_path):
    store = make_store(tmp_path, ttl_seconds=60)
    store.put("k1", "原文", "译文")
    assert store.recent(10)[0]["expires_at"] is not None


def test_prune_drops_expired_before_lru(tmp_path):
    store = make_store(tmp_path, maximum_entries=2)
    store.put("old", "原文", "旧译文", expires_at=now_ms() - 10)
    store.put("a", "原文", "A")
    store.put("b", "原文", "B")
    store.put("c", "原文", "C")
    assert store.count() == 2
    assert store.get("old") is None
    assert store.get("c") == "C"


def test_segments_keep_model_latency_and_cache_hit(tmp_path):
    repository = HistoryRepository(Database(tmp_path / "history.db"))
    repository.create_session("s1", "会话", started_at=1_700_000_000_000)
    repository.upsert_segment(
        "s1", "seg1", 1, "final", 0, 1200, "Trust no one.", "不要相信任何人。",
        "en", "zh", "ollama", "asr", "qwen3:4b", 187, True,
    )
    row = repository.list_segments("s1")[0]
    assert row.model == "qwen3:4b"
    assert row.latency_ms == 187
    assert row.cache_hit is True
    assert row.source_type == "asr"

    refreshed = repository.search_segments("Trust")[0]
    assert refreshed.model == "qwen3:4b"
    assert refreshed.cache_hit is True


def test_source_type_is_canonicalised_for_ocr():
    assert canonical_source_type(SourceType.SYSTEM_AUDIO) == "asr"
    assert canonical_source_type(SourceType.MICROPHONE) == "asr"
    assert canonical_source_type(SourceType.OCR) == "ocr"
