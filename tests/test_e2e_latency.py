"""Tests for the P0 end-to-end latency measurement module.

These verify the non-intrusive, pure-logic parts (statistics, config, pixel
diff) plus a real bus/pipeline integration run in proxy (no-pixel) mode so the
whole translation chain is exercised without needing a live display.
"""

import asyncio
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from tests.e2e_latency import (  # noqa: E402
    LatencyConfig,
    WindowPixelProbe,
    compute_stats,
    percentile,
)
from tests.e2e_latency import build_harness, E2ELatencyTester  # noqa: E402


# --------------------------------------------------------------------------- #
# Statistics                                                                  #
# --------------------------------------------------------------------------- #
def test_percentile_linear():
    vals = [10, 20, 30, 40, 50]
    assert percentile(vals, 50) == 30
    assert percentile(vals, 95) == 48.0
    assert round(percentile(vals, 99), 4) == 49.6
    assert percentile([], 50) == 0.0
    assert percentile([7], 95) == 7.0


def test_compute_stats_basic():
    vals = [10, 20, 30, 40, 50]
    stats = compute_stats(vals)
    assert stats["count"] == 5
    assert stats["mean"] == 30.0
    assert stats["min"] == 10
    assert stats["max"] == 50
    assert stats["p50"] == 30
    assert stats["p95"] == 48.0
    assert round(stats["p99"], 4) == 49.6


def test_compute_stats_empty_and_single():
    empty = compute_stats([])
    assert empty["count"] == 0
    assert empty["mean"] == 0.0
    single = compute_stats([42.0])
    assert single["count"] == 1
    assert single["mean"] == 42.0
    assert single["p99"] == 42.0


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #
def test_config_defaults_and_overrides(tmp_path):
    cfg = LatencyConfig.load(None)
    assert cfg.iterations == 10
    assert cfg.scenarios == ["basic", "style", "state", "load"]
    assert cfg.thresholds["e2e_ms"] == 500.0

    cfg2 = cfg.apply_overrides(iterations=5, pixel=False, scenarios=["basic"])
    assert cfg2.iterations == 5
    assert cfg2.pixel_enabled is False
    assert cfg2.scenarios == ["basic"]


def test_config_load_merges_partial(tmp_path):
    payload = {
        "iterations": 7,
        "thresholds": {"e2e_ms": 250.0},  # partial: other thresholds kept
        "texts": {"short": "hi"},          # partial: "long" kept from default
    }
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    cfg = LatencyConfig.load(str(path))
    assert cfg.iterations == 7
    assert cfg.thresholds["e2e_ms"] == 250.0
    assert cfg.thresholds["translation_ms"] == 300.0  # default retained
    assert cfg.texts["short"] == "hi"
    assert "long" in cfg.texts  # default retained


# --------------------------------------------------------------------------- #
# Pixel probe diff (synthetic buffers, no window required)                     #
# --------------------------------------------------------------------------- #
def test_pixel_probe_diff_identical_and_changed():
    probe = WindowPixelProbe(0)
    probe._w = 4
    probe._h = 2
    same = bytes(4 * 2 * 4)
    assert probe.diff(same, same) == 0.0
    changed = bytes([255]) * (4 * 2 * 4)
    assert probe.diff(same, changed) == 1.0
    # None inputs are treated as "unavailable" -> maximal difference.
    assert probe.diff(None, same) == 1.0


def test_pixel_probe_capture_graceful_without_window():
    probe = WindowPixelProbe(0)
    # Without a real window the capture must fail gracefully (None), never raise.
    assert probe.capture() is None


# --------------------------------------------------------------------------- #
# Integration: real bus + pipeline + mock translation (proxy / no-pixel)       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def harness():
    return build_harness(include_overlay=False, db_path=None)


def test_harness_preset_application(harness):
    harness.preset_manager.apply("cinema")
    assert harness.style_manager.get_style().subtitle_font_size == 20
    assert harness.preset_manager.current() == "cinema"


def test_integration_measurement_chain(harness):
    cfg = LatencyConfig.load(None)
    cfg.pixel_enabled = False
    cfg.iterations = 1
    tester = E2ELatencyTester(harness, cfg, pixel=False)

    async def run():
        samples = []
        for i in range(3):
            s = await tester.measure_once(
                "basic", f"basic.steady.{i}", i,
                f"Distinct sentence number {i} for latency testing.",
                recognition_latency_ms=0.0,
            )
            samples.append(s)
        return samples

    samples = asyncio.run(run())
    assert len(samples) == 3
    for s in samples:
        # translation actually ran (mock sleeps ~160ms) -> measurable.
        assert s.translation > 0.0, "translation segment should be recorded"
        assert s.e2e > 0.0
        assert s.render == 0.0  # proxy mode: render == subtitle emit time
        assert s.timeout is False
        assert s.discarded is False

    # statistics over the collected samples must be computable.
    stats = compute_stats([s.e2e for s in samples])
    assert stats["count"] == 3
    assert stats["mean"] > 0.0


def test_integration_report_build(harness):
    cfg = LatencyConfig.load(None)
    cfg.pixel_enabled = False
    tester = E2ELatencyTester(harness, cfg, pixel=False)

    async def run():
        await tester.run(["basic"])

    asyncio.run(run())
    report = tester.build_report()
    assert report["tool"] == "e2e_latency"
    assert "basic" in report["scenarios"]
    assert report["pixel_probe_active"] is False
    # basic scenario produced at least a cold-start + steady samples.
    assert report["scenarios"]["basic"]["summary"]["samples"] >= 1


def test_real_provider_build_failure_is_graceful(harness):
    """An unknown provider id must not crash the harness; it is skipped."""
    cfg = LatencyConfig.load(None)
    cfg.pixel_enabled = False
    tester = E2ELatencyTester(harness, cfg, pixel=False)

    async def run():
        return await tester._apply_provider_spec({"provider_id": "does_not_exist"})

    assert asyncio.run(run()) is None


def test_real_provider_error_flagged(harness):
    """A real provider built via the app's registry must surface translation
    failures as a timeout anomaly with the underlying error recorded (no crash)."""
    cfg = LatencyConfig.load(None)
    cfg.pixel_enabled = False
    cfg.render_timeout_ms = 800  # keep the test fast; the error is injected
    tester = E2ELatencyTester(harness, cfg, pixel=False)

    spec = {
        "name": "ollama-bogus",
        "provider_id": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:0.5b",
    }

    async def run():
        label = await tester._apply_provider_spec(spec)
        assert label == "ollama-bogus"
        tester._current_provider_name = label
        # Inject a deterministic failure into the real provider instance so the
        # test does not depend on a live (or refusing) network endpoint.
        provider = tester.harness.translation.chain[0]

        async def boom(_job):
            raise RuntimeError("injected translation failure")

        provider.translate = boom
        sample = await tester.measure_once("load", "load.short", 0, "hi", 0.0)
        return sample

    sample = asyncio.run(run())
    assert sample.timeout is True
    assert sample.anomaly is True
    assert "translation_error" in sample.reason
