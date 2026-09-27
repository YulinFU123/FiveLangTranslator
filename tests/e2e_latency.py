"""End-to-end subtitle latency measurement harness (P0 quality infrastructure).

Pure Windows-native implementation that does NOT modify any business code. It
reuses the existing translation chain (`EventBus`, `SubtitlePipelineController`,
`TranslationService`) and the subtitle rendering module (`OverlayWindow`) through
their public interfaces only:

* Segment instrumentation is done by *subscribing* to the shared event bus
  (`recognition` / `subtitle`) and to the `TranslationService.translated`
  signal -- the genuine "recognition done / translation done / render start"
  moments in the real pipeline.
* End-to-end completion is verified by a real pixel probe: the floating subtitle
  window's client area is captured with the Windows GDI (`PrintWindow` /
  `GetDIBits`) and a change is detected once the new text has actually been
  painted. This is the user-perceivable "pixel rendered" point.
* All timestamps use `QueryPerformanceCounter` (≤ 1 ms precision on Windows).

The harness is fully decoupled: it lives under ``tests/``, can be launched
stand-alone (``python tests/e2e_latency.py``) or imported. No application source
file is touched.

Scenario coverage (spec 1.2)
----------------------------
* ``basic`` : cold-start first translation + steady-state repeats.
* ``style`` : cinema / meeting / reading preset render-time differences.
* ``state`` : topmost on/off and visible/hidden background states.
* ``load``  : short vs. long source text.

Outputs (spec 1.3)
------------------
* Live console progress (current item, single-shot latency, progress).
* Structured JSON report + per-sample CSV report.
* Bottleneck segment share analysis and threshold-based anomaly flagging.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import ctypes
import json
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

try:  # provider result shape lives with the translation models
    from app.translation.models import ProviderResult
except Exception:  # pragma: no cover - import guard
    class ProviderResult:  # minimal fallback so the module still imports
        def __init__(self, text="", provider="", latency_ms=0, model="", metadata=None):
            self.text = text
            self.provider = provider
            self.latency_ms = latency_ms
            self.model = model
            self.metadata = metadata or {}

__version__ = "1.0.0"

LONG_TEXT = (
    "In a quiet town nestled between rolling hills and a wide, silver river, "
    "the clockmaker spent his evenings polishing the small brass gears that "
    "kept the whole village on time, and nobody ever noticed how much depended "
    "on the patience of one solitary man who simply loved the sound of order."
)


class _FakeTranslationProvider:
    """In-process translation double that mimics a real backend's latency.

    Returns the same ``ProviderResult`` shape the production providers do so the
    unmodified ``TranslationService`` pipeline is exercised end-to-end. It is part
    of the test harness only -- no application source is touched.
    """

    provider_id = "fake_latency"

    def __init__(self) -> None:
        from types import SimpleNamespace
        self.config = SimpleNamespace(model="fake")

    async def initialize(self):
        return None

    async def translate(self, job):
        started = time.perf_counter()
        await asyncio.sleep(0.16)  # emulate engine round-trip latency
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProviderResult(
            text=f"[译] {job.text}",
            provider=self.provider_id,
            latency_ms=latency_ms,
            model="fake",
        )


# --------------------------------------------------------------------------- #
# High-resolution timer (QueryPerformanceCounter on Windows)                   #
# --------------------------------------------------------------------------- #
class WindowsHighResTimer:
    """Monotonic timer backed by ``QueryPerformanceCounter``.

    Falls back to ``time.perf_counter`` (which itself rides on QPC on Windows)
    when the native API is unavailable, so the module still imports elsewhere.
    """

    def __init__(self) -> None:
        self.frequency: float = 0.0
        self.native = False
        if platform.system() == "Windows":
            try:
                freq = ctypes.c_int64()
                if ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq)):
                    self.frequency = float(freq.value)
                    self.native = self.frequency > 0
            except Exception:  # pragma: no cover - non-Windows / sandbox
                self.native = False

    def now(self) -> float:
        """Current time in seconds (float, monotonic)."""
        if self.native:
            cnt = ctypes.c_int64()
            ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(cnt))
            return cnt.value / self.frequency
        return time.perf_counter()


# --------------------------------------------------------------------------- #
# Windows pixel probe (GDI capture + perceptual diff)                          #
# --------------------------------------------------------------------------- #
def _bitmapinfo(width: int, height: int):
    """Minimal BITMAPINFO (header + 3 x RGB quad) for a 32bpp capture."""
    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
            ("bmiColors", ctypes.c_uint8 * 4),  # single placeholder quad
        ]
    info = BITMAPINFO()
    info.biSize = ctypes.sizeof(BITMAPINFO) - ctypes.sizeof(ctypes.c_uint8 * 4)
    info.biWidth = width
    info.biHeight = -abs(height)  # top-down DIB
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = 0  # BI_RGB
    return info


class WindowPixelProbe:
    """Captures a window's client pixels via GDI and detects visual changes.

    Only instantiated on Windows with a valid ``hwnd``. ``capture`` returns the
    raw RGBA buffer (or ``None`` on any failure) and records the captured size.
    ``diff`` returns a normalised [0, 1] fraction of changed luminance so callers
    can tell "nothing changed" from "subtitle repainted".
    """

    PW_RENDERFULLCONTENT = 0x00000002
    SRCCOPY = 0x00CC0020

    def __init__(self, hwnd: int) -> None:
        self.hwnd = int(hwnd)
        self.available = False
        self._w = 0
        self._h = 0
        if platform.system() != "Windows" or self.hwnd == 0:
            return
        try:
            self.user32 = ctypes.windll.user32
            self.gdi32 = ctypes.windll.gdi32
            self.available = True
        except Exception:  # pragma: no cover - non-Windows / sandbox
            self.available = False

    def capture(self) -> Optional[bytes]:
        if not self.available:
            return None
        rect = ctypes.wintypes.RECT()
        if not self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None
        self._w, self._h = w, h
        hdc_src = self.user32.GetWindowDC(self.hwnd)
        if not hdc_src:
            return None
        try:
            hdc_mem = self.gdi32.CreateCompatibleDC(hdc_src)
            if not hdc_mem:
                return None
            bmp = self.gdi32.CreateCompatibleBitmap(hdc_src, w, h)
            old = self.gdi32.SelectObject(hdc_mem, bmp)
            ok = self.user32.PrintWindow(self.hwnd, hdc_mem, self.PW_RENDERFULLCONTENT)
            if not ok:
                self.gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_src, 0, 0, self.SRCCOPY)
            info = _bitmapinfo(w, h)
            buf = ctypes.create_string_buffer(w * h * 4)
            got = self.gdi32.GetDIBits(
                hdc_mem, bmp, 0, h, buf, ctypes.byref(info), 0
            )
            self.gdi32.SelectObject(hdc_mem, old)
            self.gdi32.DeleteObject(bmp)
            self.gdi32.DeleteDC(hdc_mem)
            if not got:
                return None
            return bytes(buf.raw if hasattr(buf, "raw") else buf)
        finally:
            self.user32.ReleaseDC(self.hwnd, hdc_src)

    def diff(self, a: Optional[bytes], b: Optional[bytes]) -> float:
        """Normalised mean absolute RGB difference between two captures."""
        if a is None or b is None or self._w <= 0 or self._h <= 0:
            return 1.0
        stride = (self._w * 4 + 3) & ~3
        total = 0
        n = 0
        for y in range(self._h):
            ra = a[y * stride:y * stride + self._w * 4]
            rb = b[y * stride:y * stride + self._w * 4]
            for x in range(self._w):
                i = x * 4
                total += abs(ra[i] - rb[i]) + abs(ra[i + 1] - rb[i + 1]) + abs(ra[i + 2] - rb[i + 2])
                n += 1
        if n == 0:
            return 0.0
        return total / (n * 3 * 255.0)


# --------------------------------------------------------------------------- #
# Statistics                                                                  #
# --------------------------------------------------------------------------- #
def percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method).

    ``sorted_vals`` MUST be sorted ascending. Returns 0.0 for empty input.
    """
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = (p / 100.0) * (n - 1)
    lo = int(rank // 1)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def compute_stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0,
                "p50": 0.0, "p95": 0.0, "p99": 0.0}
    s = sorted(vals)
    n = len(s)
    return {
        "count": n,
        "mean": sum(s) / n,
        "min": s[0],
        "max": s[-1],
        "p50": percentile(s, 50),
        "p95": percentile(s, 95),
        "p99": percentile(s, 99),
    }


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #
_CONFIG_DEFAULTS: dict[str, Any] = {
    "iterations": 10,
    "warmup": 2,
    "recognition_latency_ms": 0.0,
    "poll_interval_ms": 8.0,
    "settle_ms": 50.0,
    "render_timeout_ms": 2000.0,
    "pixel_diff_threshold": 0.01,
    "pixel_match_threshold": 0.002,
    "discard_above_ms": 5000.0,
    "pixel_enabled": True,
    "vary_text_per_iteration": True,
    "language_pair": ["zh", "cinema"],
    "concurrency": 1,
    "inflight_dedup": True,
    "providers": [],
    "scenarios": ["basic", "style", "state", "load"],
    "texts": {"short": "Hello world.", "long": LONG_TEXT},
    "thresholds": {
        "e2e_ms": 500.0,
        "recognition_ms": 50.0,
        "translation_ms": 300.0,
        "render_ms": 100.0,
    },
}


@dataclass
class LatencyConfig:
    iterations: int = 10
    warmup: int = 2
    recognition_latency_ms: float = 0.0
    poll_interval_ms: float = 8.0
    settle_ms: float = 50.0
    render_timeout_ms: float = 2000.0
    pixel_diff_threshold: float = 0.01
    pixel_match_threshold: float = 0.002
    discard_above_ms: float = 5000.0
    pixel_enabled: bool = True
    vary_text_per_iteration: bool = True
    language_pair: list = field(default_factory=lambda: ["zh", "cinema"])
    concurrency: int = 1
    inflight_dedup: bool = True
    providers: list = field(default_factory=list)
    scenarios: list = field(default_factory=lambda: ["basic", "style", "state", "load"])
    texts: dict = field(default_factory=lambda: {"short": "Hello world.", "long": LONG_TEXT})
    thresholds: dict = field(default_factory=lambda: {
        "e2e_ms": 500.0, "recognition_ms": 50.0,
        "translation_ms": 300.0, "render_ms": 100.0,
    })

    @classmethod
    def load(cls, path: Optional[str]) -> "LatencyConfig":
        import copy
        data: dict[str, Any] = copy.deepcopy(_CONFIG_DEFAULTS)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    loaded = json.load(fh)
                for key, value in loaded.items():
                    if key not in data:
                        continue
                    if isinstance(value, dict) and isinstance(data[key], dict):
                        merged = dict(data[key])
                        merged.update(value)
                        data[key] = merged
                    else:
                        data[key] = value
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[warn] 无法解析配置文件 {path}: {exc}，使用默认配置", file=sys.stderr)
        return cls(**data)

    def apply_overrides(self, iterations: Optional[int] = None,
                        pixel: Optional[bool] = None,
                        scenarios: Optional[list] = None,
                        render_timeout: Optional[float] = None,
                        concurrency: Optional[int] = None,
                        inflight_dedup: Optional[bool] = None) -> "LatencyConfig":
        if iterations is not None:
            self.iterations = max(1, int(iterations))
        if pixel is not None:
            self.pixel_enabled = pixel
        if scenarios:
            self.scenarios = [s for s in scenarios if s in _CONFIG_DEFAULTS["scenarios"]]
        if render_timeout is not None:
            self.render_timeout_ms = float(render_timeout)
        if concurrency is not None:
            self.concurrency = max(1, int(concurrency))
        if inflight_dedup is not None:
            self.inflight_dedup = bool(inflight_dedup)
        return self

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Sample                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class LatencySample:
    group: str
    tag: str
    iteration: int
    provider: str = "fake"
    e2e: float = 0.0
    recognition: float = 0.0
    translation: float = 0.0
    pipeline: float = 0.0
    render: float = 0.0
    warmup: bool = False
    anomaly: bool = False
    discarded: bool = False
    timeout: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Harness (minimal, public-interface-only wiring)                              #
# --------------------------------------------------------------------------- #
class Harness:
    def __init__(self, bus, pipeline, translation, overlay, preset_manager,
                 style_manager, anchor_manager, topmost_manager, config_repo):
        self.bus = bus
        self.pipeline = pipeline
        self.translation = translation
        self.overlay = overlay
        self.preset_manager = preset_manager
        self.style_manager = style_manager
        self.anchor_manager = anchor_manager
        self.topmost_manager = topmost_manager
        self.config_repo = config_repo


def build_harness(include_overlay: bool = True, db_path: Optional[str] = None) -> Harness:
    """Builds the minimal translation-chain wiring needed for latency testing.

    Reuses existing classes through their public constructors/interfaces only --
    no business source is modified. ``include_overlay`` controls whether the real
    floating subtitle window is created (required for the GDI pixel probe on a
    live display); when False the tester falls back to subtitle-signal proxying.
    """
    from pathlib import Path
    import tempfile

    from app.core.events import EventBus
    from app.core.arbiter import ResultArbiter
    from app.controllers import SubtitlePipelineController
    from app.translation.service import TranslationService
    from app.storage.database import Database
    from app.storage.appearance import AppearanceRepository
    from app.storage.config_store import ConfigRepository
    from app.ui.overlay.style_manager import SubtitleStyleManager
    from app.ui.overlay.anchor import SubtitleAnchorManager
    from app.ui.overlay.topmost import WindowTopmostManager
    from app.ui.overlay.preset_manager import SubtitlePresetManager

    bus = EventBus()
    arbiter = ResultArbiter()
    if db_path is None:
        db_path = str(Path(tempfile.mkdtemp()) / "latency.db")
    db = Database(db_path)
    appearance_repo = AppearanceRepository(db)
    config_repo = ConfigRepository(db)

    style_manager = SubtitleStyleManager(bus, appearance_repo)
    anchor_manager = SubtitleAnchorManager(bus, config_repo)
    topmost_manager = WindowTopmostManager(bus, config_repo)

    translation = TranslationService()
    # Test-double provider standing in for a real translation backend. It returns
    # the same ``ProviderResult`` shape the real providers do and emulates a
    # ~160ms engine latency. Harness-only; no business code is modified.
    translation.set_providers([_FakeTranslationProvider()])
    translation.set_language_pair("zh", "cinema")

    pipeline = SubtitlePipelineController(bus, arbiter, translation)

    overlay = None
    if include_overlay:
        from app.settings import Store
        from app.ui.overlay.window import OverlayWindow
        settings = Store().load()
        overlay = OverlayWindow(settings, style_manager=style_manager, bus=bus)
        bus.subtitle.connect(overlay.update_subtitle)
        overlay.show()
        overlay.set_visible(True)

    preset_manager = SubtitlePresetManager(
        bus, style_manager, anchor_manager, topmost_manager, config_repo, overlay
    )
    return Harness(bus, pipeline, translation, overlay, preset_manager,
                   style_manager, anchor_manager, topmost_manager, config_repo)


# --------------------------------------------------------------------------- #
# E2E latency tester                                                          #
# --------------------------------------------------------------------------- #
class E2ELatencyTester:
    SEGMENTS = ("recognition", "translation", "pipeline", "render")

    def __init__(self, harness: Harness, config: LatencyConfig,
                 pixel: bool = True) -> None:
        self.harness = harness
        self.config = config
        self.timer = WindowsHighResTimer()
        self.pixel_mode = False
        self.probe: Optional[WindowPixelProbe] = None
        if pixel and config.pixel_enabled and harness.overlay is not None:
            try:
                probe = WindowPixelProbe(int(harness.overlay.winId()))
                if probe.available:
                    self.probe = probe
                    self.pixel_mode = True
            except Exception:  # pragma: no cover - display absent
                self.probe = None
        self._samples: list[LatencySample] = []
        self._pending: dict[str, dict] = {}
        self._last_error: Optional[str] = None
        self._last_error_time: Optional[float] = None
        self._current_provider_name: str = "fake"
        self._unavailable: list[str] = []
        self._connect()

    # -- wiring --------------------------------------------------------------
    def _connect(self) -> None:
        self.harness.bus.recognition.connect(self._on_recognition)
        self.harness.translation.translated.connect(self._on_translation)
        self.harness.bus.subtitle.connect(self._on_subtitle)
        self.harness.translation.error.connect(self._on_error)

    def _on_recognition(self, result) -> None:
        entry = self._pending.get(result.segment_id)
        if entry is not None:
            entry["marks"]["recognition"] = self.timer.now()

    def _on_translation(self, result) -> None:
        entry = self._pending.get(result.segment_id)
        if entry is None:
            return
        if entry["marks"].get("translation") is None:
            entry["marks"]["translation"] = self.timer.now()
        entry["translation_done"] = True
        entry["translation_event"].set()

    def _on_subtitle(self, vm) -> None:
        # Only the final (post-translation) frame carries a non-empty translation;
        # the intermediate "翻译中…" frames emitted before translation finishes
        # have empty text and are ignored, so the timeline ends at the real
        # render start regardless of signal connection order.
        if not getattr(vm, "translated_text", ""):
            return
        entry = self._pending.get(vm.segment_id)
        if entry is None:
            return
        if entry["marks"].get("render_start") is None:
            entry["marks"]["render_start"] = self.timer.now()
        if not self.pixel_mode:
            entry["marks"]["render_complete"] = entry["marks"]["render_start"]
            entry["render_event"].set()

    def _on_error(self, message: str) -> None:
        self._last_error = message
        self._last_error_time = self.timer.now()

    # -- measurement ---------------------------------------------------------
    def _make_result(self, segment_id: str, text: str):
        from app.core.models import RecognitionResult, SubtitleStatus, SourceType
        return RecognitionResult(
            segment_id=segment_id, revision=1, text=text, language="en",
            confidence=0.95, start_ms=0, end_ms=0, status=SubtitleStatus.FINAL,
            provider="mock_asr", source=SourceType.SYSTEM_AUDIO,
            metadata={"stable_source_text": text, "draft_source_text": ""},
        )

    async def _poll_render(self, entry: dict) -> bool:
        """Wait until the final subtitle frame is actually painted (pixel match)."""
        loop = asyncio.get_event_loop()
        await asyncio.sleep(self.config.settle_ms / 1000.0)
        target = self.probe.capture() if self.probe else None
        deadline = loop.time() + self.config.render_timeout_ms / 1000.0
        interval = max(0.001, self.config.poll_interval_ms / 1000.0)
        while loop.time() < deadline:
            await asyncio.sleep(interval)
            cur = self.probe.capture() if self.probe else None
            if target is not None and cur is not None and \
                    self.probe.diff(cur, target) <= self.config.pixel_match_threshold:
                entry["marks"]["render_complete"] = self.timer.now()
                entry["render_event"].set()
                return True
        entry["marks"]["render_complete"] = self.timer.now()
        entry["render_event"].set()
        return False

    async def measure_once(self, group: str, tag: str, iteration: int,
                          text: str, recognition_latency_ms: float) -> LatencySample:
        segment_id = f"seg-{uuid.uuid4().hex}"
        entry: dict = {
            "marks": {},
            "translation_done": False,
            "translation_event": asyncio.Event(),
            "render_event": asyncio.Event(),
        }
        self._pending[segment_id] = entry
        self._last_error = None

        pixel_active = self.pixel_mode and (
            self.harness.overlay is None or self.harness.overlay.isVisible()
        )
        t0 = self.timer.now()
        if recognition_latency_ms:
            await asyncio.sleep(recognition_latency_ms / 1000.0)

        # Each measurement gets a unique source string so the translation cache
        # cannot mask the real engine latency (set vary_text_per_iteration=False
        # to reproduce cache-dominated steady-state instead).
        if self.config.vary_text_per_iteration:
            text = f"{text} ·{tag}#{iteration}"
        result = self._make_result(segment_id, text)
        # "recognition done" point, then hand off to the pipeline.
        self.harness.bus.recognition.emit(result)
        self.harness.pipeline.submit_recognition(result)

        timeout = False
        try:
            await asyncio.wait_for(
                entry["translation_event"].wait(),
                self.config.render_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            timeout = True

        if pixel_active:
            detected = await self._poll_render(entry)
            if not detected:
                timeout = True
        else:
            if not timeout:
                try:
                    await asyncio.wait_for(
                        entry["render_event"].wait(),
                        self.config.render_timeout_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    timeout = True

        self._pending.pop(segment_id, None)

        m = entry["marks"]
        t_input = t0
        t_rec = m.get("recognition", t_input)
        t_trans = m.get("translation", t_rec)
        t_rstart = m.get("render_start", t_trans)
        t_rdone = m.get("render_complete", self.timer.now())

        e2e = (t_rdone - t_input) * 1000.0
        recognition = (t_rec - t_input) * 1000.0
        translation = (t_trans - t_rec) * 1000.0
        pipeline = (t_rstart - t_trans) * 1000.0
        render = (t_rdone - t_rstart) * 1000.0

        sample = LatencySample(
            group=group, tag=tag, iteration=iteration,
            provider=self._current_provider_name,
            e2e=e2e, recognition=recognition, translation=translation,
            pipeline=pipeline, render=render, timeout=timeout,
        )
        self._classify(sample, t_input)
        return sample

    def _classify(self, sample: LatencySample, t_input: float = 0.0) -> None:
        thr = self.config.thresholds
        reasons = []
        if sample.timeout:
            reasons.append("render_timeout")
            # If the provider reported an error during this window, surface it.
            if self._last_error and (self._last_error_time or 0) >= t_input:
                reasons.append(f"translation_error:{self._last_error}")
        if sample.e2e > thr.get("e2e_ms", 1e9):
            reasons.append("e2e_exceed")
        if sample.recognition > thr.get("recognition_ms", 1e9):
            reasons.append("recognition_exceed")
        if sample.translation > thr.get("translation_ms", 1e9):
            reasons.append("translation_exceed")
        if sample.render > thr.get("render_ms", 1e9):
            reasons.append("render_exceed")
        sample.anomaly = bool(reasons)
        sample.reason = ";".join(reasons)
        # Hard outliers / failed measurements are discarded from statistics.
        sample.discarded = sample.timeout or sample.e2e > self.config.discard_above_ms

    # -- scenario runners ----------------------------------------------------
    async def _run_basic(self) -> None:
        texts = self.config.texts
        base = texts.get("short", "Hello world.")
        n = self.config.iterations
        # cold start: first ever translation on a cold cache.
        s = await self.measure_once("basic", "basic.cold", 0, base,
                                    self.config.recognition_latency_ms)
        self._record(s, warmup=False)
        for i in range(n):
            s = await self.measure_once("basic", "basic.steady", i, base,
                                        self.config.recognition_latency_ms)
            self._record(s, warmup=(i < self.config.warmup))
            await asyncio.sleep(0.03)

    async def _run_style(self) -> None:
        texts = self.config.texts
        base = texts.get("short", "Hello world.")
        n = self.config.iterations
        for preset_id in self.harness.preset_manager.available():
            self.harness.preset_manager.apply(preset_id)
            await asyncio.sleep(0.3)  # let preset repaint + pulse settle
            for i in range(n):
                s = await self.measure_once("style", f"style.{preset_id}", i, base,
                                            self.config.recognition_latency_ms)
                self._record(s, warmup=(i < self.config.warmup))
                await asyncio.sleep(0.03)

    async def _run_state(self) -> None:
        texts = self.config.texts
        base = texts.get("short", "Hello world.")
        n = self.config.iterations
        overlay = self.harness.overlay
        # topmost on / off
        for top in (True, False):
            self.harness.topmost_manager.set_topmost(top)
            await asyncio.sleep(0.15)
            label = "state.topmost_on" if top else "state.topmost_off"
            for i in range(n):
                s = await self.measure_once("state", label, i, base,
                                            self.config.recognition_latency_ms)
                self._record(s, warmup=(i < self.config.warmup))
                await asyncio.sleep(0.03)
        # visible / hidden background
        if overlay is not None:
            for vis in (True, False):
                overlay.set_visible(vis)
                await asyncio.sleep(0.15)
                label = "state.visible" if vis else "state.hidden"
                for i in range(n):
                    s = await self.measure_once("state", label, i, base,
                                                self.config.recognition_latency_ms)
                    self._record(s, warmup=(i < self.config.warmup))
                    await asyncio.sleep(0.03)

    async def _run_load(self) -> None:
        n = self.config.iterations
        # Concurrency only makes sense in proxy (subtitle-signal) mode: the GDI
        # pixel probe cannot attribute a single repaint to one of N in-flight
        # measurements, so a real pixel probe is forced to run serially.
        conc = self.config.concurrency if not self.pixel_mode else 1
        for kind in ("short", "long"):
            text = self.config.texts.get(kind, "Hello world.")
            if conc > 1:
                tag = f"load.{kind}.c{conc}"
                for i in range(0, n, conc):
                    batch = list(range(i, min(i + conc, n)))
                    samples = await asyncio.gather(*[
                        self.measure_once("load", tag, idx, text,
                                         self.config.recognition_latency_ms)
                        for idx in batch
                    ])
                    for s in samples:
                        self._record(s, warmup=(i < self.config.warmup))
                    await asyncio.sleep(0.05)
            else:
                for i in range(n):
                    s = await self.measure_once("load", f"load.{kind}", i, text,
                                                self.config.recognition_latency_ms)
                    self._record(s, warmup=(i < self.config.warmup))
                    await asyncio.sleep(0.03)

    def _record(self, sample: LatencySample, warmup: bool) -> None:
        sample.warmup = warmup
        self._samples.append(sample)
        flag = "ANOMALY" if sample.anomaly else ("warmup" if warmup else "ok")
        print(f"  [{sample.tag}] #{sample.iteration + 1:>2} "
              f"e2e={sample.e2e:7.1f}ms "
              f"(rec={sample.recognition:5.1f} trans={sample.translation:6.1f} "
              f"pipe={sample.pipeline:5.1f} render={sample.render:5.1f}) [{flag}]")
        # Small settle so stacked measurements don't contend for the same frame.
        # (run inside the loop via asyncio.sleep in the caller is avoided here to
        #  keep _record synchronous; callers await between iterations.)

    # -- provider selection (real backends via the app's own registry) --------
    def _provider_entry(self, spec: dict) -> dict:
        opts = dict(spec.get("options") or {})
        opts.setdefault("timeout", spec.get("timeout", 60.0))
        opts.setdefault("temperature", spec.get("temperature", 0.2))
        ake = spec.get("api_key_environment") or opts.get("api_key_environment")
        if ake:
            opts["api_key_environment"] = ake
        if spec.get("keep_alive"):
            opts["keep_alive"] = spec["keep_alive"]
        return {
            "provider_id": spec["provider_id"],
            "base_url": spec.get("base_url", ""),
            "model": spec.get("model", ""),
            "options": opts,
        }

    async def _apply_provider_spec(self, spec) -> Optional[str]:
        """Switches the live translation chain. Returns the provider label, or
        ``None`` when the provider cannot be built/initialised (skipped)."""
        translation = self.harness.translation
        translation.set_inflight_dedup(self.config.inflight_dedup)
        if spec is None:
            # Default in-process double -- no network, offline benchmarking.
            translation.set_providers([_FakeTranslationProvider()])
            translation.set_language_pair(*self.config.language_pair)
            return "fake"
        label = spec.get("name") or spec.get("provider_id")
        entry = self._provider_entry(spec)
        try:
            from app.translation.registry import TranslationProviderRegistry
            reg = TranslationProviderRegistry()
            chain = reg.build_chain([entry])
        except Exception as exc:
            print(f"[错误] 构建 Provider {label} 失败: {exc}")
            return None
        if not chain:
            print(f"[错误] Provider {label} 未生成可用实例")
            return None
        translation.set_providers(chain)
        translation.set_language_pair(*self.config.language_pair)
        ak = spec.get("api_key")
        ake = spec.get("api_key_environment") or entry["options"].get("api_key_environment")
        if ak and ake:
            os.environ[ake] = ak
        try:
            await asyncio.gather(*[p.initialize() for p in chain if hasattr(p, "initialize")])
        except Exception as exc:
            print(f"[警告] Provider {label} 初始化失败（服务不可用？）: {exc}")
            self._unavailable.append(label)
            return None
        return label

    async def run(self, scenarios: Optional[list] = None) -> dict:
        scenarios = scenarios or self.config.scenarios
        self._unavailable = []
        specs = self.config.providers or [None]
        for spec in specs:
            name = await self._apply_provider_spec(spec)
            if name is None:
                continue
            self._current_provider_name = name
            print(f"\n#### 翻译 Provider: {name} ####")
            for sc in scenarios:
                print(f"== 场景: {sc} ==")
                if sc == "basic":
                    await self._run_basic()
                elif sc == "style":
                    await self._run_style()
                elif sc == "state":
                    await self._run_state()
                elif sc == "load":
                    await self._run_load()
                else:
                    print(f"  [跳过] 未知场景: {sc}")
        # Drop any in-flight translation tasks (e.g. ones abandoned on timeout)
        # so the event loop shuts down without "task destroyed" warnings.
        self.harness.translation.cancel_all()
        return self.build_report()

    # -- reporting -----------------------------------------------------------
    def _summarize_group(self, samples: list[LatencySample]) -> dict:
        valid = [s for s in samples if not s.warmup and not s.discarded]
        out: dict[str, Any] = {"count": len(valid), "samples": len(samples)}
        for metric in ("e2e", "recognition", "translation", "pipeline", "render"):
            out[metric] = compute_stats([getattr(s, metric) for s in valid])
        e2e_mean = out["e2e"]["mean"]
        bottleneck: dict[str, float] = {}
        for m in self.SEGMENTS:
            bottleneck[m] = (out[m]["mean"] / e2e_mean * 100.0) if e2e_mean > 0 else 0.0
        out["bottleneck_pct"] = bottleneck
        out["dominant_segment"] = (
            max(bottleneck, key=bottleneck.get) if bottleneck else "none"
        )
        out["anomaly_count"] = sum(1 for s in valid if s.anomaly)
        return out

    def build_report(self) -> dict:
        groups: dict[str, list[LatencySample]] = {}
        for s in self._samples:
            groups.setdefault(s.group, []).append(s)
        scenario_summaries = {
            g: self._summarize_group(samples) for g, samples in groups.items()
        }
        overall = self._summarize_group(self._samples)
        # Per-provider cross comparison (the core deliverable of horizontal
        # benchmarking across real backends).
        providers: dict[str, list[LatencySample]] = {}
        for s in self._samples:
            providers.setdefault(s.provider, []).append(s)
        provider_summary = {
            p: self._summarize_group(samples) for p, samples in providers.items()
        }
        return {
            "tool": "e2e_latency",
            "version": __version__,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "environment": self._environment(),
            "config": asdict(self.config),
            "pixel_probe_active": self.pixel_mode,
            "providers": list(providers.keys()),
            "provider_summary": provider_summary,
            "unavailable_providers": self._unavailable,
            "scenarios": {
                g: {
                    "summary": scenario_summaries[g],
                    "samples": [s.to_dict() for s in samples],
                }
                for g, samples in groups.items()
            },
            "anomalies": [s.to_dict() for s in self._samples if s.anomaly],
            "overall": overall,
        }

    def _environment(self) -> dict:
        env: dict[str, Any] = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python": platform.python_version(),
            "timer_native_qpc": self.timer.native,
            "pixel_probe_active": self.pixel_mode,
        }
        try:
            from PySide6 import __version__ as qt_version
            env["qt"] = qt_version
        except Exception:
            env["qt"] = "unknown"
        return env

    def write_reports(self, output_dir: str) -> tuple[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(output_dir, f"e2e_latency_report_{stamp}.json")
        csv_path = os.path.join(output_dir, f"e2e_latency_report_{stamp}.csv")
        report = self.build_report()
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        fields = ["group", "tag", "iteration", "provider", "warmup", "e2e",
                  "recognition", "translation", "pipeline", "render", "anomaly",
                  "discarded", "timeout", "reason"]
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for s in self._samples:
                writer.writerow(s.to_dict())
        return json_path, csv_path

    def print_summary(self) -> None:
        groups: dict[str, list[LatencySample]] = {}
        for s in self._samples:
            groups.setdefault(s.group, []).append(s)
        print("\n==== 延迟汇总（已剔除 warmup 与异常值）====")
        header = (f"{'场景':<14}{'n':>4}{'e2e均值':>10}{'P95':>9}{'P99':>9}"
                  f"{'翻译':>9}{'渲染':>9}{'瓶颈':>10}")
        print(header)
        for g, samples in groups.items():
            summary = self._summarize_group(samples)
            e = summary["e2e"]
            trans = summary["translation"]["mean"]
            render = summary["render"]["mean"]
            dom = summary["dominant_segment"]
            print(f"{g:<14}{summary['count']:>4}{e['mean']:>10.1f}{e['p95']:>9.1f}"
                  f"{e['p99']:>9.1f}{trans:>9.1f}{render:>9.1f}{dom:>10}")
        anomalies = [s for s in self._samples if s.anomaly]
        print(f"\n异常用例数: {len(anomalies)}")
        for s in anomalies[:20]:
            print(f"  - {s.tag} #{s.iteration + 1}: {s.reason} "
                  f"(e2e={s.e2e:.1f}ms)")

        # Cross-provider horizontal comparison (the primary benchmarking view).
        providers: dict[str, list[LatencySample]] = {}
        for s in self._samples:
            providers.setdefault(s.provider, []).append(s)
        if len(providers) > 1:
            print("\n==== 多 Provider 横向对比（端到端）====")
            print(f"{'Provider':<22}{'n':>4}{'e2e均值':>10}{'P95':>9}{'P99':>9}"
                  f"{'翻译均值':>10}")
            for p, samples in providers.items():
                summ = self._summarize_group(samples)
                e = summ["e2e"]
                trans = summ["translation"]["mean"]
                print(f"{p:<22}{summ['count']:>4}{e['mean']:>10.1f}{e['p95']:>9.1f}"
                      f"{e['p99']:>9.1f}{trans:>10.1f}")
        if self._unavailable:
            print(f"\n不可用 Provider（已跳过）: {', '.join(self._unavailable)}")
        print("=" * 60)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FiveLangTranslator 端到端字幕延迟实测脚本 (P0)")
    here = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument("--config", default=os.path.join(here, "e2e_latency_config.json"),
                        help="JSON 配置文件路径")
    parser.add_argument("--scenario", default=None,
                        help="仅运行单个场景: basic/style/state/load")
    parser.add_argument("--iterations", type=int, default=None,
                        help="覆盖配置中的循环次数")
    parser.add_argument("--no-pixel", action="store_true",
                        help="禁用像素探针，使用字幕信号作为渲染完成代理")
    parser.add_argument("--provider", default=None,
                        help="仅对配置中 name/provider_id 匹配的 Provider 做基准（默认全部）")
    parser.add_argument("--render-timeout", type=float, default=None,
                        help="覆盖单次测量总超时(ms)，真实 Provider 建议 ≥ 5000")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="覆盖负载场景的并发数（仅代理模式生效）")
    parser.add_argument("--no-inflight-dedup", action="store_true",
                        help="关闭 in-flight 并发同句去重（对照组基准，测量原始上游延迟）")
    parser.add_argument("--output-dir", default=os.path.join(here, "latency_reports"),
                        help="报告输出目录")
    return parser.parse_args(argv)


async def _run_all(args: argparse.Namespace) -> None:
    cfg = LatencyConfig.load(args.config)
    if args.provider:
        matches = [p for p in cfg.providers
                   if p.get("name") == args.provider or p.get("provider_id") == args.provider]
        if not matches:
            print(f"[错误] 配置中未找到名为 {args.provider} 的 Provider", file=sys.stderr)
            return
        cfg.providers = matches
    cfg.apply_overrides(
        iterations=args.iterations,
        pixel=(not args.no_pixel),
        scenarios=[args.scenario] if args.scenario else None,
        render_timeout=args.render_timeout,
        concurrency=args.concurrency,
        inflight_dedup=(not args.no_inflight_dedup),
    )
    harness = build_harness(include_overlay=True)
    tester = E2ELatencyTester(harness, cfg, pixel=(not args.no_pixel))

    provider_desc = ", ".join(p.get("name", p.get("provider_id")) for p in cfg.providers) \
        if cfg.providers else "fake(内置模拟)"
    print(f"计时器: {'QueryPerformanceCounter' if tester.timer.native else 'perf_counter'}"
          f" | 像素探针: {'启用' if tester.pixel_mode else '禁用(代理模式)'}")
    print(f"Provider: {provider_desc} | 并发: {cfg.concurrency} | "
          f"场景: {', '.join(cfg.scenarios)} | 循环次数/场景: {cfg.iterations}")

    await tester.run(cfg.scenarios)

    json_path, csv_path = tester.write_reports(args.output_dir)
    tester.print_summary()
    print(f"\n报告已生成:\n  JSON: {json_path}\n  CSV : {csv_path}")


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    app = QApplication.instance() or QApplication([])
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        try:
            loop.run_until_complete(_run_all(args))
        except KeyboardInterrupt:  # pragma: no cover
            return 130
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
