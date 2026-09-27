from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Profile:
    x: float = .12
    y: float = .76
    w: float = .76
    h: float = .16
    screen_name: str = ""


@dataclass(slots=True)
class Settings:
    locked: bool = False
    through: bool = False
    show_source: bool = True
    opacity: int = 76
    source_font: int = 16
    translation_font: int = 27
    audio_kind: str = "system_loopback"
    preferred_device_name: str = ""
    asr_backend: str = "auto"
    whisper_server_executable: str = ""
    whisper_server_port: int = 8178
    whisper_server_fallback: bool = True
    whisper_executable: str = ""
    whisper_model: str = ""
    asr_language: str = "auto"
    asr_use_gpu: bool = True
    asr_cpu_fallback: bool = True
    translation_provider: str = "ollama"
    translation_fallback_provider: str = "openai_compatible"
    translation_target_language: str = "zh"
    translation_style: str = "cinema"
    overlay_layout_mode: str = "stacked"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    openai_compatible_url: str = "http://127.0.0.1:1234/v1"
    openai_compatible_model: str = "local-model"
    # Ordered fallback chain. First entry answers first.
    translation_chain: list[str] = field(default_factory=lambda: ["ollama", "openai_compatible"])
    # Per provider endpoint overrides: {"deepseek": {"base_url": "...", "model": "..."}}
    translation_endpoints: dict[str, dict] = field(default_factory=dict)
    auto_record_sessions: bool = True
    export_format: str = "srt"
    export_content: str = "bilingual"
    follow_player: bool = True
    hide_when_player_minimized: bool = True
    auto_fullscreen_profile: bool = True
    player_process: str = ""
    profile: Profile | None = None
    windowed_profile: Profile | None = None
    fullscreen_profile: Profile | None = None

    def __post_init__(self) -> None:
        legacy = self.profile or Profile()
        self.windowed_profile = self.windowed_profile or legacy
        self.fullscreen_profile = self.fullscreen_profile or Profile(.08, .79, .84, .14)
        self.profile = self.windowed_profile


class Store:
    def __init__(self) -> None:
        self.path = Path.home() / ".five_lang_translator" / "settings.json"

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key in ("profile", "windowed_profile", "fullscreen_profile"):
                if isinstance(raw.get(key), dict):
                    raw[key] = Profile(**raw[key])
            return Settings(**raw)
        except Exception:
            return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
