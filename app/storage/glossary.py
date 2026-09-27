from __future__ import annotations

import json

from app.storage.database import Database, now_ms
from app.storage.models import GlossaryEntry


class GlossaryRepository:
    """Term base used by the translation prompt builder."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def all(self) -> list[GlossaryEntry]:
        rows = self.database.query(
            "SELECT term, translation, note, updated_at FROM glossary ORDER BY term ASC"
        )
        return [
            GlossaryEntry(row["term"], row["translation"], row["note"], int(row["updated_at"]))
            for row in rows
        ]

    def as_dict(self) -> dict[str, str]:
        return {entry.term: entry.translation for entry in self.all()}

    def upsert(self, term: str, translation: str, note: str = "") -> bool:
        term = term.strip()
        translation = translation.strip()
        if not term or not translation:
            return False
        self.database.execute(
            """
            INSERT INTO glossary(term, translation, note, updated_at) VALUES(?,?,?,?)
            ON CONFLICT(term) DO UPDATE SET translation=excluded.translation,
                                            note=excluded.note,
                                            updated_at=excluded.updated_at
            """,
            (term, translation, note, now_ms()),
        )
        return True

    def delete(self, term: str) -> None:
        self.database.execute("DELETE FROM glossary WHERE term=?", (term.strip(),))

    def replace_all(self, entries: dict[str, str]) -> int:
        with self.database.lock():
            self.database.execute("DELETE FROM glossary")
            for term, translation in entries.items():
                self.upsert(term, translation)
        return len(entries)

    def clear(self) -> None:
        self.database.execute("DELETE FROM glossary")

    def count(self) -> int:
        row = self.database.query_one("SELECT COUNT(*) AS n FROM glossary")
        return int(row["n"]) if row else 0

    def export_rows(self) -> list[dict]:
        return [
            {"term": entry.term, "translation": entry.translation, "note": entry.note}
            for entry in self.all()
        ]

    def import_rows(self, rows) -> int:
        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            term = str(row.get("term", "")).strip()
            translation = str(row.get("translation", "")).strip()
            if self.upsert(term, translation, str(row.get("note", ""))):
                added += 1
        return added

    def import_json(self, raw: str) -> int:
        payload = json.loads(raw)
        rows = payload.get("glossary", payload) if isinstance(payload, dict) else payload
        return self.import_rows(rows if isinstance(rows, list) else [])

    def export_json(self) -> str:
        return json.dumps({"glossary": self.export_rows()}, ensure_ascii=False, indent=2)
