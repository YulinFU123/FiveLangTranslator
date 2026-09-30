import numpy as np
import pytest

from app.audio import vad
from app.core import paths


class FakeSession:
    """Minimal stand-in for onnxruntime.InferenceSession."""

    def __init__(self, probability=0.42):
        self.probability = probability
        self.feeds = []

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        state = np.asarray(feeds["state"], dtype=np.float32) + 1.0
        return np.array([[self.probability]], dtype=np.float32), state


def _window(value=0.0):
    return np.full(vad.SILERO_WINDOW, value, dtype=np.float32)


def test_silero_returns_probability_and_advances_state():
    engine = vad.SileroVADEngine()
    engine.session = FakeSession(probability=0.73)

    probability = engine.process(_window(), -30.0)

    assert isinstance(probability, float)
    assert probability == pytest.approx(0.73)
    # state flows through: zeros -> ones after the first call
    assert engine._state.shape == vad.SILERO_STATE_SHAPE
    assert float(engine._state.min()) == 1.0


def test_silero_feeds_expected_inputs():
    engine = vad.SileroVADEngine()
    session = FakeSession()
    engine.session = session

    engine.process(_window(0.5), -20.0)

    feeds = session.feeds[0]
    assert set(feeds) == {"input", "state", "sr"}
    # Silero needs 64 samples of context + the 512 new samples = 576. Feeding
    # only 512 made the model output ~0 for every input (no speech ever seen).
    assert feeds["input"].shape == (1, vad.SILERO_WINDOW + vad.SILERO_CONTEXT)
    assert feeds["input"].dtype == np.float32
    assert int(np.asarray(feeds["sr"]).reshape(-1)[0]) == 16000


def test_silero_carries_context_between_frames():
    engine = vad.SileroVADEngine()
    session = FakeSession()
    engine.session = session

    engine.process(_window(0.0), -20.0)
    engine.process(_window(1.0), -20.0)

    second = session.feeds[1]["input"].reshape(-1)
    assert second.shape[0] == vad.SILERO_WINDOW + vad.SILERO_CONTEXT
    assert np.all(second[: vad.SILERO_CONTEXT] == 0.0)
    assert np.all(second[vad.SILERO_CONTEXT:] == 1.0)


def test_silero_rejects_wrong_window_size():
    engine = vad.SileroVADEngine()
    engine.session = FakeSession()
    with pytest.raises(ValueError):
        engine.process(np.zeros(256, dtype=np.float32), -20.0)


def test_silero_reset_clears_state():
    engine = vad.SileroVADEngine()
    engine.session = FakeSession()
    engine.process(_window(), -20.0)
    assert float(engine._state.min()) == 1.0

    engine.reset()
    assert float(engine._state.min()) == 0.0
    assert float(engine._state.max()) == 0.0


def test_silero_requires_initialisation():
    engine = vad.SileroVADEngine()
    with pytest.raises(RuntimeError):
        engine.process(_window(), -20.0)


def test_create_vad_falls_back_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    engine = vad.create_vad(prefer_silero=True)
    assert engine.name == "energy_fallback"


def test_create_vad_honours_prefer_silero_false():
    engine = vad.create_vad(prefer_silero=False)
    assert engine.name == "energy_fallback"


def test_energy_engine_matches_legacy_formula():
    engine = vad.EnergyVADEngine()
    engine.initialize()
    # legacy: clamp((level_db + 64) / 28, 0, 1)
    assert engine.process(_window(), -64.0) == pytest.approx(0.0)
    assert engine.process(_window(), -36.0) == pytest.approx(1.0)
    assert engine.process(_window(), -50.0) == pytest.approx(14.0 / 28.0)
