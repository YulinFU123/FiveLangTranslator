from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SessionRecord:
    id: str
    name: str
    source_type: str
    target_language: str
    translation_style: str
    provider: str
    started_at: int
    ended_at: int | None
    segment_count: int = 0

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


@dataclass(slots=True)
class SegmentRecord:
    id: int
    session_id: str
    segment_key: str
    revision: int
    status: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str
    source_language: str
    target_language: str
    provider: str
    source_type: str
    model: str = ""
    latency_ms: int = 0
    cache_hit: bool = False


@dataclass(slots=True)
class GlossaryEntry:
    term: str
    translation: str
    note: str = ""
    updated_at: int = 0


STATUS_RANK = {"draft": 1, "stable": 2, "final": 3}


def status_rank(status) -> int:
    name = getattr(status, "name", str(status)).lower()
    return STATUS_RANK.get(name, 2)
