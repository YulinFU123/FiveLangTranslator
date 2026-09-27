from __future__ import annotations

from collections import deque


class TranslationContext:
    def __init__(self, maximum_sentences: int = 3) -> None:
        self.sentences = deque(maxlen=maximum_sentences)

    def snapshot(self) -> list[str]:
        return list(self.sentences)

    def commit(self, source_text: str, translated_text: str) -> None:
        self.sentences.append(f"Source: {source_text}\nTranslation: {translated_text}")

    def clear(self) -> None:
        self.sentences.clear()
