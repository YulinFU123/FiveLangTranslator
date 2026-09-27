from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(slots=True)
class FilterDecision:
    accepted: bool
    text: str
    reason: str = ""
    score: float = 1.0


class HallucinationFilter:
    """Conservative post-filter for common Whisper hallucinations."""

    def __init__(self, repeat_window: int = 6) -> None:
        self.history: deque[str] = deque(maxlen=repeat_window)
        self.blocked_phrases = {
            "thank you for watching", "thanks for watching", "subscribe",
            "字幕由", "ご視聴ありがとうございました", "ありがとうございました",
        }

    def evaluate(self, text: str, confidence: float = 1.0, no_speech_probability: float | None = None) -> FilterDecision:
        normalized = self._normalize(text)
        if not normalized:
            return FilterDecision(False, "", "empty", 0.0)
        lower = normalized.casefold()
        if no_speech_probability is not None and no_speech_probability >= 0.80:
            return FilterDecision(False, normalized, "high_no_speech_probability", 0.1)
        if confidence < 0.20:
            return FilterDecision(False, normalized, "low_confidence", confidence)
        if any(phrase.casefold() in lower for phrase in self.blocked_phrases) and len(normalized) < 48:
            return FilterDecision(False, normalized, "known_hallucination_phrase", 0.2)
        if self._symbol_ratio(normalized) > 0.55:
            return FilterDecision(False, normalized, "too_many_symbols", 0.2)
        occurrences = sum(SequenceMatcher(None, item, normalized).ratio() >= 0.97 for item in self.history)
        if occurrences >= 3:
            return FilterDecision(False, normalized, "repetition_loop", 0.2)
        self.history.append(normalized)
        return FilterDecision(True, normalized, score=max(0.0, min(1.0, confidence)))

    def reset(self) -> None:
        self.history.clear()

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.strip().split())

    @staticmethod
    def _symbol_ratio(text: str) -> float:
        if not text:
            return 1.0
        symbols = sum(not char.isalnum() and not char.isspace() and char not in "，。！？、,.!?;:'\"()-" for char in text)
        return symbols / len(text)
