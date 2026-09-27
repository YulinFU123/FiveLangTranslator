from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from time import time

SCHEMA_VERSION = "1"


def default_database_path() -> Path:
    return Path.home() / ".five_lang_translator" / "history.db"


def now_ms() -> int:
    return int(time() * 1000)


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'system_audio',
        target_language TEXT NOT NULL DEFAULT 'zh',
        translation_style TEXT NOT NULL DEFAULT 'cinema',
        provider TEXT NOT NULL DEFAULT '',
        started_at INTEGER NOT NULL,
        ended_at INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        segment_key TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'stable',
        status_rank INTEGER NOT NULL DEFAULT 2,
        start_ms INTEGER NOT NULL DEFAULT 0,
        end_ms INTEGER NOT NULL DEFAULT 0,
        source_text TEXT NOT NULL,
        translated_text TEXT NOT NULL DEFAULT '',
        source_language TEXT NOT NULL DEFAULT 'auto',
        target_language TEXT NOT NULL DEFAULT 'zh',
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        latency_ms INTEGER NOT NULL DEFAULT 0,
        cache_hit INTEGER NOT NULL DEFAULT 0,
        source_type TEXT NOT NULL DEFAULT 'asr',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE(session_id, segment_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS translation_cache (
        cache_key TEXT PRIMARY KEY,
        source_text TEXT NOT NULL,
        translated_text TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        accessed_at INTEGER NOT NULL,
        access_count INTEGER NOT NULL DEFAULT 0,
        expires_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS glossary (
        term TEXT PRIMARY KEY,
        translation TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS overlay_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subtitle_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, start_ms)",
    "CREATE INDEX IF NOT EXISTS idx_cache_accessed ON translation_cache(accessed_at)",
    "CREATE INDEX IF NOT EXISTS idx_cache_expires ON translation_cache(expires_at)",
)

# Columns added after the first alpha.2 schema. Kept additive so existing
# databases keep working without a migration script.
LEGACY_COLUMNS = (
    ("segments", "model", "VARCHAR(10)"),
    ("segments", "latency_ms", "INTEGER"),
    ("segments", "cache_hit", "INTEGER"),
    ("translation_cache", "expires_at", "INTEGER"),
)


def ensure_columns(connection: sqlite3.Connection) -> list[str]:
    added = []
    for table, column, definition in LEGACY_COLUMNS:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not any(row[1] == column for row in rows):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            added.append(column)
    if added:
        connection.commit()
    return added


class Database:
    """SQLite connection wrapper used by the history, cache and glossary stores."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_database_path()
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(str(self.path), check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA synchronous=NORMAL")
                self._connection = connection
                self._apply_schema(connection)
            return self._connection

    def _apply_schema(self, connection: sqlite3.Connection) -> None:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        ensure_columns(connection)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()

    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.connection.execute(sql, parameters)
            self.connection.commit()
            return cursor

    def executemany(self, sql: str, rows) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.connection.executemany(sql, rows)
            self.connection.commit()
            return cursor

    def query(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connection.execute(sql, parameters).fetchall())

    def query_one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(sql, parameters).fetchone()

    def schema_version(self) -> str:
        row = self.query_one("SELECT value FROM schema_meta WHERE key='schema_version'")
        return str(row["value"]) if row else ""

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
