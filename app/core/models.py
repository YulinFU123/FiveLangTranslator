from dataclasses import dataclass
from enum import IntEnum, StrEnum
from uuid import uuid4


class SubtitleStatus(IntEnum):
    ERROR = 0
    DRAFT = 1
    STABLE = 2
    FINAL = 3


class SourceType(StrEnum):
    SYSTEM_AUDIO = "system_audio"
    MICROPHONE = "microphone"
    OCR = "ocr"


@dataclass(slots=True)
class SpeechSegment:
    segment_id: str
    samples: bytes
    sample_rate: int
    start_ms: int
    end_ms: int
    source: SourceType
    language_hint: str | None = None


@dataclass(slots=True)
class RecognitionResult:
    segment_id: str
    revision: int
    text: str
    language: str
    confidence: float
    start_ms: int
    end_ms: int
    status: SubtitleStatus
    provider: str
    source: SourceType
    no_speech_probability: float | None = None
    metadata: dict | None = None


@dataclass(slots=True)
class TranslationRequest:
    segment_id: str
    revision: int
    text: str
    source_language: str
    target_language: str
    status: SubtitleStatus
    style: str = "cinema"


@dataclass(slots=True)
class TranslationResult:
    segment_id: str
    revision: int
    source_text: str
    translated_text: str
    source_language: str
    target_language: str
    status: SubtitleStatus
    provider: str
    latency_ms: int
    model: str = ""
    cache_hit: bool = False


@dataclass(slots=True)
class SubtitleViewModel:
    segment_id: str
    revision: int
    source_text: str
    translated_text: str
    status: SubtitleStatus
    source: SourceType
    stable_source_text: str = ""
    draft_source_text: str = ""
    source_language: str = "auto"
    target_language: str = "zh"
    start_ms: int = 0
    end_ms: int = 0
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cache_hit: bool = False


def new_id() -> str:
    return uuid4().hex
