from __future__ import annotations

from collections import deque

# How many recent Source/Translation pairs travel with every request. Each extra
# pair costs prompt tokens, and for a local model that is the dominant part of
# the round-trip, so the default stays deliberately small. Tunable at runtime via
# TranslationService.set_context_sentences().
DEFAULT_CONTEXT_SENTENCES = 2


class TranslationContext:
    def __init__(self, maximum_sentences: int = DEFAULT_CONTEXT_SENTENCES) -> None:
        self.sentences = deque(maxlen=maximum_sentences)

    def snapshot(self) -> list[str]:
        return list(self.sentences)

    def commit(self, source_text: str, translated_text: str) -> None:
        self.sentences.append(f"Source: {source_text}\nTranslation: {translated_text}")

    def clear(self) -> None:
        self.sentences.clear()
