from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, Signal

from app.core.models import SubtitleStatus, SubtitleViewModel, new_id
from app.storage.database import Database, now_ms
from app.storage.history import HistoryRepository, canonical_source_type
from app.storage.models import SessionRecord


class HistoryService(QObject):
    """Tracks the running subtitle session and persists recognised lines."""

    session_opened = Signal(object)
    session_closed = Signal(object)
    segment_recorded = Signal(object)
    recording_changed = Signal(bool)

    def __init__(self, database: Database | None = None) -> None:
        super().__init__()
        self.database = database or Database()
        self.repository = HistoryRepository(self.database)
        self.auto_session = True
        self.record_drafts = False
        self.target_language = "zh"
        self.translation_style = "cinema"
        self.provider = ""
        self._session: SessionRecord | None = None

    @property
    def active_session(self) -> SessionRecord | None:
        return self._session

    @property
    def active_session_id(self) -> str | None:
        return self._session.id if self._session else None

    def is_recording(self) -> bool:
        return self._session is not None

    def start_session(
        self,
        name: str = "",
        source_type: str = "system_audio",
        target_language: str | None = None,
        translation_style: str | None = None,
        provider: str | None = None,
    ) -> SessionRecord:
        if self._session is not None:
            self.end_session()
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._session = self.repository.create_session(
            new_id(),
            name.strip() or f"会话 {stamp}",
            canonical_source_type(source_type),
            target_language or self.target_language,
            translation_style or self.translation_style,
            provider or self.provider,
        )
        self.session_opened.emit(self._session)
        self.recording_changed.emit(True)
        return self._session

    def end_session(self) -> SessionRecord | None:
        session = self._session
        if session is None:
            return None
        self.repository.end_session(session.id)
        session.ended_at = now_ms()
        session.segment_count = self.repository.count_segments(session.id)
        self._session = None
        self.session_closed.emit(session)
        self.recording_changed.emit(False)
        return session

    def configure(self, target_language: str, translation_style: str, provider: str = "") -> None:
        self.target_language = target_language
        self.translation_style = translation_style
        self.provider = provider

    def record(self, view: SubtitleViewModel) -> bool:
        """Persist one subtitle view model. Returns True when a row is written."""
        if not self.record_drafts and view.status == SubtitleStatus.DRAFT:
            return False
        if not view.source_text.strip():
            return False
        if self._session is None:
            if not self.auto_session:
                return False
            self.start_session(source_type=canonical_source_type(view.source))
        written = self.repository.upsert_segment(
            self._session.id,
            view.segment_id,
            view.revision,
            getattr(view.status, "name", str(view.status)).lower(),
            view.start_ms,
            view.end_ms,
            view.source_text,
            view.translated_text,
            view.source_language,
            view.target_language,
            getattr(view, "provider", ""),
            canonical_source_type(view.source),
            getattr(view, "model", ""),
            int(getattr(view, "latency_ms", 0) or 0),
            bool(getattr(view, "cache_hit", False)),
        )
        if written:
            self.segment_recorded.emit(view)
        return written

    def close(self) -> None:
        self.repository.close_open_sessions()
        self.database.close()
