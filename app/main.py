from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path
from time import monotonic

# ---- 早期日志装配：必须在任何可能失败的 import（PySide6 等）之前 ----
# 否则启动期崩溃在 console=False 的冻结版下会被静默吞掉，毫无日志。
from app.core import paths

try:
    from app.core.logging_setup import setup_logging

    setup_logging()
except Exception as _boot_err:  # noqa: BLE001
    try:
        Path(sys.executable).resolve().parent.joinpath("boot_error.log").write_text(
            f"logging bootstrap failed: {_boot_err!r}", encoding="utf-8"
        )
    except Exception:
        pass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
)
from qasync import QEventLoop

from app.asr.service import ASRService
from app.audio.models import AudioSourceKind
from app.audio.service import AudioService
from app.controllers import DemoController, SubtitlePipelineController
from app.core import paths
from app.core.arbiter import ResultArbiter
from app.core.events import EventBus
from app.core.models import RecognitionResult, SourceType, SubtitleStatus
from app.core.secrets import protect as protect_secret, unprotect as unprotect_secret
from app.export.exporters import suggest_filename, write_export
from app.vision.ocr_engine import WindowsOcrEngine
from app.vision.region_window import OcrRegionWindow
from app.vision.service import RegionOcrService
from app.providers.mock import MockASR, MockTranslation
from app.providers.registry import ProviderRegistry
from app.settings import Store
from app.storage.cache_store import SqliteCacheStore
from app.storage.config_store import ConfigRepository
from app.storage.database import Database
from app.storage.glossary import GlossaryRepository
from app.storage.session import HistoryService
from app.storage.appearance import AppearanceRepository
from app.translation.cache import PersistentTranslationCache
from app.translation.context import DEFAULT_CONTEXT_SENTENCES
from app.translation.registry import TranslationProviderRegistry, plan_from_settings
from app.translation.service import TranslationService
from app.ui.main_window import MainWindow
from app.ui.overlay.window import OverlayWindow
from app.ui.overlay.visibility import SubtitleVisibilityManager
from app.ui.overlay.anchor import SubtitleAnchorManager, ANCHOR_TIPS
from app.ui.overlay.style_manager import SubtitleStyleManager
from app.ui.overlay.preset_manager import SubtitlePresetManager
from app.ui.overlay.topmost import WindowTopmostManager
from app.ui.theme import build_style, system_prefers_light
from app.player.tracker import PlayerWindowTracker
from app.windows.hotkeys import GlobalHotkeyManager, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT


CACHE_WARM_UP_ENTRIES = 500

logger = logging.getLogger(__name__)


class ThemeWatcher(QObject):
    """Notifies the runtime when Windows switches between light and dark apps."""

    theme_changed = Signal(bool)

    def eventFilter(self, _obj, event) -> bool:
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            self.theme_changed.emit(system_prefers_light())
        return False


class Runtime:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        # app_root = read-only bundled resources, data_root = writable user data
        # (models / whisper binaries / config / database). resource_root prefers
        # downloaded assets and falls back to the bundle, which keeps discovery
        # working both from source and from a PyInstaller build.
        self.app_root = paths.app_root()
        self.data_root = paths.data_root()
        self.project_root = paths.resource_root()
        self.store = Store()
        self.settings = self.store.load()
        self.bus = EventBus()
        self.registry = ProviderRegistry()
        self.registry.register(MockASR())
        self.registry.register(MockTranslation())
        self.arbiter = ResultArbiter()
        self.database = Database()
        self.cache_store = SqliteCacheStore(self.database)
        self.config_repository = ConfigRepository(self.database)
        self.topmost_manager = WindowTopmostManager(self.bus, self.config_repository)
        self.visibility_manager = SubtitleVisibilityManager(self.bus, self.config_repository)
        self.anchor_manager = SubtitleAnchorManager(self.bus, self.config_repository)
        self.appearance_repository = AppearanceRepository(self.database)
        self.style_manager = SubtitleStyleManager(self.bus, self.appearance_repository)
        # L1 = in-memory LRU with a seconds scale TTL, L2 = SQLite. Read L1 -> L2 -> provider.
        self.translation_cache = PersistentTranslationCache(
            self.cache_store, warm_up_entries=CACHE_WARM_UP_ENTRIES
        )
        self.translation_registry = TranslationProviderRegistry()
        self.translation = TranslationService(cache=self.translation_cache)
        self.glossary = GlossaryRepository(self.database)
        self.history = HistoryService(self.database)
        self.overlay = OverlayWindow(self.settings, style_manager=self.style_manager, bus=self.bus)
        self.preset_manager = SubtitlePresetManager(
            self.bus, self.style_manager, self.anchor_manager,
            self.topmost_manager, self.config_repository, self.overlay,
        )
        self.window = MainWindow(
            self.settings, self.overlay, self.style_manager, self.bus, self.preset_manager,
        )
        self.pipeline = SubtitlePipelineController(self.bus, self.arbiter, self.translation)
        self.demo = DemoController(self.pipeline, self.registry)
        self.audio = AudioService()
        self.asr = ASRService(self.project_root)
        self.ocr_region = OcrRegionWindow()
        self.ocr_service = RegionOcrService(self.ocr_region)
        self._devices = []
        self.shortcuts = []
        self.global_hotkeys = GlobalHotkeyManager(app)
        self.player_tracker = PlayerWindowTracker(lambda: int(self.overlay.winId()))
        self._connect()
        self._make_shortcuts()
        self._make_tray()
        self._make_global_hotkeys()
        self._install_theme_watcher()
        self.apply_theme()

    def _connect(self) -> None:
        self.bus.subtitle.connect(self.overlay.update_subtitle)
        self.bus.subtitle.connect(self.history.record)
        self.bus.error.connect(lambda message: self.window.statusBar().showMessage(message))
        self.overlay.committed.connect(self.save)
        self.overlay.state_changed.connect(self._state)
        self.overlay.line_budget_changed.connect(self.apply_line_budget)
        self.window.changed.connect(self.apply_settings)
        self.window.demo.connect(self.demo.start)
        self.window.edit.connect(self.edit)
        self.window.lock.connect(self.lock)
        self.window.through.connect(self.through)
        self.window.visible.connect(self.visible)
        self.window.recover.connect(self.recover)
        self.window.audio_start.connect(self.audio_start)
        self.window.audio_stop.connect(self.audio.stop_capture)
        self.window.audio_probe.connect(self.audio_probe)
        self.window.audio_device_changed.connect(self.audio_switch_device)
        self.window.audio_refresh.connect(self.audio_refresh)
        self.window.audio_gain_changed.connect(self.audio.set_gain)
        self.window.asr_apply.connect(self.configure_asr)
        self.window.asr_benchmark.connect(self.asr.run_benchmark)
        self.window.asr_latency_changed.connect(self.set_segment_policy)
        self.window.asr_draft_toggled.connect(self.set_draft_enabled)
        self.window.model_download.connect(self.download_model)
        self.window.model_verify.connect(self.verify_assets)
        self.window.translation_apply.connect(self.configure_translation)
        self.window.deepseek_apply.connect(self.configure_deepseek)
        self.window.deepseek_test.connect(self.test_deepseek)
        self.window.translation_test.connect(self.test_translation)
        self.audio.devices_changed.connect(self.audio_devices)
        self.audio.level_changed.connect(self.window.set_audio_level)
        self.audio.probability_changed.connect(self.window.set_vad_probability)
        self.audio.activity_changed.connect(self.window.set_audio_activity)
        self.audio.status_changed.connect(self.window.set_audio_status)
        self.audio.error.connect(lambda message: self.window.set_audio_status("错误：" + message))
        self.audio.health_changed.connect(self.window.set_audio_health)
        self.audio.draft_segment_ready.connect(self._audio_segment)
        self.audio.segment_ready.connect(self._audio_segment)
        self.asr.result_ready.connect(self.pipeline.submit_recognition)
        self.asr.result_ready.connect(lambda result: self.window.set_chain_text(getattr(result, "text", "")))
        # Recognition latency is logged by ASRService; log the translation leg here
        # so a slow subtitle can be attributed to exactly one of the two.
        self.translation.translated.connect(self._report_latency)
        # Region OCR: recognised screen text enters the very same pipeline, so it
        # is translated, shown on the overlay and recorded exactly like speech.
        self.ocr_service.text_recognized.connect(self._on_ocr_text)
        self.ocr_service.status_changed.connect(self.window.set_ocr_status)
        self.window.ocr_toggle.connect(self.ocr_toggle)
        self.window.ocr_pick_region.connect(self.ocr_pick_region)
        self.window.ocr_interval.connect(self.ocr_service.set_interval)
        self.window.ocr_lock.connect(self.ocr_region.set_locked)
        # While recognition runs the frame is an adjustment aid only: leaving it up
        # makes it blink on every capture (each grab hides it) and cover the content.
        self.ocr_region.interaction_ended.connect(self._hide_ocr_frame_if_running)
        self.window.ocr_language_changed.connect(self.ocr_service.set_language)
        languages = WindowsOcrEngine.available_languages()
        default = WindowsOcrEngine.default_language()
        if languages and default:
            self.ocr_service.set_language(default)
            self.window.set_ocr_languages(languages, default)
        else:
            self.window.set_ocr_status("未安装 OCR 语言包：设置 → 时间和语言 → 语言")
        self.asr.backend_status.connect(self.window.set_asr_status)
        self.asr.metrics_changed.connect(self.window.set_asr_metrics)
        self.asr.language_state.connect(self.window.set_language_state)
        self.asr.transcript_state.connect(self.window.set_transcript_state)
        self.asr.benchmark_finished.connect(self.window.set_benchmark_result)
        self.asr.error.connect(lambda message: self.window.set_asr_status("识别错误：" + message))
        self.translation.status_changed.connect(self.window.set_translation_status)
        self.translation.metrics_changed.connect(self.window.set_translation_metrics)
        self.translation.error.connect(self._report_translation_error)
        self.window.minimized_to_tray.connect(self._announce_tray)
        self.window.quit_requested.connect(self.shutdown)
        # Keep the chain panel live: a silent failure (capture running but ASR
        # disabled) is otherwise indistinguishable from "it is working".
        self._chain_timer = QTimer(self.app)
        self._chain_timer.setInterval(1500)
        self._chain_timer.timeout.connect(self._publish_chain)
        self._chain_timer.start()
        self.window.history_start.connect(self.start_history)
        self.window.history_stop.connect(self.stop_history)
        self.window.history_select.connect(self.select_history_session)
        self.window.history_refresh.connect(self.refresh_history)
        self.window.history_delete.connect(self.delete_history_session)
        self.window.history_clear.connect(self.clear_history)
        self.window.history_search.connect(self.search_history)
        self.window.export_requested.connect(self.export_session)
        self.window.glossary_refresh.connect(self.refresh_glossary)
        self.window.glossary_changed.connect(self.apply_glossary)
        self.window.glossary_import.connect(self.import_glossary)
        self.window.glossary_export.connect(self.export_glossary)
        self.history.recording_changed.connect(self.window.set_recording_state)
        self.history.session_closed.connect(lambda _session: self.refresh_history())
        self.player_tracker.changed.connect(self.overlay.set_player_state)
        self.global_hotkeys.failed.connect(lambda message: self.window.show_toast(message, ok=False))
        self.bus.topmost_changed.connect(self.overlay.set_topmost)
        self.bus.topmost_changed.connect(self.window.set_topmost_state)
        self.window.topmost_toggled.connect(self.set_topmost)
        self.bus.subtitleVisibilityChanged.connect(self.overlay.set_visible)
        self.bus.subtitleVisibilityChanged.connect(self.window.set_visibility_state)
        self.bus.subtitleVisibilityChanged.connect(self._update_tray_visibility_text)
        self.bus.subtitleVisibilityChanged.connect(self._on_visibility_changed)
        self.window.anchor_selected.connect(self.set_anchor)
        self.overlay.anchor_requested.connect(self.set_anchor)
        self.bus.subtitleAnchorChanged.connect(self.overlay.apply_anchor)
        self.bus.subtitleAnchorChanged.connect(self.window.set_anchor_state)

    async def initialize(self) -> None:
        await self.registry.initialize_all()
        # Persisted ASR settings were never handed to the service on start-up, so
        # a restart silently lost them. Apply (and auto-fill) them here.
        self.apply_asr_settings()
        self.refresh_model_state()
        warmed = await asyncio.to_thread(self.translation_cache.warm_up, CACHE_WARM_UP_ENTRIES)
        if warmed:
            self.window.statusBar().showMessage(f"已从 SQLite 预热 {warmed} 条译文到内存缓存")
        self.audio.start_monitor()
        self.audio_refresh()
        self.configure_translation({
            "provider": self.settings.translation_provider,
            "target_language": self.settings.translation_target_language,
            "style": self.settings.translation_style,
            "ollama_url": self.settings.ollama_url,
            "ollama_model": self.settings.ollama_model,
            "openai_url": self.settings.openai_compatible_url,
            "openai_model": self.settings.openai_compatible_model,
        })
        self.history.configure(
            self.settings.translation_target_language,
            self.settings.translation_style,
            self.settings.translation_provider,
        )
        self.refresh_glossary()
        self.refresh_history()
        self.topmost_manager.load()
        self.overlay.set_topmost(self.topmost_manager.isTopmost)
        self.window.set_topmost_state(self.topmost_manager.isTopmost)
        self.tray_topmost_action.setChecked(self.topmost_manager.isTopmost)
        self.visibility_manager.load()
        # Keep the overlay hidden at startup; it reveals itself on the first
        # subtitle (or when the user toggles visibility) instead of popping open
        # automatically.
        if not self.visibility_manager.isVisible():
            self.overlay.set_visible(False)
        self.window.set_visibility_state(self.visibility_manager.isVisible())
        self.anchor_manager.load()
        self.window.set_anchor_state(self.anchor_manager.getAnchor())
        if self.anchor_manager.used_default():
            # First run: snap to the default anchor so geometry matches the picker.
            self.overlay.apply_anchor(self.anchor_manager.getAnchor())
        if self.settings.follow_player:
            self.player_tracker.start()
        self.configure_asr({
            "executable": self.settings.whisper_executable,
            "model": self.settings.whisper_model,
            "language": self.settings.asr_language,
            "use_gpu": self.settings.asr_use_gpu,
            "cpu_fallback": self.settings.asr_cpu_fallback,
            "backend_mode": self.settings.asr_backend,
            "server_executable": self.settings.whisper_server_executable,
            "server_port": self.settings.whisper_server_port,
            "server_fallback": self.settings.whisper_server_fallback,
        })

    def show(self) -> None:
        self.window.show()
        self.window.sync()

    def show_startup_hint(self) -> None:
        """First-run guidance; replaces the old automatic demo playback."""
        marker = self.data_root / ".initialized"
        if not marker.exists():
            try:
                marker.write_text("1", encoding="utf-8")
            except OSError:
                pass
            self.window.statusBar().showMessage(
                "首次运行：请在「识别」页配置 whisper 后端，模型将按需下载到 "
                f"{self.data_root / 'models'}"
            )
            return
        if not self.asr.enabled:
            self.window.statusBar().showMessage(
                "whisper 后端未就绪：可在「识别」页指定可执行文件，或通过下载器获取模型"
            )

    def _make_shortcuts(self) -> None:
        for sequence, callback in (
            ("Ctrl+Shift+E", self.edit), ("Ctrl+Shift+L", self.lock),
            ("Ctrl+Shift+P", self.through), ("Ctrl+Shift+O", self.visible),
            ("Ctrl+Shift+R", self.recover), ("Ctrl+Shift+D", self.demo.start),
            ("Ctrl+=", lambda: self.adjust_font(2)),
            ("Ctrl+-", lambda: self.adjust_font(-2)),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self.window)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def _make_global_hotkeys(self) -> None:
        callbacks = (
            (101, "E", self.edit),
            (102, "L", self.lock),
            (103, "P", self.through),
            (104, "O", self.visible),
            (105, "R", self.recover),
            (106, "D", self.demo.start),
        )
        for hotkey_id, key, callback in callbacks:
            registered = self.global_hotkeys.register(hotkey_id, key, callback)
            if registered:
                # Native registration supersedes the application-only fallback.
                for shortcut in self.shortcuts:
                    if shortcut.key().toString().endswith(key):
                        shortcut.setEnabled(False)
        visibility_registered = self.global_hotkeys.register(
            107, "H", self.visibility_manager.toggle,
            MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
        )
        if not visibility_registered:
            # The toast + Win32 error log are emitted through the `failed`
            # signal handler above, so just disable the now-unavailable action.
            self.tray_visibility_action.setEnabled(False)
            self.tray_visibility_action.setToolTip("")

    def _make_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app.style().standardIcon(QStyle.SP_ComputerIcon), self.app)
        menu = QMenu()
        self.tray.activated.connect(self._tray_activated)
        for text, callback in (
            ("显示控制中心", self.show_control_centre), ("编辑字幕", self.edit),
            ("锁定/解锁", self.lock), ("鼠标穿透", self.through),
            ("播放模拟字幕", self.demo.start), ("紧急恢复", self.recover),
        ):
            action = QAction(text, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray_topmost_action = QAction("窗口置顶", menu)
        self.tray_topmost_action.setCheckable(True)
        self.tray_topmost_action.setToolTip("Ctrl+Alt+T（预留，本期未绑定）")
        self.tray_topmost_action.triggered.connect(lambda checked: self.set_topmost(checked))
        self.bus.topmost_changed.connect(self.tray_topmost_action.setChecked)
        menu.addAction(self.tray_topmost_action)
        self.tray_visibility_action = QAction("隐藏字幕", menu)
        self.tray_visibility_action.setToolTip("Ctrl+Alt+H")
        self.tray_visibility_action.triggered.connect(self.visible)
        self.bus.subtitleVisibilityChanged.connect(self._update_tray_visibility_text)
        menu.addAction(self.tray_visibility_action)
        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def audio_devices(self, devices) -> None:
        self._devices = devices
        self.audio_refresh()

    def audio_refresh(self) -> None:
        # Always re-enumerate. The old cache (`if not self._devices`) meant the
        # list was fetched once and never picked up newly plugged devices, a new
        # default, or a source switch.
        try:
            self._devices = self.audio.list_devices()
        except Exception as exc:  # noqa: BLE001 - report, never break the UI
            self.window.set_audio_status(f"设备枚举失败：{exc}")
            return
        self.window.set_audio_devices(self._devices)

    def audio_switch_device(self) -> None:
        """Re-opens capture on the newly chosen device while it is running.

        Selecting a device used to have no effect until the next manual start,
        which made the control look broken.
        """
        if not getattr(self.audio, "running", False):
            self.window.set_audio_status("已选择设备：点「开始电平测试」或「开始采集」即生效")
            return
        device_id = self.window.audio_device.currentData()
        kind = self.window.audio_kind.currentData()
        try:
            self.audio.start_capture(device_id, kind)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.window.set_audio_status(f"切换设备失败：{exc}")

    def audio_probe(self, enabled: bool) -> None:
        """Capture purely to visualise the input level.

        Deliberately bypasses the "models ready" gate used by 开始采集: the user
        must be able to confirm a device really receives audio *before*
        downloading any ASR model. Nothing here feeds the recognition pipeline.
        """
        if enabled:
            device_id = self.window.audio_device.currentData()
            kind = self.window.audio_kind.currentData()
            try:
                self.audio.start_capture(device_id, kind)
            except Exception as exc:  # noqa: BLE001 - report, keep the UI usable
                self.window.set_audio_status(f"电平测试启动失败：{exc}")
                self.window.audio_probe_btn.setChecked(False)
                return
            self.window.set_audio_status("电平测试进行中：请说话，观察电平条跳动")
        else:
            self.audio.stop_capture()
            self.window.set_audio_status("电平测试已停止")

    def set_draft_enabled(self, enabled: bool) -> None:
        """Draft subtitles: nicer streaming, but each one is a full inference."""
        self.settings.asr_draft_enabled = bool(enabled)
        self.save()

    def set_segment_policy(self, payload) -> None:
        """Apply the latency/accuracy preset picked in the UI."""
        if not payload:
            return
        self.audio.set_segment_policy(
            min_silence_ms=payload.get("min_silence_ms"),
            max_seconds=payload.get("max_seconds"),
        )

    def _publish_chain(self) -> None:
        """Report each link of the chain so a dead ASR is never silent."""
        capture = bool(self.audio.running)
        asr = bool(self.asr.enabled)
        details: list[str] = []
        if not capture:
            details.append("采集未启动 → 点「开始采集并识别」")
        if not asr:
            details.append("识别引擎未就绪 → 到「本地识别」点「检测并应用识别后端」")
        if capture and asr:
            details.append("链路已通：说话或播放音频后，「最近识别」会显示识别到的原文")
        self.window.set_chain_state({
            "capture": capture,
            "asr": asr,
            "translation": bool(getattr(self.settings, "translation_provider", "")),
            "detail": "；".join(details),
        })

    def audio_start(self, device_id, kind) -> None:
        from app.core import assets

        readiness = assets.status()
        if not readiness.ready:
            # Say exactly what is missing instead of a generic "not ready": the two
            # prerequisites fail independently and need different user actions.
            has_binary = bool(readiness.whisper_server or readiness.whisper_cli)
            if readiness.installed_models and not has_binary:
                hint = "模型已就绪，但缺少 whisper.cpp 二进制 → 请在「本地识别」页点「下载 whisper.cpp」"
            elif has_binary and not readiness.installed_models:
                hint = "whisper.cpp 已就绪，但缺少 GGML 模型 → 请在「本地识别」页下载 small 或 medium"
            else:
                hint = "请在「本地识别」页下载 GGML 模型与 whisper.cpp 二进制"
            self.window.set_audio_status(f"模型未就绪：{hint}")
            self.window.open_recognition_tab()
            return
        source = AudioSourceKind.SYSTEM_LOOPBACK if kind == "system_loopback" else AudioSourceKind.MICROPHONE
        self.settings.audio_kind = kind
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        self.settings.preferred_device_name = selected.name if selected else ""
        self.save()
        self.audio.start_capture(device_id, source)
        self._publish_chain()
        if not self.asr.enabled:
            # Say it now, not only after a VAD segment: with a quiet source the
            # segment never arrives and the user is left with zero feedback.
            self.window.set_audio_status(
                "已开始采集，但识别引擎未就绪 → 不会有字幕；请到「本地识别」点「检测并应用识别后端」"
            )
        if self.settings.auto_record_sessions:
            self.start_history({
                "source_type": source.value,
                "target_language": self.settings.translation_target_language,
                "translation_style": self.settings.translation_style,
                "provider": self.settings.translation_provider,
            })

    def _audio_segment(self, segment) -> None:
        if self.asr.enabled:
            # Every call costs about the same (whisper pads to a 30 s window), so
            # drafts are pure overhead on CPU and delay the final subtitle.
            if not segment.is_final and not self.settings.asr_draft_enabled:
                return
            self.asr.submit(segment)
        elif segment.is_final:
            # Do not auto-start the demo: it would pop a subtitle box without the
            # user's intent. Trigger it from the dashboard button / hotkey instead.
            self.window.set_asr_status("whisper.cpp 未配置，点击「播放模拟实时字幕」体验演示")

    def _report_latency(self, result) -> None:
        """Log the translation leg (recognition is logged by ASRService)."""
        latency = int(getattr(result, "latency_ms", 0) or 0)
        text = (getattr(result, "translated_text", "") or "")[:30]
        logger.info("[翻译] %d ms · %s", latency, text)

    def configure_asr(self, payload) -> None:
        self.settings.whisper_executable = payload.get("executable", "")
        self.settings.whisper_model = payload.get("model", "")
        self.settings.asr_language = payload.get("language", "auto")
        self.settings.asr_use_gpu = bool(payload.get("use_gpu", True))
        self.settings.asr_cpu_fallback = bool(payload.get("cpu_fallback", True))
        self.settings.asr_backend = payload.get("backend_mode", "auto")
        self.settings.whisper_server_executable = payload.get("server_executable", "")
        self.settings.whisper_server_port = int(payload.get("server_port", 8178))
        self.settings.whisper_server_fallback = bool(payload.get("server_fallback", True))
        self.settings.asr_beam_size = int(payload.get("beam_size", 1) or 1)
        self.save()
        self.apply_asr_settings()

    def _fill_asr_defaults(self) -> None:
        """Fills blank ASR paths from the assets already installed on disk.

        The download flow installs whisper.cpp and the GGML model into the data
        directory but leaves the settings fields empty. Without this the
        recogniser has nothing to execute, so capture runs while no transcript is
        ever produced -- which looks exactly like "recognition is broken".
        """
        from app.core import assets

        readiness = assets.status()
        if not self.settings.whisper_executable:
            self.settings.whisper_executable = readiness.whisper_cli
        if not self.settings.whisper_server_executable:
            self.settings.whisper_server_executable = readiness.whisper_server
        if not self.settings.whisper_model and readiness.installed_models:
            models_dir = Path(readiness.models_dir)
            for spec in assets.MODELS:
                candidate = models_dir / spec.filename
                if spec.key in readiness.installed_models and candidate.is_file():
                    self.settings.whisper_model = str(candidate)
                    break

    def apply_asr_settings(self) -> None:
        """Pushes ASR settings into the service, filling any gaps first."""
        self._fill_asr_defaults()
        self.asr.configure(
            self.settings.whisper_executable,
            self.settings.whisper_model,
            self.settings.asr_language,
            self.settings.asr_use_gpu,
            self.settings.asr_cpu_fallback,
            self.settings.asr_backend,
            self.settings.whisper_server_executable,
            self.settings.whisper_server_port,
            self.settings.whisper_server_fallback,
            self.settings.asr_beam_size,
        )

    def refresh_model_state(self) -> None:
        """Report local ASR asset availability to the UI (also gates capture)."""
        from app.core import assets

        current = assets.status()
        self.window.set_model_state({
            "installed_models": current.installed_models,
            "ready": current.ready,
        })
        self.window.set_assets_detail({
            "model": ", ".join(current.installed_models) or "未下载",
            "whisper": bool(current.whisper_server or current.whisper_cli),
            "vad": bool(assets.silero_vad_path()),
            "ready": current.ready,
        })

    def verify_assets(self) -> None:
        """Re-check integrity of local assets; corrupt models are removed and reported."""
        from app.core import assets

        current = assets.status()
        corrupted = [key for key in current.installed_models if not assets.verify_model(key)]
        vad_ok = bool(assets.silero_vad_path())
        whisper_ok = bool(current.whisper_server or current.whisper_cli)
        if corrupted:
            self.window.set_download_finished(
                False, f"校验失败：{', '.join(corrupted)} 已损坏并删除，请重新下载"
            )
            self.window.show_toast(f"校验未通过 · {', '.join(corrupted)} 已删除", ok=False)
        else:
            self.window.set_download_finished(True, "完整性校验通过")
            self.window.show_toast(
                f"校验通过 · 模型 {len(current.installed_models)} · "
                f"whisper.cpp {'就绪' if whisper_ok else '缺失'} · "
                f"VAD {'就绪' if vad_ok else '缺失'}",
                ok=True,
            )
        self.refresh_model_state()

    def download_model(self, key: str, source: str = "huggingface") -> None:
        self.settings.download_source = source
        self.save()
        asyncio.create_task(self._download_model_async(key, source))

    async def _download_model_async(self, key: str, source: str = "huggingface") -> None:
        from app.core import assets

        loop = asyncio.get_running_loop()
        state = {"last": monotonic(), "last_written": 0}

        def progress(written: int, total: int) -> None:
            # 运行在下载线程，UI 更新必须切回事件循环
            now = monotonic()
            elapsed = max(1e-6, now - state["last"])
            speed = max(0.0, (written - state["last_written"]) / elapsed)
            state["last"] = now
            state["last_written"] = written
            eta = (total - written) / speed if speed > 0 and total else None
            loop.call_soon_threadsafe(
                self.window.set_download_progress,
                {"written": written, "total": total, "speed": speed, "eta": eta},
            )

        self.window.set_model_status("正在下载…")
        try:
            await asyncio.to_thread(assets.download_model, key, progress, source)
        except Exception as exc:
            self.window.set_download_finished(False, f"下载失败：{exc}")
            self.window.set_asr_status(f"模型下载失败：{exc}")
            return
        self.window.set_download_finished(True, "下载完成，正在校验完整性…")
        ok = await asyncio.to_thread(assets.verify_model, key)
        if not ok:
            self.window.set_download_finished(False, "校验失败：文件已损坏并被删除，请重新下载")
            self.window.set_asr_status("模型校验失败，已回退")
        else:
            # 补齐其余缺失资源：就绪条件要求「模型 + whisper.cpp 二进制」同时存在，
            # VAD 模型缺失则会降级为能量 VAD，故一并尝试获取。
            missing = await asyncio.to_thread(assets.status)
            if not (missing.whisper_server or missing.whisper_cli):
                self.window.set_model_status("正在下载 whisper.cpp 二进制…")
                try:
                    await asyncio.to_thread(assets.download_whisper_cpp, progress)
                except Exception as exc:
                    self.window.set_asr_status(f"whisper.cpp 二进制下载失败：{exc}")
            if not assets.silero_vad_path():
                self.window.set_model_status("正在下载 VAD 模型…")
                try:
                    await asyncio.to_thread(assets.download_silero_vad, progress)
                except Exception as exc:
                    self.window.set_asr_status(f"VAD 模型下载失败：{exc}")
            self.window.set_download_finished(True, f"模型 {key} 已就绪")
            self.window.set_asr_status(f"模型 {key} 校验通过，可开始识别")
            # 下载完成后自动启用识别后端：若用户尚未手动指定路径，则使用自动发现。
            if not self.asr.enabled:
                self.window.set_model_status("正在启用识别后端…")
                self.configure_asr({
                    "executable": self.settings.whisper_executable,
                    "model": self.settings.whisper_model,
                    "language": self.settings.asr_language,
                    "use_gpu": self.settings.asr_use_gpu,
                    "cpu_fallback": self.settings.asr_cpu_fallback,
                    "backend_mode": self.settings.asr_backend,
                    "server_executable": self.settings.whisper_server_executable,
                    "server_port": self.settings.whisper_server_port,
                    "server_fallback": self.settings.whisper_server_fallback,
                })
            model_path = str(paths.models_dir() / assets.model_spec(key).filename)
            self.window.set_asr_model_path(model_path)
        self.refresh_model_state()

    def configure_translation(self, payload=None) -> None:
        data = payload if isinstance(payload, dict) else {}
        self.settings.translation_target_language = data.get("target_language", self.settings.translation_target_language)
        self.settings.translation_style = data.get("style", self.settings.translation_style)
        self.settings.translation_max_tokens = int(data.get("max_tokens", self.settings.translation_max_tokens) or 0)
        self.settings.translation_context_sentences = int(
            data.get(
                "context_sentences",
                getattr(self.settings, "translation_context_sentences", DEFAULT_CONTEXT_SENTENCES),
            ) or 0
        )
        if "ollama_url" in data:
            self.settings.ollama_url = data.get("ollama_url", self.settings.ollama_url)
            self.settings.ollama_model = data.get("ollama_model", self.settings.ollama_model)
        if "openai_url" in data:
            self.settings.openai_compatible_url = data.get("openai_url", self.settings.openai_compatible_url)
            self.settings.openai_compatible_model = data.get("openai_model", self.settings.openai_compatible_model)
        if "endpoints" in data:
            endpoints = dict(self.settings.translation_endpoints)
            endpoints.update(data.get("endpoints") or {})
            self.settings.translation_endpoints = endpoints
        if "chain" in data:
            self.settings.translation_chain = list(data.get("chain") or [])
        elif "provider" in data:
            # The current UI only edits the primary entry; keep manual fallbacks.
            primary = data.get("provider", "ollama")
            self.settings.translation_provider = primary
            chain = [primary]
            chain += [item for item in self.settings.translation_chain if item != primary]
            self.settings.translation_chain = chain
        self.apply_translation_settings()
        self.save()

    def configure_deepseek(self, payload: dict) -> None:
        """Encrypts and persists the DeepSeek endpoint/key, then applies it live."""
        data = dict(payload or {})
        endpoints = dict(getattr(self.settings, "translation_endpoints", None) or {})
        entry = {
            "base_url": str(data.get("base_url", "") or ""),
            "model": str(data.get("model", "") or ""),
            "timeout": float(data.get("timeout", 60.0) or 60.0),
            "retries": int(data.get("retries", 2) or 2),
        }
        endpoints["deepseek"] = entry
        self.settings.translation_endpoints = endpoints
        key = str(data.get("api_key", "") or "").strip()
        if key:
            try:
                self.settings.api_key_secrets["deepseek"] = protect_secret(key)
            except Exception as exc:  # noqa: BLE001 - never store a key unencrypted
                self.window.set_deepseek_status(f"密钥加密保存失败，未写入：{exc}")
                return
        chain = list(getattr(self.settings, "translation_chain", None) or [])
        if "deepseek" not in chain:
            chain.append("deepseek")
            self.settings.translation_chain = chain
        self.apply_translation_settings()
        self.save()
        self.window.set_deepseek_status(
            "已保存并生效：API Key 经 Windows DPAPI 加密，DeepSeek 已加入回退链"
        )

    def test_deepseek(self, payload: dict) -> None:
        """Saves the entry, then runs the standard provider connection test."""
        data = dict(payload or {})
        key = str(data.get("api_key", "") or "").strip()
        if key:
            try:
                self.settings.api_key_secrets["deepseek"] = protect_secret(key)
            except Exception:  # noqa: BLE001 - test still runs with the env key
                pass
        self.configure_deepseek(data)
        self.test_translation({
            "provider_id": "deepseek",
            "base_url": str(data.get("base_url", "") or ""),
            "model": str(data.get("model", "") or ""),
        })

    def _inject_api_keys(self, plan) -> None:
        """Attaches DPAPI-decrypted API keys to the providers that declare one.

        Keys never sit in settings.json as plaintext; they are decrypted here, in
        memory only, right before the provider objects are built.
        """
        encrypted = dict(getattr(self.settings, "api_key_secrets", None) or {})
        for entry in plan:
            provider_id = entry.get("provider_id", "")
            cipher = encrypted.get(provider_id, "")
            if not cipher:
                continue
            options = entry.setdefault("options", {})
            options["api_key"] = unprotect_secret(cipher)

    def apply_translation_settings(self) -> None:
        plan = plan_from_settings(self.settings)
        self._inject_api_keys(plan)
        missing = [item for item in plan if item["provider_id"] not in self.translation_registry.presets]
        plan = [item for item in plan if item["provider_id"] in self.translation_registry.presets]
        self.translation.set_language_pair(
            self.settings.translation_target_language,
            self.settings.translation_style,
        )
        # Prompt-context window: fewer sentences = fewer tokens = faster inference.
        self.translation.set_context_sentences(
            int(getattr(self.settings, "translation_context_sentences", DEFAULT_CONTEXT_SENTENCES) or 0)
        )
        self.translation.set_providers(self.translation_registry.build_chain(plan))
        self.history.configure(
            self.settings.translation_target_language,
            self.settings.translation_style,
            self.settings.translation_chain[0] if self.settings.translation_chain else "",
        )
        problems = self.translation_registry.validate_plan(plan)
        problems += [f"未知的翻译Provider：{item['provider_id']}" for item in missing]
        if problems:
            self.window.set_translation_status("配置提醒 · " + "；".join(problems))

    def apply_line_budget(self, lines: int) -> None:
        """The overlay box height drives how many lines the translation may use."""
        if self.translation.set_max_lines(lines):
            message = f"字幕框可视行数 {self.translation.max_lines} 行 · 译文长度约束已更新"
            self.window.statusBar().showMessage(message)

    def ocr_pick_region(self) -> None:
        """Shows the OCR frame pinned above other windows so it is actually visible."""
        self.ocr_region.reveal()
        self.window.set_ocr_status(
            "调整识别框：拖动内部移动，拖边角缩放 · 「开始识别」后框自动隐藏（避免遮挡与每秒闪烁），"
            "「停止识别」也会关闭框；需要重新调整再点「选择区域」"
        )

    def ocr_toggle(self, enabled: bool) -> None:
        """Starts or stops the periodic region recognition."""
        if enabled:
            if not self.ocr_region.isVisible():
                self.ocr_pick_region()
            self.ocr_service.start()
            # The frame exists only for positioning: once recognition runs it has to
            # go, otherwise it blinks once per capture and sits over the content.
            self.ocr_region.hide()
        else:
            self.ocr_service.stop()
            # Stopping must dismiss the frame too -- it used to stay on screen
            # forever because nothing ever hid it.
            self.ocr_region.hide()

    def _hide_ocr_frame_if_running(self) -> None:
        """Hides the adjustment frame again once the user lets go (while running)."""
        if self.ocr_service.running:
            self.ocr_region.hide()

    def _on_ocr_text(self, text: str) -> None:
        """Feeds recognised region text into the pipeline as an OCR result."""
        now_ms = int(monotonic() * 1000)
        result = RecognitionResult(
            segment_id=f"ocr-{now_ms}",
            revision=1,
            text=text,
            language=self.ocr_service.engine.language_tag,
            confidence=1.0,
            start_ms=now_ms,
            end_ms=now_ms,
            status=SubtitleStatus.FINAL,
            provider="windows-ocr",
            source=SourceType.OCR,
        )
        self.pipeline.submit_recognition(result)

    def set_topmost(self, enabled: bool) -> None:
        """User/code request to change always-on-top. Persists, broadcasts, toasts."""
        self.topmost_manager.set_topmost(enabled)
        self.window.show_toast("字幕已置顶" if enabled else "已取消置顶", duration=1500)

    def _update_tray_visibility_text(self, visible: bool) -> None:
        self.tray_visibility_action.setText("隐藏字幕" if visible else "显示字幕")

    def _on_visibility_changed(self, visible: bool) -> None:
        self.window.show_toast("字幕已隐藏" if not visible else "字幕已显示", duration=1500)

    def set_anchor(self, anchor: str) -> None:
        """User request to re-align the subtitle window to one of the nine anchors."""
        self.anchor_manager.setAnchor(anchor)
        self.window.statusBar().showMessage(f"字幕已对齐到 {ANCHOR_TIPS.get(anchor, anchor)}")

    def start_history(self, payload) -> None:
        data = payload if isinstance(payload, dict) else {}
        session = self.history.start_session(
            str(data.get("name", "") or ""),
            str(data.get("source_type", "system_audio") or "system_audio"),
            str(data.get("target_language", self.settings.translation_target_language) or "zh"),
            str(data.get("translation_style", self.settings.translation_style) or "cinema"),
            str(data.get("provider", self.settings.translation_provider) or ""),
        )
        self.refresh_history()
        self.window.statusBar().showMessage(f"开始记录会话 · {session.name}")

    def stop_history(self) -> None:
        session = self.history.end_session()
        self.refresh_history()
        if session is not None:
            self.window.statusBar().showMessage(f"会话已保存 · {session.name} · {session.segment_count} 条字幕")

    def refresh_history(self) -> None:
        self.window.set_history_sessions(self.history.repository.list_sessions())
        self.refresh_stats()

    def select_history_session(self, session_id: str) -> None:
        if not session_id:
            return
        self.window.set_history_segments(self.history.repository.list_segments(session_id))

    def delete_history_session(self, session_id: str) -> None:
        self.history.repository.delete_session(session_id)
        self.refresh_history()
        self.window.statusBar().showMessage("会话已删除")

    def clear_history(self) -> None:
        self.history.repository.clear()
        self.refresh_history()
        self.window.statusBar().showMessage("历史记录已清空")

    def search_history(self, payload) -> None:
        data = payload if isinstance(payload, dict) else {}
        query = str(data.get("query", "") or "").strip()
        session_id = data.get("session_id") or None
        if not query:
            if session_id:
                self.select_history_session(session_id)
            else:
                self.refresh_history()
            return
        rows = self.history.repository.search_segments(query, session_id)
        self.window.set_history_segments(rows)
        self.window.statusBar().showMessage(f"搜索命中 {len(rows)} 条字幕")

    def refresh_stats(self) -> None:
        self.window.set_storage_stats({
            "sessions": len(self.history.repository.list_sessions()),
            "segments": self.history.repository.count_segments(),
            "cache": self.cache_store.count(),
            "memory_cache": len(self.translation_cache.memory),
            "glossary": self.glossary.count(),
            "path": str(self.database.path),
        })

    def export_session(self, payload) -> None:
        data = payload if isinstance(payload, dict) else {}
        fmt = str(data.get("format", "srt") or "srt")
        content = str(data.get("content", "bilingual") or "bilingual")
        repository = self.history.repository
        session_id = data.get("session_id") or self.history.active_session_id
        if not session_id:
            sessions = repository.list_sessions(1)
            if not sessions:
                self.window.set_export_result("还没有可导出的会话，先开始一次记录")
                return
            session_id = sessions[0].id
        session = repository.get_session(session_id)
        segments = repository.list_segments(session_id)
        if not segments:
            self.window.set_export_result("当前会话还没有已写入的字幕片段")
            return
        default = str(Path.home() / suggest_filename(session, fmt))
        destination, _ = QFileDialog.getSaveFileName(
            None, "导出字幕", default, f"字幕文件 (*.{fmt});;所有文件 (*)"
        )
        if not destination:
            return
        try:
            path = write_export(destination, fmt, segments, session, content)
        except Exception as exc:
            self.window.set_export_result(f"导出失败：{exc}")
            return
        self.window.set_export_result(f"已导出 {len(segments)} 条字幕 → {path}")

    def refresh_glossary(self) -> None:
        rows = self.glossary.all()
        self.window.set_glossary_rows(rows)
        self.translation.set_glossary({row.term: row.translation for row in rows})
        self.refresh_stats()

    def apply_glossary(self, entries: dict) -> None:
        mapping = {str(term).strip(): str(value).strip() for term, value in dict(entries or {}).items()}
        mapping = {term: value for term, value in mapping.items() if term and value}
        self.glossary.replace_all(mapping)
        self.translation.set_glossary(mapping)
        self.window.set_glossary_status(f"已保存并应用 {len(mapping)} 条术语")
        self.refresh_stats()

    def import_glossary(self, path: str) -> None:
        try:
            raw = Path(path).expanduser().read_text(encoding="utf-8")
            added = self.glossary.import_json(raw)
        except Exception as exc:
            self.window.set_glossary_status(f"导入失败：{exc}")
            return
        self.refresh_glossary()
        self.window.set_glossary_status(f"已导入 {added} 条术语")

    def export_glossary(self, path: str) -> None:
        try:
            Path(path).expanduser().write_text(self.glossary.export_json(), encoding="utf-8")
        except Exception as exc:
            self.window.set_glossary_status(f"导出失败：{exc}")
            return
        self.window.set_glossary_status(f"已导出术语表 → {path}")

    def _report_translation_error(self, message: str) -> None:
        # A toast instead of a modal dialog: it never covers the subtitles.
        self.window.set_translation_status("翻译错误：" + message)
        self.window.show_toast("翻译失败 · " + message, ok=False)

    def apply_theme(self, light: bool | None = None) -> None:
        mode = system_prefers_light() if light is None else bool(light)
        self.app.setStyleSheet(build_style(mode))
        self.window.apply_theme_mode(mode)
        # Colours follow the Windows light/dark theme until the user customises them.
        self.style_manager.apply_theme_defaults(mode)
        self._apply_window_material(mode)

    def _apply_window_material(self, light: bool) -> None:
        """Tints the native frame and rounds the corners to match the theme.

        Deliberately does NOT use ``WA_TranslucentBackground``: that turns the
        window into a layered surface which Qt never paints, and unless the
        Windows 11 Mica backdrop happens to be available it renders pure black.
        The frosted look is produced by translucent cards over an opaque
        gradient instead, which is stable on every Windows version.
        """
        try:
            from app.windows import window_styles

            hwnd = int(self.window.winId())
            window_styles.set_immersive_dark(hwnd, not light)
            window_styles.set_rounded_corners(hwnd, True)
        except Exception:
            pass

    def _install_theme_watcher(self) -> None:
        self.theme_watcher = ThemeWatcher()
        self.theme_watcher.theme_changed.connect(self.apply_theme)
        self.app.installEventFilter(self.theme_watcher)

    def _announce_tray(self) -> None:
        self.tray.showMessage(
            "FiveLangTranslator",
            "控制中心已收起到托盘，双击托盘图标可重新打开",
            QSystemTrayIcon.Information,
            2500,
        )

    def show_control_centre(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_control_centre()

    def test_translation(self, payload=None) -> None:
        self.window.set_translation_testing(True)
        asyncio.create_task(self._test_translation_async(payload))

    async def _test_translation_async(self, payload=None) -> None:
        try:
            await self._run_translation_test(payload)
        finally:
            self.window.set_translation_testing(False)

    async def _run_translation_test(self, payload=None) -> None:
        data = payload if isinstance(payload, dict) else {}
        provider_id = str(data.get("provider_id", "") or "")
        if not provider_id:
            provider_id = self.settings.translation_chain[0] if self.settings.translation_chain else "ollama"
        # A key saved from the panel is stored DPAPI-encrypted; it must feed both
        # the provider and the presence check, otherwise the UI always reports
        # "no key detected" even right after saving one.
        encrypted = dict(getattr(self.settings, "api_key_secrets", None) or {})
        stored_key = unprotect_secret(encrypted.get(provider_id, ""))
        try:
            preset = self.translation_registry.get(provider_id)
            provider = self.translation_registry.create(
                provider_id,
                str(data.get("base_url", "") or ""),
                str(data.get("model", "") or ""),
                {"api_key": stored_key},
            )
        except Exception as exc:
            self.window.set_translation_status(f"连接测试失败：{exc}")
            self.window.show_toast(f"连接测试失败：{exc}", ok=False)
            return

        if preset.requires_api_key and not (stored_key or os.environ.get(preset.api_key_environment, "")):
            message = f"未检测到密钥：请在面板填写，或设置环境变量 {preset.api_key_environment}"
            self.window.set_translation_status(message)
            self.window.show_toast(message, ok=False)
            return

        try:
            ok = await provider.health_check()
        except Exception as exc:
            message = f"{preset.title} 不可用：{exc}"
            self.window.set_translation_status(message)
            self.window.show_toast(message, ok=False)
            return
        message = f"{preset.title} · {'可用' if ok else '不可用'}"
        self.window.set_translation_status("连接测试 · " + message)
        self.window.show_toast(message, ok=bool(ok))

    def apply_settings(self) -> None:
        if self.settings.follow_player:
            self.player_tracker.start()
        else:
            self.player_tracker.stop()
        self.save()

    def edit(self) -> None:
        self.overlay.set_through(False)
        self.overlay.set_locked(False)
        self.overlay.show()
        self.overlay.raise_()
        self._state()

    def lock(self) -> None:
        self.overlay.set_locked(not self.overlay.locked)
        self._state()

    def adjust_font(self, delta: int) -> None:
        """Quick subtitle font-size stepper, bound to Ctrl+= / Ctrl+-.

        Locked or click-through boxes keep their size on purpose: locking means
        the layout is fixed, so the shortcut is a no-op rather than silently
        fighting the lock.
        """
        overlay = self.overlay
        if overlay.locked or overlay.through:
            return
        manager = overlay.style_manager
        if manager is None:
            return
        current = manager.get_style().subtitle_font_size
        new_size = max(8, min(72, current + delta))
        if new_size == current:
            return
        manager.update(subtitle_font_size=new_size)
        self.window.statusBar().showMessage(f"字幕字号 {new_size}")

    def through(self) -> None:
        self.overlay.set_through(not self.overlay.through)
        self._state()

    def visible(self) -> None:
        self.visibility_manager.toggle()

    def recover(self) -> None:
        self.overlay.set_through(False)
        self.overlay.set_locked(False)
        self.overlay.reset_size()
        self.overlay.apply_profile(self.overlay.fullscreen_mode, self.overlay.player_rect)
        self.overlay.show()
        self.overlay.raise_()
        self._state()

    def _state(self) -> None:
        self.window.sync()
        self.save()

    def save(self) -> None:
        self.overlay.save_profile()
        self.store.save(self.settings)

    def shutdown(self) -> None:
        """Tear everything down and leave the process.

        Re-entrant guard: the tray menu and the in-app button can both fire it.
        A watchdog backs every path, because a single blocked await below (the
        whisper-server subprocess) used to leave FiveLangTranslator.exe alive in
        the tray forever, which then locked dist and broke the next build.
        """
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        threading.Thread(target=self._exit_watchdog, name="exit-watchdog", daemon=True).start()
        asyncio.create_task(self._shutdown_async())

    def _exit_watchdog(self) -> None:
        """Last resort: never leave a zombie holding the dist folder."""
        deadline = monotonic() + 10.0
        while monotonic() < deadline:
            time.sleep(0.5)
        os._exit(0)

    async def _shutdown_async(self) -> None:
        try:
            await self._teardown()
        finally:
            # app.quit() must run even if a teardown step blew up, otherwise the
            # process would stay alive with no visible window.
            self.app.quit()

    async def _teardown(self) -> None:
        for step in (
            lambda: self.player_tracker.stop(),
            lambda: self.global_hotkeys.unregister_all(),
            lambda: self.audio.close(),
            lambda: self.translation.cancel_all(),
            lambda: self.history.end_session(),
            self.save,
        ):
            try:
                step()
            except Exception:
                pass
        server = getattr(self.asr, "server_process", None)
        child = getattr(server, "process", None) if server is not None else None
        # Bounded: awaiting asr.close() with no timeout was the reason an exit
        # request could never reach app.quit().
        try:
            await asyncio.wait_for(self.asr.close(), 4.0)
        except Exception:
            pass
        self._kill_child(child)
        self._kill_child(getattr(getattr(self.asr, "server_process", None), "process", None))
        try:
            self.history.close()
        except Exception:
            pass

    def _kill_child(self, process) -> None:
        """Make sure the whisper-server subprocess never outlives the app."""
        if process is None:
            return
        try:
            if process.returncode is None:
                process.kill()
        except Exception:
            pass


async def boot(runtime: Runtime) -> None:
    await runtime.initialize()
    runtime.show()
    QTimer.singleShot(500, runtime.show_startup_hint)


def _already_running() -> bool:
    """True when another instance already owns the single-instance mutex.

    Relaunching used to pile up extra processes that lingered in the tray and
    kept dist locked, so a second copy now refuses to start instead.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        # The handle is deliberately never closed: it stays owned for the whole
        # process lifetime, and Windows releases it on exit.
        kernel32.CreateMutexW(None, True, "FiveLangTranslator_SingleInstance")
        return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def main() -> int:
    # Honour fractional scaling (125% / 150%) exactly instead of rounding it to a
    # whole factor. Qt's default rounding policy makes it render at a scale that
    # does not match the display, which oversizes every widget relative to the
    # window and clips the control centre unless it is maximised. Must be set
    # before the application object is created.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if _already_running():
        QMessageBox.information(
            None,
            "FiveLangTranslator",
            "程序已经在运行了（通常在右下角系统托盘里）。\n\n"
            "请先右键托盘图标 → 退出，再重新启动；\n"
            "若托盘找不到，用任务管理器结束 FiveLangTranslator.exe。",
        )
        return 0
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    runtime = Runtime(app)
    with loop:
        loop.create_task(boot(runtime))
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
