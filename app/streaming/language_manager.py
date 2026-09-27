from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

ALLOWED = {"zh", "en", "ja", "ru", "de"}


@dataclass(slots=True)
class LanguageState:
    configured: str = "auto"
    detected: str = "auto"
    locked: bool = False
    confidence: float = 0.0


class LanguageManager:
    def __init__(self, configured: str = "auto", lock_after: int = 3, unlock_after: int = 3) -> None:
        self.state = LanguageState(configured=configured)
        self.lock_after = lock_after
        self.unlock_after = unlock_after
        self.history = deque(maxlen=max(lock_after, unlock_after) + 2)
        self.mismatch_count = 0
        self.configure(configured)

    def configure(self, language: str) -> None:
        language = language if language in ALLOWED | {"auto"} else "auto"
        self.state = LanguageState(configured=language, detected=language if language != "auto" else "auto", locked=language != "auto")
        self.history.clear(); self.mismatch_count = 0

    def observe(self, language: str, confidence: float = 1.0) -> LanguageState:
        if language not in ALLOWED:
            return self.state
        if self.state.configured != "auto":
            if language != self.state.configured and confidence >= 0.8:
                self.mismatch_count += 1
            else:
                self.mismatch_count = 0
            return self.state
        self.history.append(language)
        winner, count = Counter(self.history).most_common(1)[0]
        ratio = count / len(self.history)
        if not self.state.locked and count >= self.lock_after:
            self.state.detected = winner; self.state.locked = True; self.state.confidence = ratio
        elif self.state.locked:
            if language != self.state.detected:
                self.mismatch_count += 1
                if self.mismatch_count >= self.unlock_after:
                    self.state.locked = False; self.state.detected = winner; self.state.confidence = ratio; self.mismatch_count = 0
            else:
                self.mismatch_count = 0; self.state.confidence = ratio
        else:
            self.state.detected = winner; self.state.confidence = ratio
        return self.state

    @property
    def inference_language(self) -> str:
        if self.state.configured != "auto": return self.state.configured
        return self.state.detected if self.state.locked else "auto"
