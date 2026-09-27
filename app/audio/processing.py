from __future__ import annotations

from collections import deque
from math import log10
from uuid import uuid4

import numpy as np

from app.audio.models import AudioFrame, AudioSourceKind, AudioSpeechSegment


class StreamingLinearResampler:
    """Small stateful resampler for live speech.

    It keeps fractional position and the previous source sample across packets,
    preventing the packet-boundary timing discontinuities of stateless resampling.
    """

    def __init__(self, target_rate: int = 16000) -> None:
        self.target_rate = target_rate
        self._source_rate: int | None = None
        self._position = 0.0
        self._tail = np.empty(0, dtype=np.float32)

    def reset(self) -> None:
        self._source_rate = None
        self._position = 0.0
        self._tail = np.empty(0, dtype=np.float32)

    def convert(self, packet) -> np.ndarray:
        samples = np.frombuffer(packet.pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if packet.channels > 1:
            samples = samples.reshape(-1, packet.channels).mean(axis=1)
        if packet.sample_rate == self.target_rate:
            return np.ascontiguousarray(samples, dtype=np.float32)
        if self._source_rate != packet.sample_rate:
            self.reset()
            self._source_rate = packet.sample_rate
        source = np.concatenate((self._tail, samples))
        if source.size < 2:
            self._tail = source
            return np.empty(0, dtype=np.float32)
        step = packet.sample_rate / self.target_rate
        positions = np.arange(self._position, source.size - 1, step, dtype=np.float64)
        output = np.interp(positions, np.arange(source.size), source).astype(np.float32)
        next_position = self._position + positions.size * step
        consumed = max(0, int(next_position) - 1)
        self._tail = source[consumed:]
        self._position = next_position - consumed
        return np.ascontiguousarray(np.clip(output, -1.0, 1.0), dtype=np.float32)


class FixedFrameBuffer:
    def __init__(self, size: int = 512) -> None:
        self.size = size
        self.buffer = np.empty(0, dtype=np.float32)

    def push(self, samples: np.ndarray):
        self.buffer = np.concatenate((self.buffer, samples))
        while self.buffer.size >= self.size:
            frame = self.buffer[: self.size]
            self.buffer = self.buffer[self.size :]
            yield np.ascontiguousarray(frame, dtype=np.float32)

    def clear(self) -> None:
        self.buffer = np.empty(0, dtype=np.float32)


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return -100.0 if rms <= 1e-10 else max(-100.0, 20 * log10(rms))


class SpeechSegmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size: int = 512,
        min_speech_ms: int = 180,
        min_silence_ms: int = 700,
        pre_ms: int = 180,
        post_ms: int = 250,
        max_seconds: int = 15,
        draft_interval_ms: int = 640,
    ) -> None:
        self.rate = sample_rate
        self.frame_size = frame_size
        self.frame_ms = frame_size * 1000 / sample_rate
        frames = lambda ms: max(1, round(ms / self.frame_ms))
        self.min_speech = frames(min_speech_ms)
        self.min_silence = frames(min_silence_ms)
        self.pre_count = frames(pre_ms)
        self.post_count = frames(post_ms)
        self.max_frames = max(1, int(max_seconds * sample_rate / frame_size))
        self.draft_interval_frames = frames(draft_interval_ms)
        self.reset()

    def reset(self) -> None:
        self.pre = deque(maxlen=self.pre_count)
        self.candidate: list[AudioFrame] = []
        self.frames: list[AudioFrame] = []
        self.speech_count = 0
        self.silence_count = 0
        self.active = False
        self.frames_since_draft = 0
        self.segment_id = uuid4().hex

    def process(self, frame: AudioFrame, source: AudioSourceKind, generation: int):
        probability = frame.speech_probability
        if not self.active:
            self.pre.append(frame)
            if probability >= 0.58:
                self.speech_count += 1
                self.candidate.append(frame)
                if self.speech_count >= self.min_speech:
                    self.frames = list(self.pre)
                    self.active = True
                    self.candidate.clear()
                    self.pre.clear()
                    self.silence_count = 0
                    self.frames_since_draft = 0
                    return "speech_started", None, None
            else:
                self.speech_count = 0
                self.candidate.clear()
            return ("possible_speech" if self.speech_count else "silence"), None, None

        self.frames.append(frame)
        self.frames_since_draft += 1
        self.silence_count = 0 if probability >= 0.45 else self.silence_count + 1
        draft = None
        if self.frames_since_draft >= self.draft_interval_frames:
            self.frames_since_draft = 0
            draft = self._make_segment(self.frames, source, generation, final=False)

        should_finish = len(self.frames) >= self.max_frames or self.silence_count >= max(self.min_silence, self.post_count)
        if should_finish:
            frames = self.frames
            if self.silence_count > self.post_count:
                frames = frames[: -(self.silence_count - self.post_count)]
            final = self._make_segment(frames, source, generation, final=True)
            self.reset()
            return "speech_ended", draft, final
        return "speech", draft, None

    def flush(self, source: AudioSourceKind, generation: int):
        if not self.frames:
            self.reset()
            return None
        result = self._make_segment(self.frames, source, generation, final=True)
        self.reset()
        return result

    def _make_segment(self, frames, source, generation, final):
        if not frames:
            return None
        result = AudioSpeechSegment(
            self.segment_id,
            np.concatenate([item.samples for item in frames]).astype(np.float32),
            self.rate,
            frames[0].start_ms,
            frames[-1].end_ms,
            source,
            generation,
        )
        result.is_final = final
        return result
