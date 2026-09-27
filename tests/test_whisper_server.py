import asyncio
import json
from pathlib import Path

from app.asr.whisper_server import (
    WhisperServerConfig,
    WhisperServerProcess,
    _parse_server_payload,
)


def test_server_payload_text_format():
    text, language, metadata = _parse_server_payload(
        {"text": "hello world", "language": "en", "confidence": .9},
        "auto",
    )
    assert text == "hello world"
    assert language == "en"
    assert metadata["confidence"] == .9


def test_server_payload_transcription_format():
    text, language, _ = _parse_server_payload(
        {"transcription": [{"text": "你"}, {"text": "好"}]}, "zh"
    )
    assert text == "你 好"
    assert language == "zh"


def test_server_config_url():
    config = WhisperServerConfig(Path("server.exe"), Path("model.bin"), port=9000)
    assert config.base_url == "http://127.0.0.1:9000"


def test_missing_server_binary_fails_cleanly(tmp_path):
    config = WhisperServerConfig(tmp_path / "missing.exe", tmp_path / "model.bin")
    process = WhisperServerProcess(config)
    async def scenario():
        try:
            await process.start()
        except RuntimeError as exc:
            assert "whisper-server" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
    asyncio.run(scenario())
