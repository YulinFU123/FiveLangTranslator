from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
import re
import numpy as np

class AudioSourceKind(StrEnum):
    MICROPHONE = "microphone"
    SYSTEM_LOOPBACK = "system_loopback"

class DeviceSelectionMode(StrEnum):
    FOLLOW_DEFAULT = "follow_default"
    SPECIFIC = "specific"
    PREFER_SPECIFIC = "prefer_specific"

class AudioActivity(StrEnum):
    NO_AUDIO = "no_audio"
    SILENCE = "silence"
    POSSIBLE_SPEECH = "possible_speech"
    SPEECH_STARTED = "speech_started"
    SPEECH = "speech"
    SPEECH_ENDED = "speech_ended"
    WAITING_DEVICE = "waiting_device"

@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    name_key: str
    source_kind: AudioSourceKind
    is_loopback: bool
    host_api_name: str = ""

@dataclass(slots=True)
class AudioDevice:
    device_id: int
    name: str
    source_kind: AudioSourceKind
    sample_rate: int
    channels: int
    is_default: bool = False
    is_loopback: bool = False
    host_api_name: str = ""
    @property
    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(normalize_name(self.name), self.source_kind,
                              self.is_loopback, self.host_api_name.casefold().strip())

@dataclass(slots=True)
class NativeAudioPacket:
    pcm_bytes: bytes
    sample_rate: int
    channels: int
    sample_width: int
    timestamp_ms: int

@dataclass(slots=True)
class AudioFrame:
    samples: np.ndarray
    sample_rate: int
    start_ms: int
    end_ms: int
    level_dbfs: float
    speech_probability: float

@dataclass(slots=True)
class AudioSpeechSegment:
    segment_id: str
    samples: np.ndarray
    sample_rate: int
    start_ms: int
    end_ms: int
    source_kind: AudioSourceKind
    generation: int
    is_final: bool = True

def normalize_name(name: str) -> str:
    value = re.sub(r"\s*\(loopback\)\s*$", "", name.casefold().strip())
    return re.sub(r"\s+", " ", value)
