from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import json
from time import monotonic

# Cache keys cover every setting that can change a translation.
# Note: the visible line budget (max_lines) is deliberately NOT part of the key.
# It only feeds a soft "keep it within N lines" instruction, so sharing one entry
# across box sizes avoids re-translating the same sentence after every resize
# (a visible slowdown whenever embeds the perspective changes).
CACHE_KEY_FIELDS = ("text", "source", "target", "style", "glossary", "provider", "model")

# L1 keeps a short TTL (seconds scale) so a stale in process copy never outlives
# the durable L2 entry for long. L2 is consulted whenever L1 misses or expires.
L1_DEFAULT_TTL_SECONDS = 300.0


def build_key(job, provider_id: str, model: str) -> str:
    payload = {
        "text": " ".join(job.text.split()),
        "source": job.source_language,
        "target": job.target_language,
        "style": job.style,
        "glossary": job.glossary,
        "provider": provider_id,
        "model": model,
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class TranslationCache:
    """In-memory LRU cache (L1). Original implementation plus an optional TTL."""

    def __init__(self, maximum_entries: int = 1000, ttl_seconds: float = 0.0) -> None:
        self.maximum_entries = maximum_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[str, float | None]] = OrderedDict()

    def key(self, job, provider_id: str, model: str) -> str:
        return build_key(job, provider_id, model)

    def get(self, key: str) -> str | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and monotonic() >= expires_at:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def lookup(self, key: str) -> tuple[str | None, str | None]:
        value = self.get(key)
        return value, ("memory" if value is not None else None)

    def put(self, key: str, value: str, source_text: str = "", provider: str = "", model: str = "") -> None:
        expires_at = monotonic() + self.ttl_seconds if self.ttl_seconds and self.ttl_seconds > 0 else None
        self._items[key] = (value, expires_at)
        self._items.move_to_end(key)
        while len(self._items) > self.maximum_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class PersistentTranslationCache:
    """Two level cache: L1 LRU in front of an L2 SQLite store.

    Read path:  L1 memory (TTL guarded) -> L2 SQLite -> provider
    Write path: provider result -> L1 and L2
    """

    def __init__(
        self,
        store=None,
        memory_entries: int = 500,
        maximum_entries: int = 20000,
        ttl_seconds: float = L1_DEFAULT_TTL_SECONDS,
        warm_up_entries: int = 0,
    ) -> None:
        self.store = store
        if store is not None:
            store.maximum_entries = maximum_entries
        self.maximum_entries = maximum_entries
        self.ttl_seconds = ttl_seconds
        self.memory = TranslationCache(memory_entries, ttl_seconds)
        if store is not None and warm_up_entries:
            self.warm_up(warm_up_entries)

    def key(self, job, provider_id: str, model: str) -> str:
        return build_key(job, provider_id, model)

    def get(self, key: str) -> str | None:
        value, _ = self.lookup(key)
        return value

    def lookup(self, key: str) -> tuple[str | None, str | None]:
        value = self.memory.get(key)
        if value is not None:
            return value, "memory"
        if self.store is None:
            return None, None
        value = self.store.get(key)
        if value is not None:
            self.memory.put(key, value)
            return value, "sqlite"
        return None, None

    def put(self, key: str, value: str, source_text: str = "", provider: str = "", model: str = "") -> None:
        self.memory.put(key, value)
        if self.store is not None:
            self.store.put(key, source_text, value, provider, model)

    def warm_up(self, limit: int = 500) -> int:
        """Prefill L1 with the most recently used L2 rows (P3 startup requirement)."""
        if self.store is None:
            return 0
        loaded = 0
        for row in self.store.recent(limit):
            self.memory.put(str(row["cache_key"]), str(row["translated_text"]))
            loaded += 1
        return loaded

    def clear(self) -> None:
        self.memory.clear()
        if self.store is not None:
            self.store.clear()

    def count_entries(self) -> int:
        return self.store.count() if self.store is not None else len(self.memory)

    def __len__(self) -> int:
        return self.count_entries()
