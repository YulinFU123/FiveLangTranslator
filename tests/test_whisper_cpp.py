import json
import wave
import numpy as np

from app.asr.whisper_cpp import _parse_console, _parse_json, _write_pcm16_wav


def test_json_output_parsing(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({
        "result": {"language": "ja"},
        "transcription": [{"text": "こんにちは"}, {"text": "世界"}],
    }, ensure_ascii=False), encoding="utf-8")
    text, language = _parse_json(path)
    assert text == "こんにちは 世界"
    assert language == "ja"


def test_console_output_parsing():
    output = "[00:00:00.000 --> 00:00:01.000] Hello\nmain: ignored\n"
    assert _parse_console(output) == "Hello"


def test_wav_writer(tmp_path):
    path = tmp_path / "audio.wav"
    _write_pcm16_wav(path, np.zeros(16000, dtype=np.float32), 16000)
    with wave.open(str(path), "rb") as stream:
        assert stream.getframerate() == 16000
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2

from app.asr.whisper_cpp import _parse_json_detailed


def test_detailed_json_extracts_confidence(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({
        "result": {"language": "en"},
        "transcription": [
            {"text": "hello", "confidence": .8, "no_speech_prob": .1},
            {"text": "world", "confidence": .6, "no_speech_prob": .3},
        ],
    }), encoding="utf-8")
    text, language, metadata = _parse_json_detailed(path)
    assert text == "hello world"
    assert language == "en"
    assert metadata["confidence"] == .7
    assert metadata["no_speech_probability"] == .2
