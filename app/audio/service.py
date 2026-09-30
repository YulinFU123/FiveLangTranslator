from __future__ import annotations

import queue
import threading
from dataclasses import asdict, dataclass
from time import monotonic

import numpy as np

from PySide6.QtCore import QObject, QTimer, Signal

from app.audio.capture import PyAudioWASAPICapture
from app.audio.device_monitor import DeviceMonitor, DevicePolicy
from app.audio.models import AudioActivity, AudioFrame, AudioSourceKind, DeviceSelectionMode
from app.audio.processing import FixedFrameBuffer, SpeechSegmenter, StreamingLinearResampler, dbfs
from app.audio.vad import create_vad


@dataclass(slots=True)
class AudioHealth:
    packets_received: int = 0
    packets_dropped: int = 0
    processed_frames: int = 0
    draft_segments: int = 0
    final_segments: int = 0
    restart_count: int = 0
    consecutive_errors: int = 0
    queue_depth: int = 0
    last_packet_age_ms: int = 0
    active_device: str = ""
    vad_backend: str = "none"


class AudioService(QObject):
    devices_changed = Signal(object)
    active_device_changed = Signal(object)
    level_changed = Signal(float)
    probability_changed = Signal(float)
    activity_changed = Signal(str)
    draft_segment_ready = Signal(object)
    segment_ready = Signal(object)
    health_changed = Signal(object)
    status_changed = Signal(str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.capture = PyAudioWASAPICapture()
        self.monitor = DeviceMonitor(self.capture)
        self.monitor.on_change = self._devices_changed
        self.monitor.on_error = lambda exc: self.error.emit(str(exc))
        self.policy = DevicePolicy()
        self.converter = StreamingLinearResampler()
        self.frames = FixedFrameBuffer()
        self.segmenter = SpeechSegmenter()
        self.vad = None
        # Virtual cables (Voicemeeter / VB-Cable) often deliver audio near
        # -50 dBFS, where Silero reports ~0% speech and nothing ever reaches
        # whisper. This gain lifts the signal before VAD and segmentation.
        self.gain_db = 0.0
        self._gain_factor = 1.0
        self.packet_queue: queue.Queue = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.active = None
        self.generation = 0
        self.running = False
        self.next_frame_time: float | None = None
        self.last_packet_at = 0.0
        self.health = AudioHealth()
        self._recovery_lock = threading.Lock()
        self._manual_stop = True
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._publish_health)
        self._health_timer.start()

    def available(self) -> bool:
        try:
            self.capture._pyaudio()
            return True
        except Exception:
            return False

    def list_devices(self):
        try:
            return self.capture.list_devices()
        except Exception:
            return []

    def set_gain(self, gain_db: float) -> None:
        """Lift quiet sources before VAD/segmentation.

        Virtual cables often sit near -50 dBFS; boosting here raises the speech
        probability Silero reports and what whisper finally receives.
        """
        self.gain_db = float(gain_db or 0.0)
        self._gain_factor = 10.0 ** (self.gain_db / 20.0)

    def set_segment_policy(self, min_silence_ms=None, max_seconds=None) -> None:
        """Trade latency against accuracy when ending a sentence."""
        self.segmenter.configure(min_silence_ms=min_silence_ms, max_seconds=max_seconds)

    def start_monitor(self) -> None:
        if self.available():
            self.monitor.start()
            self.devices_changed.emit(list(self.monitor.snapshot.devices.values()))
        else:
            self.status_changed.emit("音频扩展未安装，当前保持模拟模式")

    def start_capture(self, device_id=None, kind=AudioSourceKind.SYSTEM_LOOPBACK) -> None:
        if not self.available():
            self.error.emit('请安装音频依赖：pip install -e ".[audio]"')
            return
        self._manual_stop = False
        self.policy.kind = kind
        devices = self.list_devices()
        self.monitor.snapshot = self.monitor.snapshot.build(devices)
        selected = next((d for d in devices if d.device_id == device_id), None) if device_id is not None else None
        if selected is None:
            # The UI may hand us an index captured from an older enumeration
            # (device unplugged / default switched). Fall back to the
            # source-appropriate default instead of refusing to start.
            selected = self.policy.select(self.monitor.snapshot)
        if not selected:
            self.status_changed.emit("没有找到可用音频设备")
            return
        self._restart(selected)

    def stop_capture(self) -> None:
        self._manual_stop = True
        self._stop_stream(flush=True)
        self.status_changed.emit("音频采集已停止")

    def close(self) -> None:
        self.monitor.stop()
        self._stop_stream(flush=False)
        if self.vad:
            self.vad.close()

    def _stop_stream(self, flush: bool) -> None:
        self.capture.stop()
        self.stop_event.set()
        if self.worker:
            self.worker.join(2.0)
            self.worker = None
        if flush:
            final = self.segmenter.flush(self.policy.kind, self.generation)
            if final:
                self.segment_ready.emit(final)
        else:
            self.segmenter.reset()
        self.running = False
        self.active = None
        self.activity_changed.emit(AudioActivity.NO_AUDIO.value)

    def _restart(self, device) -> None:
        with self._recovery_lock:
            self._manual_stop = False
            self._stop_stream(flush=False)
            self.generation += 1
            self.health.restart_count += 1
            self.packet_queue = queue.Queue(maxsize=64)
            self.converter.reset()
            self.frames.clear()
            self.segmenter.reset()
            self.next_frame_time = None
            if self.vad is None:
                self.vad = create_vad(True)
            else:
                self.vad.reset()
            self.health.vad_backend = self.vad.name
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._processing_loop, name="audio-processing", daemon=True)
            self.worker.start()
            try:
                self.capture.start(device.device_id, self._packet, self._capture_error,
                                   kind=device.source_kind)
                self.active = device
                self.running = True
                self.health.active_device = device.name
                self.health.consecutive_errors = 0
                self.active_device_changed.emit(device)
                self.status_changed.emit(f"正在采集：{device.name} · VAD {self.vad.name}")
            except Exception as exc:
                self.stop_event.set()
                self.health.consecutive_errors += 1
                self.error.emit(f"启动音频失败：{exc}")

    def _packet(self, packet) -> None:
        self.health.packets_received += 1
        self.last_packet_at = monotonic()
        try:
            self.packet_queue.put_nowait(packet)
        except queue.Full:
            self.health.packets_dropped += 1
            try:
                self.packet_queue.get_nowait()
                self.packet_queue.put_nowait(packet)
            except queue.Empty:
                pass

    def _capture_error(self, exc: Exception) -> None:
        self.health.consecutive_errors += 1
        self.error.emit(str(exc))
        if not self._manual_stop:
            threading.Thread(target=self._recover, name="audio-recovery", daemon=True).start()

    def _recover(self) -> None:
        if self.stop_event.wait(0.8) and self._manual_stop:
            return
        selected = self.policy.select(self.monitor.snapshot)
        if selected and not self._manual_stop:
            self._restart(selected)
        elif not self._manual_stop:
            self.status_changed.emit("设备已断开，等待重新连接")
            self.activity_changed.emit(AudioActivity.WAITING_DEVICE.value)

    def _devices_changed(self, old, new) -> None:
        self.devices_changed.emit(list(new.devices.values()))
        selected = self.policy.select(new)
        lost = self.running and (not self.active or not new.find(self.active.identity))
        default_changed = self.running and self.policy.mode == DeviceSelectionMode.FOLLOW_DEFAULT and selected and selected.identity != self.active.identity
        if (lost or default_changed) and not self._manual_stop:
            threading.Thread(target=self._recover, daemon=True).start()

    def _processing_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet = self.packet_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                samples = self.converter.convert(packet)
                if self._gain_factor != 1.0 and samples.size:
                    samples = np.clip(samples * self._gain_factor, -1.0, 1.0).astype(np.float32)
                if self.next_frame_time is None:
                    self.next_frame_time = float(packet.timestamp_ms)
                for data in self.frames.push(samples):
                    start = int(round(self.next_frame_time))
                    self.next_frame_time += 32.0
                    end = int(round(self.next_frame_time))
                    level = dbfs(data)
                    probability = self.vad.process(data, level)
                    frame = AudioFrame(data, 16000, start, end, level, probability)
                    activity, draft, final = self.segmenter.process(frame, self.policy.kind, self.generation)
                    self.health.processed_frames += 1
                    self.level_changed.emit(level)
                    self.probability_changed.emit(probability)
                    self.activity_changed.emit(activity)
                    if draft:
                        self.health.draft_segments += 1
                        self.draft_segment_ready.emit(draft)
                    if final:
                        self.health.final_segments += 1
                        self.segment_ready.emit(final)
            except Exception as exc:
                self.health.consecutive_errors += 1
                self.error.emit(f"音频处理异常：{exc}")

    def _publish_health(self) -> None:
        self.health.queue_depth = self.packet_queue.qsize()
        self.health.last_packet_age_ms = 0 if not self.last_packet_at else int((monotonic() - self.last_packet_at) * 1000)
        self.health_changed.emit(asdict(self.health))
        if self.running and self.last_packet_at and self.health.last_packet_age_ms > 3500 and not self._manual_stop:
            self.status_changed.emit("音频流超时，正在尝试恢复")
            threading.Thread(target=self._recover, name="audio-timeout-recovery", daemon=True).start()
