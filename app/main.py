from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QStyle, QSystemTrayIcon
from qasync import QEventLoop

from app.asr.service import ASRService
from app.audio.models import AudioSourceKind
from app.audio.service import AudioService
from app.controllers import DemoController, SubtitlePipelineController
from app.core import paths
from app.core.arbiter import ResultArbiter
from app.core.events import EventBus
from app.export.exporters import suggest_filename, write_export
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
        self.window.audio_refresh.connect(self.audio_refresh)
        self.window.asr_apply.connect(self.configure_asr)
        self.window.asr_benchmark.connect(self.asr.run_benchmark)
        self.window.model_download.connect(self.download_model)
        self.window.translation_apply.connect(self.configure_translation)
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
        self.global_hotkeys.failed.connect(lambda message: self.window.statusBar().showMessage(message))
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
        self.overlay.set_visible(self.visibility_manager.isVisible())
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
        self.overlay.show()
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
            self.window.show_toast(
                "全局热键 Ctrl+Alt+H 注册失败，字幕显隐热键暂不可用", ok=False,
            )
            self.tray_visibility_action.setEnabled(False)
            self.tray_visibility_action.setToolTip("")
            logger.error("Ctrl+Alt+H 全局热键注册失败（可能已被其他程序占用或权限不足）")

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
        if not self._devices:
            self._devices = self.audio.list_devices()
        self.window.set_audio_devices(self._devices)

    def audio_start(self, device_id, kind) -> None:
        from app.core import assets

        if not assets.status().ready:
            # 兜底拦截：模型未就绪时不允许开始采集
            self.window.set_audio_status("模型未就绪：请先在「本地识别」页下载 GGML 模型与 whisper.cpp 二进制")
            return
        source = AudioSourceKind.SYSTEM_LOOPBACK if kind == "system_loopback" else AudioSourceKind.MICROPHONE
        self.settings.audio_kind = kind
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        self.settings.preferred_device_name = selected.name if selected else ""
        self.save()
        self.audio.start_capture(device_id, source)
        if self.settings.auto_record_sessions:
            self.start_history({
                "source_type": source.value,
                "target_language": self.settings.translation_target_language,
                "translation_style": self.settings.translation_style,
                "provider": self.settings.translation_provider,
            })

    def _audio_segment(self, segment) -> None:
        if self.asr.enabled:
            self.asr.submit(segment)
        elif segment.is_final:
            self.window.set_asr_status("whisper.cpp 未配置，使用模拟识别演示")
            self.demo.start()

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
        self.save()
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
        )

    def refresh_model_state(self) -> None:
        """Report local ASR asset availability to the UI (also gates capture)."""
        from app.core import assets

        current = assets.status()
        self.window.set_model_state({
            "installed_models": current.installed_models,
            "ready": current.ready,
        })

    def download_model(self, key: str) -> None:
        asyncio.create_task(self._download_model_async(key))

    async def _download_model_async(self, key: str) -> None:
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
            await asyncio.to_thread(assets.download_model, key, progress)
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
            self.window.set_download_finished(True, f"模型 {key} 已就绪")
            self.window.set_asr_status(f"模型 {key} 校验通过，可开始识别")
        self.refresh_model_state()

    def configure_translation(self, payload=None) -> None:
        data = payload if isinstance(payload, dict) else {}
        self.settings.translation_target_language = data.get("target_language", self.settings.translation_target_language)
        self.settings.translation_style = data.get("style", self.settings.translation_style)
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

    def apply_translation_settings(self) -> None:
        plan = plan_from_settings(self.settings)
        missing = [item for item in plan if item["provider_id"] not in self.translation_registry.presets]
        plan = [item for item in plan if item["provider_id"] in self.translation_registry.presets]
        self.translation.set_language_pair(
            self.settings.translation_target_language,
            self.settings.translation_style,
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
        try:
            preset = self.translation_registry.get(provider_id)
            provider = self.translation_registry.create(
                provider_id,
                str(data.get("base_url", "") or ""),
                str(data.get("model", "") or ""),
            )
        except Exception as exc:
            self.window.set_translation_status(f"连接测试失败：{exc}")
            self.window.show_toast(f"连接测试失败：{exc}", ok=False)
            return

        if preset.requires_api_key and not os.environ.get(preset.api_key_environment, ""):
            message = f"未检测到密钥：请先在当前终端设置 {preset.api_key_environment}"
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

    def through(self) -> None:
        self.overlay.set_through(not self.overlay.through)
        self._state()

    def visible(self) -> None:
        self.visibility_manager.toggle()

    def recover(self) -> None:
        self.overlay.set_through(False)
        self.overlay.set_locked(False)
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
        asyncio.create_task(self._shutdown_async())

    async def _shutdown_async(self) -> None:
        self.player_tracker.stop()
        self.global_hotkeys.unregister_all()
        self.audio.close()
        self.translation.cancel_all()
        self.history.end_session()
        self.save()
        await self.asr.close()
        self.history.close()
        self.app.quit()


async def boot(runtime: Runtime) -> None:
    await runtime.initialize()
    runtime.show()
    QTimer.singleShot(500, runtime.show_startup_hint)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    runtime = Runtime(app)
    with loop:
        loop.create_task(boot(runtime))
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
