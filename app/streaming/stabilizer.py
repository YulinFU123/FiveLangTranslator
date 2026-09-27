from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.core.models import RecognitionResult, SubtitleStatus


@dataclass(slots=True)
class StableTranscript:
    committed: str
    draft: str
    status: SubtitleStatus
    similarity: float

    @property
    def display_text(self) -> str:
        return (self.committed + self.draft).strip()


class TranscriptStabilizer:
    """Converts changing ASR hypotheses into DRAFT/STABLE/FINAL states.

    Space-delimited languages commit token prefixes. Chinese and Japanese
    commit character prefixes so they do not depend on whitespace.
    """

    def __init__(self, confirmations: int = 2) -> None:
        self.confirmations = max(2, confirmations)
        self._previous: dict[str, str] = {}
        self._committed: dict[str, str] = {}
        self._candidate: dict[str, str] = {}
        self._candidate_count: dict[str, int] = {}

    def update(self, result: RecognitionResult) -> tuple[RecognitionResult, StableTranscript]:
        key = result.segment_id
        text = self._normalize(result.text)
        previous = self._previous.get(key, "")
        committed = self._committed.get(key, "")
        similarity = SequenceMatcher(None, previous, text).ratio() if previous else 1.0

        if result.status == SubtitleStatus.FINAL:
            self._clear(key)
            final = self._clone(result, text, SubtitleStatus.FINAL)
            return final, StableTranscript(text, "", SubtitleStatus.FINAL, similarity)

        common = self._common_prefix(previous, text, result.language)
        common = self._not_before(committed, common)
        if common and common != committed:
            if self._candidate.get(key) == common:
                self._candidate_count[key] = self._candidate_count.get(key, 1) + 1
            else:
                self._candidate[key] = common
                self._candidate_count[key] = 1
            if self._candidate_count[key] >= self.confirmations - 1:
                committed = common
                self._committed[key] = committed
        self._previous[key] = text
        draft = text[len(committed):] if text.startswith(committed) else text
        status = SubtitleStatus.STABLE if committed else SubtitleStatus.DRAFT
        updated = self._clone(result, text, status)
        return updated, StableTranscript(committed, draft, status, similarity)

    def reset(self) -> None:
        self._previous.clear(); self._committed.clear(); self._candidate.clear(); self._candidate_count.clear()

    def _clear(self, key: str) -> None:
        for mapping in (self._previous, self._committed, self._candidate, self._candidate_count):
            mapping.pop(key, None)

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.strip().split())

    @staticmethod
    def _not_before(committed: str, common: str) -> str:
        return common if len(common) >= len(committed) and common.startswith(committed) else committed

    @staticmethod
    def _common_prefix(left: str, right: str, language: str) -> str:
        if not left or not right:
            return ""
        char_mode = language in {"zh", "ja"}
        if char_mode:
            length = 0
            for a, b in zip(left, right):
                if a != b: break
                length += 1
            return right[:length]
        left_tokens = left.split()
        right_tokens = right.split()
        output = []
        for a, b in zip(left_tokens, right_tokens):
            if a != b: break
            output.append(b)
        return (" ".join(output) + (" " if output else ""))

    @staticmethod
    def _clone(result: RecognitionResult, text: str, status: SubtitleStatus) -> RecognitionResult:
        return RecognitionResult(
            result.segment_id, result.revision, text, result.language,
            result.confidence, result.start_ms, result.end_ms,
            status, result.provider, result.source,
        )
