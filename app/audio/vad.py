from __future__ import annotations

import numpy as np

# Silero VAD @ 16 kHz 接受固定 512 样本窗口（32 ms）
SILERO_WINDOW = 512
SILERO_STATE_SHAPE = (2, 1, 128)


def _default_state() -> np.ndarray:
    return np.zeros(SILERO_STATE_SHAPE, dtype=np.float32)


class EnergyVADEngine:
    name = "energy_fallback"

    def __init__(self, threshold_db=-46.0):
        self.threshold_db = threshold_db

    def initialize(self):
        return None

    def reset(self):
        return None

    def process(self, samples, level_db):
        probability = max(0.0, min(1.0, (level_db + 64.0) / 28.0))
        return probability

    def close(self):
        return None


class SileroVADEngine:
    """Silero VAD running on onnxruntime (no torch dependency).

    Drop-in replacement for the torch-backed engine: identical constructor,
    identical `process()` contract (512 samples @ 16 kHz -> speech probability
    in [0, 1]) and identical stateful behaviour, so callers are unaffected.
    """

    name = "silero_onnx"

    def __init__(self, threshold=.55, model_path=None):
        self.threshold = threshold
        self.model_path = model_path
        self.session = None
        self._state = _default_state()
        self._sr = np.array(16000, dtype=np.int64)

    def initialize(self):
        from onnxruntime import InferenceSession, SessionOptions

        from app.core import assets

        path = self.model_path or assets.silero_vad_path()
        if not path:
            raise RuntimeError("未找到 silero_vad.onnx，请先下载 VAD 模型")
        options = SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        self.reset()
        return None

    def reset(self):
        self._state = _default_state()
        return None

    def process(self, samples, level_db):
        if self.session is None:
            raise RuntimeError("Silero VAD 未初始化")
        if samples.size != SILERO_WINDOW:
            raise ValueError("Silero VAD requires 512 samples at 16 kHz")
        window = np.ascontiguousarray(samples, dtype=np.float32).reshape(1, SILERO_WINDOW)
        output, state = self.session.run(
            None, {"input": window, "state": self._state, "sr": self._sr}
        )
        self._state = np.asarray(state, dtype=np.float32).reshape(SILERO_STATE_SHAPE)
        return float(np.asarray(output, dtype=np.float32).reshape(-1)[0])

    def close(self):
        self.reset()
        self.session = None
        return None


def create_vad(prefer_silero=True):
    if prefer_silero:
        try:
            engine = SileroVADEngine()
            engine.initialize()
            return engine
        except Exception:
            pass
    engine = EnergyVADEngine()
    engine.initialize()
    return engine
