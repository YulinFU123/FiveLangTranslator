from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.asr.benchmark import benchmark_wav, save_benchmark
from app.asr.filters import HallucinationFilter
from app.asr.priority_worker import ASRPriorityWorker
from app.asr.whisper_cpp import WhisperCppConfig, WhisperCppProvider
from app.asr.whisper_server import (
    WhisperServerConfig,
    WhisperServerProcess,
    WhisperServerProvider,
)
from app.streaming.language_manager import LanguageManager
from app.streaming.stabilizer import TranscriptStabilizer


class ASRService(QObject):
    result_ready = Signal(object)
    transcript_state = Signal(object)
    language_state = Signal(object)
    backend_status = Signal(str)
    metrics_changed = Signal(object)
    benchmark_finished = Signal(object)
    error = Signal(str)

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.provider = None
        self.cli_provider: WhisperCppProvider | None = None
        self.server_process: WhisperServerProcess | None = None
        self.worker: ASRPriorityWorker | None = None
        self.delivery_tasks: set[asyncio.Task] = set()
        self.language_manager = LanguageManager("auto")
        self.stabilizer = TranscriptStabilizer(confirmations=2)
        self.hallucination_filter = HallucinationFilter()
        self.enabled = False
        self.cpu_fallback = True
        self.server_fallback = True
        self.backend_mode = "auto"
        self._cli_config: WhisperCppConfig | None = None

    def configure(
        self,
        executable: str,
        model: str,
        language: str = "auto",
        use_gpu: bool = True,
        cpu_fallback: bool = True,
        backend_mode: str = "auto",
        server_executable: str = "",
        server_port: int = 8178,
        server_fallback: bool = True,
    ) -> None:
        asyncio.create_task(self._configure_async(
            executable, model, language, use_gpu, cpu_fallback,
            backend_mode, server_executable, server_port, server_fallback,
        ))

    async def _configure_async(self, executable, model, language, use_gpu, cpu_fallback, backend_mode, server_executable, server_port, server_fallback) -> None:
        await self._close_backend()
        cli_exe = Path(executable).expanduser() if executable else WhisperCppProvider.discover(self.project_root)
        model_path = Path(model).expanduser() if model else self._discover_model()
        probe = WhisperCppProvider.probe(cli_exe, model_path)
        self.language_manager.configure(language)
        self.stabilizer.reset()
        self.hallucination_filter.reset()
        self.cpu_fallback = cpu_fallback
        self.server_fallback = server_fallback
        self.backend_mode = backend_mode if backend_mode in {"auto", "server", "cli"} else "auto"
        self.cli_provider = None
        if probe.available:
            self._cli_config = WhisperCppConfig(cli_exe, model_path, language=language, use_gpu=use_gpu)
            self.cli_provider = WhisperCppProvider(self._cli_config)

        server_exe = Path(server_executable).expanduser() if server_executable else WhisperServerProcess.discover(self.project_root)
        wants_server = self.backend_mode in {"auto", "server"}
        if wants_server and server_exe and model_path and model_path.is_file():
            config = WhisperServerConfig(server_exe, model_path, port=int(server_port), language=language, use_gpu=use_gpu)
            process = WhisperServerProcess(config)
            server = WhisperServerProvider(process)
            try:
                self.backend_status.emit("正在启动 whisper-server 并加载模型…")
                await server.initialize()
                self.server_process = process
                self.provider = server
                self.enabled = True
                self.worker = ASRPriorityWorker(self._recognize_with_fallback)
                self.backend_status.emit(f"whisper-server 已就绪 · 模型常驻 · 端口 {server_port}")
                return
            except Exception as exc:
                await process.stop()
                self.server_process = None
                if self.backend_mode == "server" and not self.server_fallback:
                    self.provider = None; self.enabled = False
                    self.backend_status.emit(f"whisper-server 启动失败：{exc}")
                    return
                self.backend_status.emit(f"Server不可用，回退CLI：{exc}")

        if self.cli_provider:
            self.provider = self.cli_provider
            self.enabled = True
            self.worker = ASRPriorityWorker(self._recognize_with_fallback)
            backend = "GPU/Vulkan" if use_gpu else "CPU"
            self.backend_status.emit(f"whisper.cpp CLI 已就绪 · {backend} · {model_path.name}")
        else:
            self.provider = None; self.enabled = False
            self.backend_status.emit(probe.details or "未找到可用whisper.cpp后端")

    def submit(self, segment) -> None:
        if not self.enabled or self.provider is None or self.worker is None:
            return
        future = self.worker.submit(segment)
        task = asyncio.create_task(self._deliver(future))
        self.delivery_tasks.add(task)
        task.add_done_callback(self.delivery_tasks.discard)

    def _preempt_metrics(self) -> dict:
        w = self.worker
        if not w:
            return {"preemption_count": 0, "draft_discarded_count": 0, "preemption_save_ms": 0.0}
        return {
            "preemption_count": w.preemption_count,
            "draft_discarded_count": w.draft_discarded_count,
            "preemption_save_ms": w.preemption_save_ms,
        }

    async def _deliver(self, future: asyncio.Future) -> None:
        try:
            result, provider, fallback_name = await future
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.error.emit(str(exc)); return
        decision = self.hallucination_filter.evaluate(result.text, result.confidence, result.no_speech_probability)
        if not decision.accepted:
            self.metrics_changed.emit({
                "latency_ms": provider.last_latency_ms,
                "realtime_factor": provider.last_realtime_factor,
                "status": "filtered",
                "language": result.language,
                "filter_reason": decision.reason,
                "queue_depth": self.worker.depth if self.worker else 0,
                "queue_wait_ms": self.worker.last_queue_wait_ms if self.worker else 0,
                **self._preempt_metrics(),
            })
            return
        result.text = decision.text
        language = self.language_manager.observe(result.language, result.confidence)
        stable_result, transcript = self.stabilizer.update(result)
        stable_result.metadata = dict(stable_result.metadata or {})
        stable_result.metadata.update({"stable_source_text": transcript.committed, "draft_source_text": transcript.draft, "stability_similarity": transcript.similarity})
        self.language_state.emit({"configured": language.configured, "detected": language.detected, "locked": language.locked, "confidence": language.confidence, "mismatch_count": self.language_manager.mismatch_count})
        self.transcript_state.emit({"segment_id": result.segment_id, "committed": transcript.committed, "draft": transcript.draft, "status": transcript.status.name.lower(), "similarity": transcript.similarity})
        self.result_ready.emit(stable_result)
        self.metrics_changed.emit({
            "latency_ms": provider.last_latency_ms,
            "realtime_factor": provider.last_realtime_factor,
            "status": stable_result.status.name.lower(),
            "language": stable_result.language,
            "language_locked": language.locked,
            "fallback": fallback_name,
            "backend": provider.provider_id,
            "queue_depth": self.worker.depth if self.worker else 0,
            "queue_wait_ms": self.worker.last_queue_wait_ms if self.worker else 0,
            **self._preempt_metrics(),
        })

    async def _recognize_with_fallback(self, segment):
        provider = self.provider
        if provider is None:
            raise RuntimeError("ASR provider is not configured")
        provider.config.language = self.language_manager.inference_language
        try:
            return await provider.recognize_audio_segment(segment), provider, ""
        except asyncio.CancelledError:
            raise
        except Exception as primary_error:
            if provider.provider_id == "whisper_server" and self.server_fallback and self.cli_provider:
                self.backend_status.emit("Server请求失败，当前片段回退CLI")
                self.cli_provider.config.language = self.language_manager.inference_language
                try:
                    return await self.cli_provider.recognize_audio_segment(segment), self.cli_provider, "cli"
                except Exception:
                    pass
            cli = self.cli_provider if self.cli_provider else provider if provider.provider_id == "whisper_cpp" else None
            if cli and cli.config.use_gpu and self.cpu_fallback and self._cli_config:
                self.backend_status.emit("GPU识别失败，当前片段回退CPU")
                fallback = WhisperCppProvider(WhisperCppConfig(self._cli_config.executable, self._cli_config.model, language=self.language_manager.inference_language, threads=self._cli_config.threads, draft_beam_size=self._cli_config.draft_beam_size, final_beam_size=self._cli_config.final_beam_size, timeout_seconds=self._cli_config.timeout_seconds, use_gpu=False))
                return await fallback.recognize_audio_segment(segment), fallback, "cpu"
            raise primary_error

    def run_benchmark(self, path: str) -> None:
        provider = self.cli_provider or self.provider
        if not provider or provider.provider_id != "whisper_cpp":
            self.error.emit("基准测试目前需要CLI后端"); return
        task = asyncio.create_task(self._benchmark(provider, Path(path)))
        self.delivery_tasks.add(task); task.add_done_callback(self.delivery_tasks.discard)

    async def _benchmark(self, provider, path: Path) -> None:
        try:
            result = await benchmark_wav(provider, path)
            report = save_benchmark(result, self.project_root / "logs")
            self.benchmark_finished.emit({"latency_ms": result.latency_ms, "realtime_factor": result.realtime_factor, "language": result.language, "text": result.text, "report": str(report), "gpu_requested": result.gpu_requested})
        except Exception as exc:
            self.error.emit(f"基准测试失败：{exc}")

    def cancel_all(self) -> None:
        for task in tuple(self.delivery_tasks): task.cancel()
        self.delivery_tasks.clear(); self.stabilizer.reset()
        if self.worker: asyncio.create_task(self.worker.close())

    async def _close_backend(self) -> None:
        self.cancel_all()
        if self.server_process:
            await self.server_process.stop()
        self.server_process = None; self.worker = None; self.provider = None; self.enabled = False

    async def close(self) -> None:
        await self._close_backend()

    def _discover_model(self) -> Path | None:
        directory = self.project_root / "models"
        for pattern in ("*large-v3-turbo*.bin", "*medium*.bin", "*small*.bin", "*.bin"):
            found = sorted(directory.glob(pattern))
            if found: return found[0]
        return None
