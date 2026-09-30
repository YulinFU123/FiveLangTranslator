from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from app.asr.whisper_cpp import _no_window_flag, _write_pcm16_wav
from app.core.models import RecognitionResult, SourceType, SubtitleStatus


@dataclass(slots=True)
class WhisperServerConfig:
    executable: Path
    model: Path
    host: str = "127.0.0.1"
    port: int = 8178
    language: str = "auto"
    threads: int = 8
    use_gpu: bool = True
    startup_timeout: float = 60.0
    request_timeout: float = 90.0
    # Beam width for the server's own decoding. CPU inference is dominated by
    # beam search, so a small beam is the single biggest speed lever here.
    beam_size: int = 1

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class WhisperServerProcess:
    def __init__(self, config: WhisperServerConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.stdout_task: asyncio.Task | None = None
        self.log_lines: list[str] = []
        self.restart_count = 0

    @staticmethod
    def discover(root: Path | None = None) -> Path | None:
        candidates: list[Path] = []
        env = os.environ.get("WHISPER_SERVER_PATH")
        if env:
            candidates.append(Path(env))
        tools = None
        if root:
            tools = root / "tools" / "whisper.cpp"
            candidates.extend([
                # Prefer a locally built Vulkan (GPU) build when present.
                tools / "Vulkan" / "whisper-server.exe",
                tools / "whisper-server.exe",
                tools / "whisper-server",
                # The official whisper-bin-x64.zip unpacks straight into
                # <tools>/Release/ instead of <tools>/build/bin/Release/.
                tools / "Release" / "whisper-server.exe",
                tools / "build" / "bin" / "Release" / "whisper-server.exe",
            ])
        found = shutil.which("whisper-server") or shutil.which("whisper-server.exe")
        if found:
            candidates.append(Path(found))
        for path in candidates:
            if path.is_file():
                return path
        # Recursive fallback so a future archive layout cannot silently disable ASR.
        if tools and tools.is_dir():
            for path in sorted(tools.rglob("whisper-server.exe")):
                if path.is_file():
                    return path
        return None

    async def start(self) -> None:
        if self.is_running:
            return
        if not self.config.executable.is_file():
            raise RuntimeError("未找到 whisper-server 可执行文件")
        if not self.config.model.is_file():
            raise RuntimeError("未找到 whisper.cpp 模型")
        command = [
            str(self.config.executable), "-m", str(self.config.model),
            "--host", self.config.host, "--port", str(self.config.port),
            "-t", str(max(1, self.config.threads)),
            "-bs", str(max(1, self.config.beam_size)),
            "-bo", str(max(1, self.config.beam_size)),
        ]
        if not self.config.use_gpu:
            command.append("-ng")
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=_no_window_flag(),
            env=_gpu_env(),
        )
        self.stdout_task = asyncio.create_task(self._collect_output())
        deadline = monotonic() + self.config.startup_timeout
        while monotonic() < deadline:
            if self.process.returncode is not None:
                raise RuntimeError("whisper-server 启动失败：" + "\n".join(self.log_lines[-15:]))
            if await asyncio.to_thread(_port_open, self.config.host, self.config.port):
                return
            await asyncio.sleep(.25)
        await self.stop()
        raise RuntimeError(f"whisper-server 启动超时：{self.config.startup_timeout:.0f}秒")

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self.stdout_task:
            self.stdout_task.cancel()
            self.stdout_task = None

    async def restart(self) -> None:
        await self.stop()
        self.restart_count += 1
        await self.start()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _collect_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            while line := await self.process.stdout.readline():
                self.log_lines.append(line.decode("utf-8", errors="replace").rstrip())
                del self.log_lines[:-200]
        except asyncio.CancelledError:
            return


class WhisperServerProvider:
    provider_id = "whisper_server"

    def __init__(self, process: WhisperServerProcess) -> None:
        self.process = process
        self.config = process.config
        self.revisions: dict[str, int] = {}
        self.last_latency_ms = 0
        self.last_realtime_factor = 0.0

    async def initialize(self) -> None:
        await self.process.start()

    async def close(self) -> None:
        await self.process.stop()

    async def health_check(self) -> bool:
        return self.process.is_running and await asyncio.to_thread(
            _port_open, self.config.host, self.config.port
        )

    async def recognize_audio_segment(self, segment) -> RecognitionResult:
        if not await self.health_check():
            await self.process.restart()
        started = monotonic()
        with tempfile.TemporaryDirectory(prefix="flt-server-") as directory:
            audio_path = Path(directory) / "audio.wav"
            _write_pcm16_wav(audio_path, segment.samples, segment.sample_rate)
            payload = await asyncio.to_thread(
                _post_inference,
                self.config.base_url + "/inference",
                audio_path,
                self.config.language,
                self.config.request_timeout,
            )
        elapsed = monotonic() - started
        self.last_latency_ms = int(elapsed * 1000)
        duration = max(.001, (segment.end_ms - segment.start_ms) / 1000)
        self.last_realtime_factor = elapsed / duration
        revision = self.revisions.get(segment.segment_id, 0) + 1
        self.revisions[segment.segment_id] = revision
        text, language, metadata = _parse_server_payload(payload, self.config.language)
        source = SourceType.SYSTEM_AUDIO if segment.source_kind.value == "system_loopback" else SourceType.MICROPHONE
        return RecognitionResult(
            segment.segment_id,
            revision,
            text,
            language,
            float(metadata.get("confidence", .85 if text else 0.0)),
            segment.start_ms,
            segment.end_ms,
            SubtitleStatus.FINAL if segment.is_final else SubtitleStatus.DRAFT,
            self.provider_id,
            source,
            metadata.get("no_speech_probability"),
            metadata,
        )


def _gpu_env() -> dict:
    """Pick the discrete GPU for the Vulkan backend.

    Without this ggml may latch onto the CPU's integrated graphics (which shows
    up as "AMD Radeon(TM) Graphics") and leave a much faster card idle.
    """
    env = os.environ.copy()
    env.setdefault("GGML_VULKAN_DEVICE", "0")
    return env


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=.5):
            return True
    except OSError:
        return False


def _post_inference(url: str, audio_path: Path, language: str, timeout: float):
    boundary = "----FiveLang" + uuid.uuid4().hex
    audio = audio_path.read_bytes()
    fields = {
        "response_format": "json",
        "temperature": "0.0",
        "language": language,
    }
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode(), b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        audio, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    request = Request(
        url,
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"whisper-server 请求失败：{exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw.strip()}


def _parse_server_payload(payload, fallback_language: str):
    if isinstance(payload, str):
        return payload.strip(), fallback_language, {}
    text = ""
    language = fallback_language
    metadata = {}
    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()
        language = str(payload.get("language") or fallback_language)
        if not text and isinstance(payload.get("transcription"), list):
            text = " ".join(
                str(item.get("text", "")).strip()
                for item in payload["transcription"] if isinstance(item, dict)
            ).strip()
        for key in ("confidence", "no_speech_probability"):
            if isinstance(payload.get(key), (int, float)):
                metadata[key] = float(payload[key])
    return text, language, metadata
