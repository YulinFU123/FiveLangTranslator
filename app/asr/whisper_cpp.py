from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import numpy as np

from app.core.models import RecognitionResult, SourceType, SubtitleStatus

SUPPORTED_LANGUAGES = {"auto", "zh", "en", "ja", "ru", "de"}


@dataclass(slots=True)
class WhisperCppConfig:
    executable: Path
    model: Path
    language: str = "auto"
    threads: int = 8
    draft_beam_size: int = 1
    final_beam_size: int = 3
    timeout_seconds: float = 90.0
    use_gpu: bool = True


@dataclass(slots=True)
class BackendProbe:
    available: bool
    executable: str = ""
    model: str = ""
    vulkan_hint: bool = False
    details: str = ""


class WhisperCppProvider:
    provider_id = "whisper_cpp"

    def __init__(self, config: WhisperCppConfig) -> None:
        self.config = config
        self._revision: dict[str, int] = {}
        self.last_latency_ms = 0
        self.last_realtime_factor = 0.0

    @staticmethod
    def discover(root: Path | None = None) -> Path | None:
        candidates: list[Path] = []
        env = os.environ.get("WHISPER_CPP_PATH")
        if env:
            candidates.append(Path(env))
        if root:
            candidates.extend([
                root / "tools" / "whisper.cpp" / "whisper-cli.exe",
                root / "tools" / "whisper.cpp" / "whisper-cli",
                root / "tools" / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
                root / "tools" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe",
            ])
        found = shutil.which("whisper-cli") or shutil.which("whisper-cli.exe")
        if found:
            candidates.append(Path(found))
        return next((path for path in candidates if path.is_file()), None)

    @classmethod
    def probe(cls, executable: Path | None, model: Path | None) -> BackendProbe:
        if executable is None or not executable.is_file():
            return BackendProbe(False, details="未找到 whisper-cli 可执行文件")
        if model is None or not model.is_file():
            return BackendProbe(False, executable=str(executable), details="未找到 GGML 模型文件")
        try:
            completed = subprocess.run(
                [str(executable), "--help"], capture_output=True, text=True,
                errors="replace", timeout=10, creationflags=_no_window_flag(),
            )
            output = (completed.stdout + "\n" + completed.stderr).strip()
            vulkan = "vulkan" in output.casefold() or "gpu" in output.casefold()
            return BackendProbe(completed.returncode in (0, 1), str(executable), str(model), vulkan, output[-1200:])
        except Exception as exc:
            return BackendProbe(False, str(executable), str(model), False, str(exc))

    async def initialize(self) -> None:
        probe = self.probe(self.config.executable, self.config.model)
        if not probe.available:
            raise RuntimeError(probe.details)

    async def close(self) -> None:
        return None

    async def recognize_audio_segment(self, segment) -> RecognitionResult:
        language = self.config.language if self.config.language in SUPPORTED_LANGUAGES else "auto"
        is_final = bool(getattr(segment, "is_final", True))
        beam = self.config.final_beam_size if is_final else self.config.draft_beam_size
        started = monotonic()
        text, detected, metadata = await self._run_cli_async(
            segment.samples, segment.sample_rate, language, beam
        )
        elapsed = monotonic() - started
        self.last_latency_ms = int(elapsed * 1000)
        duration = max(0.001, (segment.end_ms - segment.start_ms) / 1000)
        self.last_realtime_factor = elapsed / duration
        revision = self._revision.get(segment.segment_id, 0) + 1
        self._revision[segment.segment_id] = revision
        status = SubtitleStatus.FINAL if is_final else SubtitleStatus.DRAFT
        source = SourceType.SYSTEM_AUDIO if segment.source_kind.value == "system_loopback" else SourceType.MICROPHONE
        confidence = float(metadata.get("confidence", 0.85 if text.strip() else 0.0))
        no_speech = metadata.get("no_speech_probability")
        return RecognitionResult(
            segment.segment_id, revision, text.strip(), detected or language,
            confidence, segment.start_ms, segment.end_ms, status,
            self.provider_id, source, no_speech, metadata,
        )

    async def _run_cli_async(self, samples: np.ndarray, sample_rate: int, language: str, beam_size: int) -> tuple[str, str, dict]:
        with tempfile.TemporaryDirectory(prefix="flt-whisper-") as directory:
            temp = Path(directory)
            wav_path = temp / "audio.wav"
            output_base = temp / "result"
            _write_pcm16_wav(wav_path, samples, sample_rate)
            command = [
                str(self.config.executable), "-m", str(self.config.model),
                "-f", str(wav_path), "-l", language,
                "-t", str(max(1, self.config.threads)),
                "-bs", str(max(1, beam_size)), "-bo", str(max(1, beam_size)),
                "-oj", "-of", str(output_base), "-np",
            ]
            if not self.config.use_gpu:
                command.append("-ng")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_no_window_flag(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.config.timeout_seconds
                )
            except asyncio.CancelledError:
                await _terminate_process(process)
                raise
            except asyncio.TimeoutError as exc:
                await _terminate_process(process)
                raise RuntimeError(f"whisper-cli 超时：{self.config.timeout_seconds:.0f}秒") from exc
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            if process.returncode != 0:
                message = (stderr_text or stdout_text).strip()
                raise RuntimeError(f"whisper-cli 失败 ({process.returncode})：{message[-1000:]}")
            json_path = output_base.with_suffix(".json")
            if json_path.exists():
                return _parse_json_detailed(json_path)
            return _parse_console(stdout_text), language, {}


def _write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm.tobytes())


def _parse_json(path: Path) -> tuple[str, str]:
    text, language, _ = _parse_json_detailed(path)
    return text, language


def _parse_json_detailed(path: Path) -> tuple[str, str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    language = "auto"
    result = payload.get("result")
    if isinstance(result, dict):
        language = str(result.get("language") or language)
    transcription = payload.get("transcription", [])
    parts: list[str] = []
    for item in transcription if isinstance(transcription, list) else []:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text.strip())
    if not parts and isinstance(payload.get("text"), str):
        parts.append(payload["text"].strip())
    metadata = {}
    probabilities = []
    no_speech_values = []
    for item in transcription if isinstance(transcription, list) else []:
        if isinstance(item, dict):
            for key in ("probability", "confidence"):
                if isinstance(item.get(key), (int, float)):
                    probabilities.append(float(item[key]))
            if isinstance(item.get("no_speech_prob"), (int, float)):
                no_speech_values.append(float(item["no_speech_prob"]))
    if probabilities:
        metadata["confidence"] = sum(probabilities) / len(probabilities)
    if no_speech_values:
        metadata["no_speech_probability"] = sum(no_speech_values) / len(no_speech_values)
    return " ".join(part for part in parts if part).strip(), language, metadata


async def _terminate_process(process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def _parse_console(output: str) -> str:
    parts = []
    for line in output.splitlines():
        clean = re.sub(r"^\[[^\]]+\]\s*", "", line).strip()
        if clean and not clean.startswith(("whisper_", "system_info", "main:")):
            parts.append(clean)
    return " ".join(parts).strip()


def _no_window_flag() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
