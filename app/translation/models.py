from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import SubtitleStatus
from app.translation.prompt import DEFAULT_MAX_LINES


@dataclass(slots=True)
class TranslationJob:
    segment_id: str
    revision: int
    text: str
    source_language: str
    target_language: str
    status: SubtitleStatus
    context: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)
    style: str = "cinema"
    max_lines: int = DEFAULT_MAX_LINES
    audio_end_ms: int = 0


@dataclass(slots=True)
class ProviderResult:
    text: str
    provider: str
    latency_ms: int
    model: str = ""
    metadata: dict = field(default_factory=dict)
