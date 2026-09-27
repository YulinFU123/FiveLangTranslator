import json
from app.asr.benchmark import BenchmarkResult, save_benchmark


def test_benchmark_report_is_persisted(tmp_path):
    result = BenchmarkResult(
        "2026-01-01T00:00:00+00:00", "test.wav", 10.0, 2500,
        .25, "en", "hello", "whisper_cpp", True, "model.bin",
    )
    path = save_benchmark(result, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["realtime_factor"] == .25
    assert payload["gpu_requested"] is True
