from __future__ import annotations

from app.storage.database import Database, default_database_path
from app.storage.glossary import GlossaryRepository
from app.storage.history import HistoryRepository
from app.storage.models import SegmentRecord, SessionRecord

__all__ = [
    "Database",
    "GlossaryRepository",
    "HistoryRepository",
    "HistoryService",
    "SegmentRecord",
    "SessionRecord",
    "default_database_path",
]


def __getattr__(name: str):
    """Qt dependent members stay importable without pulling PySide6 eagerly."""
    if name == "HistoryService":
        from app.storage.session import HistoryService

        return HistoryService
    raise AttributeError(name)
