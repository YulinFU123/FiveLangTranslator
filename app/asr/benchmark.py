from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
import wave

import numpy as np

from app.asr.whisper_cpp import WhisperCppProvider
from app.audio.models import AudioSourceKind, AudioSpeechSegment


@dataclass(slots=True)
class BenchmarkResult:
    created_at: str
    audio_file: str
    duration_seconds: float
    latency_ms: int
    realtime_factor: float
    language: str
    text: str
    provider: str
    gpu_requested: bool
    model: str


async def benchmark_wav(provider: WhisperCppProvider, wav_path: Path) -> BenchmarkResult:
    with wave.open(str(wav_path), "rb") as stream:
        if stream.getsampwidth() != 2:
            raise ValueError("Benchmark WAV must use 16-bit PCM")
        channels = stream.getnchannels()
        rate = stream.getframerate()
        raw = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        raw = raw.reshape(-1, channels).mean(axis=1)
    duration = raw.size / rate
    segment = AudioSpeechSegment("benchmark", raw, rate, 0, int(duration * 1000), AudioSourceKind.MICROPHONE, 0, True)
    started = monotonic()
    result = await provider.recognize_audio_segment(segment)
    elapsed = monotonic() - started
    return BenchmarkResult(
        datetime.now(timezone.utc).isoformat(), str(wav_path), duration,
        int(elapsed * 1000), elapsed / max(duration, .001), result.language,
        result.text, result.provider, provider.config.use_gpu, str(provider.config.model),
    )


def save_benchmark(result: BenchmarkResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "latest-whisper-benchmark.json"
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
