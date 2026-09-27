from __future__ import annotations

from app.storage.database import Database, now_ms
from app.storage.models import SessionRecord, SegmentRecord, status_rank


def canonical_source_type(source) -> str:
    """Collapses concrete sources into the `asr` / `ocr` values required by the schema."""
    name = str(getattr(source, "value", source))
    return "ocr" if name == "ocr" else "asr"

_SESSION_COLUMNS = """
    s.id, s.name, s.source_type, s.target_language, s.translation_style, s.provider,
    s.started_at, s.ended_at,
    (SELECT COUNT(*) FROM segments g WHERE g.session_id = s.id) AS segment_count
"""


class HistoryRepository:
    """Persistence for subtitle sessions and their segments."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(
        self,
        session_id: str,
        name: str,
        source_type: str = "system_audio",
        target_language: str = "zh",
        translation_style: str = "cinema",
        provider: str = "",
        started_at: int | None = None,
    ) -> SessionRecord:
        stamp = int(started_at or now_ms())
        self.database.execute(
            """
            INSERT INTO sessions(id, name, source_type, target_language, translation_style,
                                 provider, started_at, ended_at, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,NULL,?,?)
            """,
            (session_id, name, source_type, target_language, translation_style, provider, stamp, stamp, stamp),
        )
        return SessionRecord(session_id, name, source_type, target_language, translation_style, provider, stamp, None, 0)

    def end_session(self, session_id: str, ended_at: int | None = None) -> None:
        stamp = int(ended_at or now_ms())
        self.database.execute(
            "UPDATE sessions SET ended_at=?, updated_at=? WHERE id=?",
            (stamp, stamp, session_id),
        )

    def close_open_sessions(self, ended_at: int | None = None) -> int:
        stamp = int(ended_at or now_ms())
        with self.database.lock():
            rows = self.database.query("SELECT id FROM sessions WHERE ended_at IS NULL")
            for row in rows:
                self.database.execute(
                    "UPDATE sessions SET ended_at=?, updated_at=? WHERE id=?",
                    (stamp, stamp, row["id"]),
                )
            return len(rows)

    def rename_session(self, session_id: str, name: str) -> None:
        self.database.execute(
            "UPDATE sessions SET name=?, updated_at=? WHERE id=?",
            (name, now_ms(), session_id),
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self.database.query_one(
            f"SELECT {_SESSION_COLUMNS} FROM sessions s WHERE s.id=?",
            (session_id,),
        )
        return self._session(row)

    def list_sessions(self, limit: int = 200) -> list[SessionRecord]:
        rows = self.database.query(
            f"SELECT {_SESSION_COLUMNS} FROM sessions s ORDER BY s.started_at DESC LIMIT ?",
            (limit,),
        )
        return [self._session(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with self.database.lock():
            self.database.execute("DELETE FROM segments WHERE session_id=?", (session_id,))
            self.database.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def clear(self) -> None:
        with self.database.lock():
            self.database.execute("DELETE FROM segments")
            self.database.execute("DELETE FROM sessions")

    def upsert_segment(
        self,
        session_id: str,
        segment_key: str,
        revision: int,
        status: str,
        start_ms: int,
        end_ms: int,
        source_text: str,
        translated_text: str,
        source_language: str,
        target_language: str,
        provider: str = "",
        source_type: str = "asr",
        model: str = "",
        latency_ms: int = 0,
        cache_hit: bool = False,
    ) -> bool:
        """Insert a segment, or refresh it when the incoming revision is newer."""
        if not source_text.strip():
            return False
        rank = status_rank(status)
        stamp = now_ms()
        cursor = self.database.execute(
            """
            INSERT INTO segments(session_id, segment_key, revision, status, status_rank, start_ms, end_ms,
                                 source_text, translated_text, source_language, target_language,
                                 provider, model, latency_ms, cache_hit, source_type, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id, segment_key) DO UPDATE SET
                revision = excluded.revision,
                status = excluded.status,
                status_rank = excluded.status_rank,
                start_ms = excluded.start_ms,
                end_ms = excluded.end_ms,
                source_text = excluded.source_text,
                translated_text = CASE
                    WHEN excluded.translated_text <> '' THEN excluded.translated_text
                    ELSE segments.translated_text END,
                source_language = excluded.source_language,
                target_language = excluded.target_language,
                provider = CASE WHEN excluded.provider <> '' THEN excluded.provider ELSE segments.provider END,
                model = CASE WHEN excluded.model <> '' THEN excluded.model ELSE segments.model END,
                latency_ms = excluded.latency_ms,
                cache_hit = excluded.cache_hit,
                updated_at = excluded.updated_at
            WHERE excluded.revision > segments.revision
               OR (excluded.revision = segments.revision AND excluded.status_rank >= segments.status_rank)
            """,
            (
                session_id, segment_key, int(revision), str(status), rank,
                int(start_ms), int(end_ms), source_text, translated_text,
                source_language, target_language, provider, model,
                int(latency_ms), int(bool(cache_hit)), source_type, stamp, stamp,
            ),
        )
        return cursor.rowcount > 0

    def list_segments(self, session_id: str, limit: int = 5000) -> list[SegmentRecord]:
        rows = self.database.query(
            """
            SELECT id, session_id, segment_key, revision, status, start_ms, end_ms,
                   source_text, translated_text, source_language, target_language,
                   provider, model, latency_ms, cache_hit, source_type
            FROM segments WHERE session_id=? ORDER BY start_ms ASC, id ASC LIMIT ?
            """,
            (session_id, int(limit)),
        )
        return [self._segment(row) for row in rows]

    def search_segments(self, query: str, session_id: str | None = None, limit: int = 500) -> list[SegmentRecord]:
        like = f"%{query.strip()}%"
        sql = (
            "SELECT id, session_id, segment_key, revision, status, start_ms, end_ms,"
            "       source_text, translated_text, source_language, target_language,"
            "       provider, model, latency_ms, cache_hit, source_type"
            " FROM segments WHERE (source_text LIKE ? OR translated_text LIKE ?)"
        )
        parameters: list = [like, like]
        if session_id:
            sql += " AND session_id=?"
            parameters.append(session_id)
        sql += " ORDER BY start_ms ASC LIMIT ?"
        parameters.append(int(limit))
        return [self._segment(row) for row in self.database.query(sql, tuple(parameters))]

    def count_segments(self, session_id: str | None = None) -> int:
        if session_id:
            row = self.database.query_one("SELECT COUNT(*) AS n FROM segments WHERE session_id=?", (session_id,))
        else:
            row = self.database.query_one("SELECT COUNT(*) AS n FROM segments")
        return int(row["n"]) if row else 0

    def _session(self, row) -> SessionRecord | None:
        if row is None:
            return None
        return SessionRecord(
            row["id"], row["name"], row["source_type"], row["target_language"],
            row["translation_style"], row["provider"], int(row["started_at"]),
            int(row["ended_at"]) if row["ended_at"] is not None else None,
            int(row["segment_count"]),
        )

    def _segment(self, row) -> SegmentRecord:
        return SegmentRecord(
            int(row["id"]), row["session_id"], row["segment_key"], int(row["revision"]),
            row["status"], int(row["start_ms"]), int(row["end_ms"]), row["source_text"],
            row["translated_text"], row["source_language"], row["target_language"],
            row["provider"], row["source_type"], row["model"], int(row["latency_ms"]),
            bool(int(row["cache_hit"])),
        )
