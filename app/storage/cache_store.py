from __future__ import annotations

from app.storage.database import Database, now_ms


class SqliteCacheStore:
    """Durable translation cache (L2). Keeps subtitles available after restart."""

    def __init__(self, database: Database, maximum_entries: int = 20000, ttl_seconds: float = 0.0) -> None:
        self.database = database
        self.maximum_entries = maximum_entries
        self.ttl_seconds = ttl_seconds

    def get(self, cache_key: str) -> str | None:
        stamp = now_ms()
        row = self.database.query_one(
            """
            SELECT translated_text FROM translation_cache
            WHERE cache_key=? AND (expires_at IS NULL OR expires_at > ?)
            """,
            (cache_key, stamp),
        )
        if row is None:
            expired = self.database.query_one(
                "SELECT translated_text FROM translation_cache WHERE cache_key=?", (cache_key,)
            )
            if expired is not None:
                self._delete(cache_key)
            return None
        self.database.execute(
            "UPDATE translation_cache SET accessed_at=?, access_count=access_count+1 WHERE cache_key=?",
            (stamp, cache_key),
        )
        return str(row["translated_text"])

    def put(
        self,
        cache_key: str,
        source_text: str,
        translated_text: str,
        provider: str = "",
        model: str = "",
        expires_at: int | None = None,
    ) -> None:
        if not translated_text:
            return
        now = now_ms()
        if expires_at is None and self.ttl_seconds and self.ttl_seconds > 0:
            expires_at = now + int(self.ttl_seconds * 1000)
        self.database.execute(
            """
            INSERT INTO translation_cache(cache_key, source_text, translated_text, provider, model,
                                          created_at, accessed_at, access_count, expires_at)
            VALUES(?,?,?,?,?,?,?,0,?)
            ON CONFLICT(cache_key) DO UPDATE SET translated_text=excluded.translated_text,
                                                 provider=excluded.provider,
                                                 model=excluded.model,
                                                 accessed_at=excluded.accessed_at,
                                                 expires_at=excluded.expires_at
            """,
            (cache_key, source_text, translated_text, provider, model, now, now, expires_at),
        )
        self.prune()

    def prune(self) -> int:
        """Drops expired rows first, then the least recently used overflow."""
        removed = self.purge_expired()
        with self.database.lock():
            row = self.database.query_one("SELECT COUNT(*) AS n FROM translation_cache")
            total = int(row["n"]) if row else 0
            overflow = total - self.maximum_entries
            if overflow <= 0:
                return removed
            rows = self.database.query(
                "SELECT cache_key FROM translation_cache ORDER BY accessed_at ASC LIMIT ?",
                (overflow,),
            )
            for row in rows:
                self._delete(row["cache_key"])
            return removed + len(rows)

    def purge_expired(self) -> int:
        stamp = now_ms()
        with self.database.lock():
            cursor = self.database.execute(
                "DELETE FROM translation_cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (stamp,),
            )
            return int(cursor.rowcount or 0)

    def _delete(self, cache_key: str) -> None:
        self.database.execute("DELETE FROM translation_cache WHERE cache_key=?", (cache_key,))

    def count(self) -> int:
        self.purge_expired()
        row = self.database.query_one("SELECT COUNT(*) AS n FROM translation_cache")
        return int(row["n"]) if row else 0

    def clear(self) -> None:
        self.database.execute("DELETE FROM translation_cache")

    def recent(self, limit: int = 200) -> list[dict]:
        rows = self.database.query(
            """
            SELECT cache_key, source_text, translated_text, provider, model,
                   access_count, accessed_at, expires_at
            FROM translation_cache ORDER BY accessed_at DESC, cache_key ASC LIMIT ?
            """,
            (int(limit),),
        )
        return [dict(row) for row in rows]
