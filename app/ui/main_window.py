from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.export.exporters import CONTENT_MODES, clock_timestamp as ms_to_clock
from app.translation.registry import LEGACY_ENDPOINT_FIELDS, PRESETS
from app.ui.metrics import MetricRow, Sparkline
from app.ui.overlay.anchor import ANCHOR_ORDER, ANCHOR_TIPS
from app.ui.style_panel import StylePanel
from app.ui.theme import current_tokens, set_theme_mode


class Toast(QLabel):
    """Transient message inside the control centre. Never blocks the overlay."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def pop(self, text: str, ok: bool = True, duration: int = 3000) -> None:
        self.setText(text)
        tokens = current_tokens()
        foreground = tokens["accent"] if ok else tokens["danger"]
        self.setStyleSheet(
            f"background:{tokens['card']};color:{foreground};"
            f"border:1px solid {tokens['card_border']};padding:9px 13px;border-radius:10px;font-weight:600"
        )
        self.adjustSize()
        parent = self.parent()
        self.move(24, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self.timer.start(duration)


class Card(QFrame):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 18, 20, 18)
        self.box.setSpacing(10)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:16px;font-weight:700")
        self.box.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("muted")
            description.setWordWrap(True)
            self.box.addWidget(description)


class MainWindow(QMainWindow):
    demo = Signal()
    edit = Signal()
    lock = Signal()
    through = Signal()
    visible = Signal()
    recover = Signal()
    changed = Signal()
    audio_start = Signal(object, object)
    audio_stop = Signal()
    audio_refresh = Signal()
    asr_apply = Signal(object)
    asr_benchmark = Signal(str)
    model_download = Signal(str, str)
    model_verify = Signal()
    translation_apply = Signal(object)
    translation_test = Signal(object)
    history_start = Signal(object)
    history_stop = Signal()
    history_select = Signal(str)
    history_refresh = Signal()
    history_delete = Signal(str)
    history_clear = Signal()
    history_search = Signal(object)
    export_requested = Signal(object)
    glossary_refresh = Signal()
    glossary_changed = Signal(dict)
    glossary_import = Signal(str)
    glossary_export = Signal(str)
    topmost_toggled = Signal(bool)
    anchor_selected = Signal(str)
    minimized_to_tray = Signal()

    def __init__(self, settings, overlay, style_manager=None, bus=None, preset_manager=None) -> None:
        super().__init__()
        self.settings = settings
        self.overlay = overlay
        self.style_manager = style_manager
        self.bus = bus
        self.preset_manager = preset_manager
        self.translation_dirty = False
        self.setWindowTitle("FiveLang Translator")
        self.resize(1120, 760)
        self.setMinimumSize(940, 650)
        self._build()
        self.sync()
        self.toast = Toast(self.centralWidget())
        self.toast.pop("控制中心已就绪", ok=True)

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(18)
        header = QHBoxLayout()
        text = QVBoxLayout()
        title = QLabel("实时翻译控制中心")
        title.setObjectName("title")
        subtitle = QLabel("音频检测、Vulkan 识别与悬浮字幕的统一工作台")
        subtitle.setObjectName("muted")
        text.addWidget(title)
        text.addWidget(subtitle)
        header.addLayout(text)
        header.addStretch()
        self.status_pill = QLabel("● v0.4.0 Alpha 2")
        self.status_pill.setStyleSheet("background:#123326;color:#6ee7b7;padding:8px 13px;border-radius:12px;font-weight:700")
        header.addWidget(self.status_pill)
        root.addLayout(header)
        tabs = QTabWidget()
        tabs.addTab(self._dashboard(), "概览")
        tabs.addTab(self._audio_page(), "音频与VAD")
        tabs.addTab(self._asr_page(), "本地识别")
        tabs.addTab(self._translation_page(), "翻译")
        tabs.addTab(self._glossary_page(), "术语表")
        tabs.addTab(self._history_page(), "历史与导出")
        tabs.addTab(self._style_page(), "字幕样式")
        root.addWidget(tabs, 1)
        self.statusBar().showMessage("准备就绪 · Ctrl+Shift+D 播放模拟字幕")

    def _dashboard(self):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setSpacing(16)
        mode = Card("运行模式", "真实识别不可用时自动保留模拟演示，不会阻塞字幕 UI。")
        self.run_mode = QComboBox()
        self.run_mode.addItems(["智能自动", "完全本地", "完全云端"])
        mode.box.addWidget(self.run_mode)
        demo = QPushButton("播放模拟实时字幕")
        demo.setObjectName("primary")
        demo.clicked.connect(self.demo)
        mode.box.addWidget(demo)
        grid.addWidget(mode, 0, 0)
        state = Card("实时状态")
        state_grid = QGridLayout()
        for row, pair in enumerate((("音频", "WASAPI / 麦克风"), ("ASR", "whisper.cpp / Mock"), ("方向", "五语 → 简体中文"))):
            state_grid.addWidget(QLabel(pair[0]), row, 0)
            state_grid.addWidget(QLabel(pair[1]), row, 1)
        state.box.addLayout(state_grid)
        grid.addWidget(state, 0, 1)
        controls = Card("字幕控制", "编辑时拖动内部区域，并从窗口边缘或四角缩放。")
        buttons = QGridLayout()
        self.edit_btn = QPushButton("进入编辑")
        self.lock_btn = QPushButton()
        self.through_btn = QPushButton()
        self.visible_btn = QPushButton()
        recover = QPushButton("紧急恢复")
        self.edit_btn.clicked.connect(self.edit)
        self.lock_btn.clicked.connect(self.lock)
        self.through_btn.clicked.connect(self.through)
        self.visible_btn.clicked.connect(self.visible)
        recover.clicked.connect(self.recover)
        for index, button in enumerate((self.edit_btn, self.lock_btn, self.through_btn, self.visible_btn, recover)):
            buttons.addWidget(button, index // 2, index % 2)
        controls.box.addLayout(buttons)
        grid.addWidget(controls, 1, 0, 1, 2)
        return page

    def _audio_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        input_card = Card("音频输入", "支持麦克风与 Windows WASAPI 系统声音，并自动监控设备变化。")
        row = QHBoxLayout()
        self.audio_kind = QComboBox()
        self.audio_kind.addItem("系统声音（WASAPI Loopback）", "system_loopback")
        self.audio_kind.addItem("麦克风", "microphone")
        self.audio_kind.currentIndexChanged.connect(lambda: self.audio_refresh.emit())
        self.audio_device = QComboBox()
        refresh = QPushButton("刷新设备")
        refresh.clicked.connect(self.audio_refresh)
        self.audio_refresh_button = refresh
        row.addWidget(QLabel("来源"))
        row.addWidget(self.audio_kind)
        row.addWidget(QLabel("设备"))
        row.addWidget(self.audio_device, 1)
        row.addWidget(refresh)
        input_card.box.addLayout(row)
        actions = QHBoxLayout()
        start = QPushButton("开始采集")
        start.setObjectName("primary")
        stop = QPushButton("停止采集")
        start.clicked.connect(lambda: self.audio_start.emit(self.audio_device.currentData(), self.audio_kind.currentData()))
        stop.clicked.connect(self.audio_stop)
        # 保留句柄：模型未就绪时需要整体置灰
        self.audio_start_button = start
        self.audio_stop_button = stop
        actions.addWidget(start)
        actions.addWidget(stop)
        actions.addStretch()
        input_card.box.addLayout(actions)
        layout.addWidget(input_card)
        live = Card("实时检测", "Silero ONNX 不可用时自动使用能量 VAD 兜底。")
        self.audio_status = QLabel("尚未启动")
        self.audio_status.setObjectName("muted")
        self.level_label = QLabel("音量  -100.0 dBFS")
        self.prob_label = QLabel("人声概率  0%")
        self.activity_label = QLabel("状态  no_audio")
        self.health_label = QLabel("健康  队列 0 · 丢包 0 · 重启 0")
        for label in (self.audio_status, self.level_label, self.prob_label, self.activity_label, self.health_label):
            live.box.addWidget(label)
        layout.addWidget(live)
        layout.addStretch()
        return page

    def _asr_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("whisper.cpp 本地识别", "选择 whisper-cli 和 GGML 模型。启用 GPU 时，由 Vulkan 构建版本负责在 AMD 显卡上运行。")
        self.asr_executable = QLineEdit(self.settings.whisper_executable)
        self.asr_model = QLineEdit(self.settings.whisper_model)
        for label, edit, picker in (("可执行文件", self.asr_executable, self._pick_executable), ("GGML模型", self.asr_model, self._pick_model)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(edit, 1)
            browse = QPushButton("浏览")
            browse.clicked.connect(picker)
            row.addWidget(browse)
            card.box.addLayout(row)
        backend_row = QHBoxLayout()
        self.asr_backend_mode = QComboBox()
        self.asr_backend_mode.addItem("自动：优先常驻Server", "auto")
        self.asr_backend_mode.addItem("常驻whisper-server", "server")
        self.asr_backend_mode.addItem("whisper-cli兼容模式", "cli")
        backend_index = self.asr_backend_mode.findData(self.settings.asr_backend)
        self.asr_backend_mode.setCurrentIndex(max(0, backend_index))
        self.asr_server = QLineEdit(self.settings.whisper_server_executable)
        self.asr_server.setPlaceholderText("whisper-server.exe，可留空自动发现")
        pick_server = QPushButton("浏览Server")
        pick_server.clicked.connect(self._pick_server)
        self.asr_server_port = QSpinBox()
        self.asr_server_port.setRange(1024, 65535)
        self.asr_server_port.setValue(self.settings.whisper_server_port)
        backend_row.addWidget(QLabel("后端"))
        backend_row.addWidget(self.asr_backend_mode)
        backend_row.addWidget(self.asr_server, 1)
        backend_row.addWidget(pick_server)
        backend_row.addWidget(QLabel("端口"))
        backend_row.addWidget(self.asr_server_port)
        card.box.addLayout(backend_row)
        options = QHBoxLayout()
        self.asr_language = QComboBox()
        for title, code in (("自动检测", "auto"), ("中文", "zh"), ("英语", "en"), ("日语", "ja"), ("俄语", "ru"), ("德语", "de")):
            self.asr_language.addItem(title, code)
        index = self.asr_language.findData(self.settings.asr_language)
        self.asr_language.setCurrentIndex(max(0, index))
        self.asr_gpu = QCheckBox("使用 GPU / Vulkan")
        self.asr_gpu.setChecked(self.settings.asr_use_gpu)
        self.asr_cpu_fallback = QCheckBox("GPU失败时自动回退CPU")
        self.asr_cpu_fallback.setChecked(self.settings.asr_cpu_fallback)
        self.asr_server_fallback = QCheckBox("Server失败时自动回退CLI")
        self.asr_server_fallback.setChecked(self.settings.whisper_server_fallback)
        options.addWidget(QLabel("语言"))
        options.addWidget(self.asr_language)
        options.addWidget(self.asr_gpu)
        options.addWidget(self.asr_cpu_fallback)
        options.addWidget(self.asr_server_fallback)
        options.addStretch()
        card.box.addLayout(options)
        apply_button = QPushButton("检测并应用识别后端")
        apply_button.setObjectName("primary")
        apply_button.clicked.connect(self._emit_asr_settings)
        card.box.addWidget(apply_button)
        benchrow=QHBoxLayout();self.benchmark_path=QLineEdit();self.benchmark_path.setPlaceholderText("选择16-bit PCM WAV进行真实性能测试");pickbench=QPushButton("选择测试音频");pickbench.clicked.connect(self._pick_benchmark);runbench=QPushButton("运行基准");runbench.clicked.connect(lambda:self.asr_benchmark.emit(self.benchmark_path.text().strip()));benchrow.addWidget(self.benchmark_path,1);benchrow.addWidget(pickbench);benchrow.addWidget(runbench);card.box.addLayout(benchrow)
        layout.addWidget(card)
        layout.addWidget(self._model_card())
        metrics = Card("识别状态")
        self.asr_status = QLabel("尚未检测 whisper.cpp")
        self.asr_status.setObjectName("muted")
        self.asr_metrics = QLabel("延迟  -- ms · 实时率 -- · 语言 --")
        self.asr_vad_label = QLabel("VAD 激活概率  --")
        self.asr_vad_label.setObjectName("muted")
        self.language_label = QLabel("语言锁定  尚未检测")
        self.transcript_label = QLabel("稳定文本  等待识别")
        self.transcript_label.setWordWrap(True)
        metrics.box.addWidget(self.asr_status)
        metrics.box.addWidget(self.asr_metrics)
        metrics.box.addWidget(self.asr_vad_label)
        metrics.box.addWidget(self.language_label)
        metrics.box.addWidget(self.transcript_label)
        layout.addWidget(metrics)
        layout.addStretch()
        return page

    def _model_card(self):
        """模型管理：规格选择、本地状态、下载进度与完整性校验。"""
        from app.core import assets

        card = Card(
            "模型管理",
            "选择规格后点击下载：将依次获取 GGML 模型、whisper.cpp 二进制与 VAD 模型。"
            "下载完成后会自动启用识别后端；未就绪时识别与采集功能不可用。",
        )
        self.model_spec = QComboBox()
        for spec in assets.MODELS:
            self.model_spec.addItem(f"{spec.key}（约 {spec.size_mb} MB · {spec.note}）", spec.key)
        self.download_source = QComboBox()
        self.download_source.addItem("官方 HuggingFace", "huggingface")
        self.download_source.addItem("国内镜像 hf-mirror", "mirror")
        source_index = self.download_source.findData(getattr(self.settings, "download_source", "huggingface"))
        self.download_source.setCurrentIndex(max(0, source_index))
        self.download_source.setToolTip("GGML 模型下载源；国内网络选镜像可显著提升速度")
        self.model_download_button = QPushButton("下载模型")
        self.model_download_button.setObjectName("primary")
        self.model_download_button.clicked.connect(
            lambda: self.model_download.emit(
                self.model_spec.currentData(), self.download_source.currentData()
            )
        )
        self.model_verify_button = QPushButton("重新校验完整性")
        self.model_verify_button.setToolTip("对本地资源做完整性校验，损坏的模型会被删除以便重新下载")
        self.model_verify_button.clicked.connect(lambda: self.model_verify.emit())
        row = QHBoxLayout()
        row.addWidget(QLabel("规格"))
        row.addWidget(self.model_spec, 1)
        row.addWidget(QLabel("下载源"))
        row.addWidget(self.download_source, 1)
        row.addWidget(self.model_download_button)
        row.addWidget(self.model_verify_button)
        card.box.addLayout(row)

        self.model_status = QLabel("检测中…")
        self.model_status.setObjectName("muted")
        self.model_components = QLabel("资源状态：检测中…")
        self.model_components.setObjectName("muted")
        self.model_components.setWordWrap(True)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 100)
        self.model_progress.setValue(0)
        self.model_progress.setVisible(False)
        self.model_progress_label = QLabel("")
        self.model_progress_label.setObjectName("muted")
        self.model_progress_label.setWordWrap(True)
        card.box.addWidget(self.model_status)
        card.box.addWidget(self.model_components)
        card.box.addWidget(self.model_progress)
        card.box.addWidget(self.model_progress_label)
        return card

    def set_model_state(self, data):
        """data: {'installed_models': tuple[str, ...], 'ready': bool}"""
        installed = tuple(data.get("installed_models") or ())
        if installed:
            self.model_status.setText(f"已下载：{'、'.join(installed)}")
        else:
            self.model_status.setText("未下载模型：请选择规格后点击「下载模型」")
        self.set_capture_enabled(bool(data.get("ready")))
        if not data.get("ready"):
            self.model_progress_label.setText("模型未就绪，音频采集与识别功能已禁用")

    def set_assets_detail(self, data):
        """Shows the per-component readiness of the three ASR assets.

        data: {'model': str, 'whisper': bool, 'vad': bool, 'ready': bool}
        """
        mark = lambda ok: "✓ 已就绪" if ok else "✗ 缺失"
        lines = [
            f"GGML 模型：{data.get('model') or '未下载'}",
            f"whisper.cpp 二进制：{mark(bool(data.get('whisper')))}",
            f"VAD 模型（Silero ONNX）：{mark(bool(data.get('vad')))}",
        ]
        self.model_components.setText("\n".join(lines))

    def set_asr_model_path(self, path: str) -> None:
        """Reflects the auto-discovered model path into the manual override field."""
        if path and not self.asr_model.text().strip():
            self.asr_model.setText(path)

    def set_model_status(self, text: str) -> None:
        self.model_status.setText(text)

    def set_capture_enabled(self, enabled: bool) -> None:
        """Gate audio capture controls until the ASR assets are ready."""
        for widget in (
            getattr(self, "audio_start_button", None),
            getattr(self, "audio_refresh_button", None),
            getattr(self, "audio_kind", None),
            getattr(self, "audio_device", None),
        ):
            if widget is not None:
                widget.setEnabled(bool(enabled))

    def set_download_progress(self, data):
        written = int(data.get("written") or 0)
        total = int(data.get("total") or 0)
        speed = float(data.get("speed") or 0.0)
        eta = data.get("eta")
        self.model_progress.setVisible(True)
        if total:
            self.model_progress.setValue(min(100, int(written * 100 / total)))
        text = f"{written / 1e6:.1f}"
        text += f" / {total / 1e6:.1f} MB" if total else " MB"
        text += f" · {speed / 1e6:.1f} MB/s"
        if eta is not None:
            text += f" · 剩余 {int(eta)} 秒"
        self.model_progress_label.setText(text)

    def set_download_finished(self, ok: bool, message: str = "") -> None:
        self.model_progress.setVisible(ok)
        self.model_progress_label.setText(message)

    def _translation_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("翻译Provider", "固定 4 个 Provider。DeepSeek 与豆包复用 OpenAI Compatible 适配层，API 密钥只从环境变量读取，不写入配置文件。")
        row = QHBoxLayout()
        self.translation_provider = QComboBox()
        for preset in PRESETS.values():
            self.translation_provider.addItem(preset.title, preset.provider_id)
        index = self.translation_provider.findData(self.settings.translation_provider)
        self.translation_provider.setCurrentIndex(max(0, index))
        self.translation_provider.setToolTip("选择要配置的 Provider；密钥来自各家对应的环境变量")
        self.translation_provider.currentIndexChanged.connect(self._provider_selected)
        self.translation_target = QComboBox()
        for title, code in (("简体中文", "zh"), ("英语", "en"), ("日语", "ja"), ("俄语", "ru"), ("德语", "de")):
            self.translation_target.addItem(title, code)
        self.translation_target.setCurrentIndex(max(0, self.translation_target.findData(self.settings.translation_target_language)))
        self.translation_target.setToolTip("目标语言 · 应用翻译设置后立即生效")
        self.translation_style = QComboBox()
        self.translation_style.addItem("影院精简", "cinema")
        self.translation_style.addItem("完整翻译", "complete")
        self.translation_style.addItem("直译", "literal")
        self.translation_style.setCurrentIndex(max(0, self.translation_style.findData(self.settings.translation_style)))
        self.translation_style.setToolTip("翻译风格 · 应用翻译设置后立即生效，并参与缓存键")
        row.addWidget(QLabel("Provider")); row.addWidget(self.translation_provider)
        row.addWidget(QLabel("目标语言")); row.addWidget(self.translation_target)
        row.addWidget(QLabel("风格")); row.addWidget(self.translation_style)
        card.box.addLayout(row)

        self.provider_endpoints = dict(getattr(self.settings, "translation_endpoints", None) or {})
        base_line = QHBoxLayout()
        self.provider_base_edit = QLineEdit()
        self.provider_base_edit.setPlaceholderText("服务地址，留空用预设默认值")
        self.provider_base_edit.textChanged.connect(self._provider_entry_edited)
        base_line.addWidget(QLabel("服务地址")); base_line.addWidget(self.provider_base_edit, 1)
        card.box.addLayout(base_line)
        model_line = QHBoxLayout()
        self.provider_model_label = QLabel("模型")
        self.provider_model_edit = QLineEdit()
        self.provider_model_edit.textChanged.connect(self._provider_entry_edited)
        model_line.addWidget(self.provider_model_label); model_line.addWidget(self.provider_model_edit, 1)
        card.box.addLayout(model_line)
        self.provider_note = QLabel("")
        self.provider_note.setObjectName("muted")
        self.provider_note.setWordWrap(True)
        self.provider_error = QLabel("")
        self.provider_error.setObjectName("muted")
        self.provider_error.setWordWrap(True)
        card.box.addWidget(self.provider_note)
        card.box.addWidget(self.provider_error)

        chain_card = Card("回退链顺序", "按顺序尝试：排在最前面的先应答，失败后依次往后。改动后点「应用翻译设置」生效。")
        list_row = QHBoxLayout()
        self.chain_list = QListWidget()
        self.chain_list.setToolTip("越靠前优先级越高")
        order_buttons = QVBoxLayout()
        up = QPushButton("上移"); up.clicked.connect(lambda: self._move_chain_item(-1))
        down = QPushButton("下移"); down.clicked.connect(lambda: self._move_chain_item(1))
        add = QPushButton("加入链"); add.clicked.connect(self._add_chain_item)
        remove = QPushButton("移除"); remove.clicked.connect(self._remove_chain_item)
        for button in (up, down, add, remove):
            order_buttons.addWidget(button)
        order_buttons.addStretch()
        list_row.addWidget(self.chain_list, 1); list_row.addLayout(order_buttons)
        chain_card.box.addLayout(list_row)
        self._reload_chain()
        layout.addWidget(chain_card)

        actions = QHBoxLayout()
        self.translation_apply_button = QPushButton("应用翻译设置")
        self.translation_apply_button.setObjectName("primary")
        self.translation_apply_button.setToolTip("把 Provider、地址/模型、回退链顺序写入配置并立即生效")
        self.translation_apply_button.clicked.connect(self._emit_translation_settings)
        apply_button = self.translation_apply_button
        self.translation_test_button = QPushButton("测试连接")
        self.translation_test_button.clicked.connect(self._emit_translation_test)
        actions.addWidget(apply_button); actions.addWidget(self.translation_test_button); actions.addStretch(); card.box.addLayout(actions)
        # Runs after the test button exists: validation toggles it.
        self._provider_selected()
        # Dirty tracking is connected after construction so the initial load
        # does not mark the page as changed.
        self.translation_provider.currentIndexChanged.connect(self._mark_translation_dirty)
        self.translation_target.currentIndexChanged.connect(self._mark_translation_dirty)
        self.translation_style.currentIndexChanged.connect(self._mark_translation_dirty)
        layout.addWidget(card)
        state = Card("翻译状态", "最近 50 次翻译的统计。延迟为翻译环节耗时，不含 ASR 识别。")
        self.translation_status = QLabel("尚未配置"); self.translation_status.setObjectName("muted")
        self.translation_metrics = MetricRow({
            "provider": "生效 Provider",
            "model": "实际模型",
            "p50": "P50 延迟",
            "p95": "P95 延迟",
            "hit": "缓存命中率",
        })
        self.latency_chart = Sparkline()
        state.box.addWidget(self.translation_status)
        state.box.addWidget(self.translation_metrics)
        state.box.addWidget(self.latency_chart)
        layout.addWidget(state); layout.addStretch(); return page

    def _glossary_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("术语表", "术语会进入翻译提示词，并参与缓存键计算：修改术语表后不会误用旧译文。")
        row = QHBoxLayout()
        self.glossary_term = QLineEdit()
        self.glossary_term.setPlaceholderText("原文，例如 Dark Lord")
        self.glossary_value = QLineEdit()
        self.glossary_value.setPlaceholderText("译文，例如 黑暗领主")
        add = QPushButton("添加 / 更新")
        add.setObjectName("primary")
        add.clicked.connect(self._add_glossary_row)
        row.addWidget(QLabel("原文")); row.addWidget(self.glossary_term, 1)
        row.addWidget(QLabel("译文")); row.addWidget(self.glossary_value, 1)
        row.addWidget(add)
        card.box.addLayout(row)
        self.glossary_table = QTableWidget(0, 2)
        self.glossary_table.setHorizontalHeaderLabels(["原文", "译文"])
        self.glossary_table.verticalHeader().setVisible(False)
        self.glossary_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.glossary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.glossary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.glossary_table.cellChanged.connect(self._glossary_cell_edited)
        card.box.addWidget(self.glossary_table, 1)
        actions = QHBoxLayout()
        apply_button = QPushButton("应用到翻译服务")
        apply_button.setObjectName("primary")
        apply_button.clicked.connect(self._emit_glossary)
        delete_button = QPushButton("删除选中")
        delete_button.clicked.connect(self._delete_glossary_rows)
        reload_button = QPushButton("重新载入")
        reload_button.clicked.connect(self.glossary_refresh)
        import_button = QPushButton("导入JSON")
        import_button.clicked.connect(self._import_glossary)
        export_button = QPushButton("导出JSON")
        export_button.clicked.connect(self._export_glossary)
        for button in (apply_button, delete_button, reload_button, import_button, export_button):
            actions.addWidget(button)
        actions.addStretch()
        card.box.addLayout(actions)
        self.glossary_status = QLabel("尚未载入术语表")
        self.glossary_status.setObjectName("muted")
        card.box.addWidget(self.glossary_status)
        layout.addWidget(card)
        return page

    def _add_glossary_row(self):
        term = self.glossary_term.text().strip()
        value = self.glossary_value.text().strip()
        if not term or not value:
            self.glossary_status.setText("原文与译文都不能为空"); return
        self._glossary_updating = True
        existing = self._find_glossary_row(term)
        if existing is None:
            index = self.glossary_table.rowCount()
            self.glossary_table.insertRow(index)
            self.glossary_table.setItem(index, 0, QTableWidgetItem(term))
            self.glossary_table.setItem(index, 1, QTableWidgetItem(value))
        else:
            self.glossary_table.setItem(existing, 1, QTableWidgetItem(value))
        self._glossary_updating = False
        self.glossary_term.clear(); self.glossary_value.clear()
        self._emit_glossary()

    def _text_at(self, table, row, column) -> str:
        item = table.item(row, column)
        return item.text() if item is not None else ""

    def _find_glossary_row(self, term):
        for row in range(self.glossary_table.rowCount()):
            item = self.glossary_table.item(row, 0)
            if item and item.text().strip() == term:
                return row
        return None

    def _delete_glossary_rows(self):
        for row in reversed(sorted({item.row() for item in self.glossary_table.selectedIndexes()})):
            self.glossary_table.removeRow(row)
        self._emit_glossary()

    def _glossary_cell_edited(self, row, column):
        if getattr(self, "_glossary_updating", False):
            return
        term = self._text_at(self.glossary_table, row, 0)
        value = self._text_at(self.glossary_table, row, 1)
        if not term.strip() or not value.strip():
            self.glossary_status.setText("原文与译文都不能为空，已忽略该行")
            return
        self._emit_glossary()

    def _collect_glossary(self) -> dict:
        entries = {}
        for row in range(self.glossary_table.rowCount()):
            term = self._text_at(self.glossary_table, row, 0).strip()
            value = self._text_at(self.glossary_table, row, 1).strip()
            if term and value:
                entries[term] = value
        return entries

    def _emit_glossary(self):
        self.glossary_changed.emit(self._collect_glossary())

    def _import_glossary(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入术语表", filter="JSON (*.json);;All files (*)")
        if path:
            self.glossary_import.emit(path)

    def _export_glossary(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出术语表", "glossary.json", filter="JSON (*.json)")
        if path:
            self.glossary_export.emit(path)

    def set_glossary_rows(self, rows):
        self._glossary_updating = True
        self.glossary_table.setRowCount(0)
        for entry in rows:
            index = self.glossary_table.rowCount()
            self.glossary_table.insertRow(index)
            self.glossary_table.setItem(index, 0, QTableWidgetItem(entry.term))
            self.glossary_table.setItem(index, 1, QTableWidgetItem(entry.translation))
        self._glossary_updating = False
        self.glossary_status.setText(f"已载入 {len(rows)} 条术语")

    def set_glossary_status(self, text):
        self.glossary_status.setText(text)
        self.statusBar().showMessage(text)

    def _history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        session_card = Card("会话记录", "开始记录后，STABLE / FINAL 字幕会写入本地 SQLite，草稿不会污染历史。模拟演示字幕同样会记录，方便在没有 whisper 时验证导出。")
        name_row = QHBoxLayout()
        self.session_name = QLineEdit()
        self.session_name.setPlaceholderText("会话名称，留空自动生成")
        self.history_start_btn = QPushButton("开始记录")
        self.history_start_btn.setObjectName("primary")
        self.history_start_btn.clicked.connect(self._start_history)
        self.history_stop_btn = QPushButton("停止记录")
        self.history_stop_btn.clicked.connect(self.history_stop)
        name_row.addWidget(QLabel("名称")); name_row.addWidget(self.session_name, 1)
        name_row.addWidget(self.history_start_btn); name_row.addWidget(self.history_stop_btn)
        session_card.box.addLayout(name_row)
        list_row = QHBoxLayout()
        self.session_list = QComboBox()
        self.session_list.currentIndexChanged.connect(self._select_history_session)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.history_refresh)
        delete_button = QPushButton("删除会话")
        delete_button.clicked.connect(self._delete_history_session)
        clear_button = QPushButton("清空全部历史")
        clear_button.clicked.connect(self.history_clear)
        list_row.addWidget(QLabel("会话")); list_row.addWidget(self.session_list, 1)
        list_row.addWidget(refresh); list_row.addWidget(delete_button); list_row.addWidget(clear_button)
        session_card.box.addLayout(list_row)
        self.storage_stats = QLabel("会话 -- · 片段 -- · 缓存 -- · 术语 --")
        self.storage_stats.setObjectName("muted")
        session_card.box.addWidget(self.storage_stats)
        layout.addWidget(session_card)

        table_card = Card("字幕片段", "双击表格可以完整查看当前会话的原文与译文。")
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["序号", "开始", "原文", "译文"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.setWordWrap(True)
        table_card.box.addWidget(self.history_table, 1)
        search_row = QHBoxLayout()
        self.history_search_input = QLineEdit()
        self.history_search_input.setPlaceholderText("搜索原文或译文")
        search = QPushButton("搜索")
        search.clicked.connect(self._emit_history_search)
        all_button = QPushButton("显示全部")
        all_button.clicked.connect(lambda: self.history_select.emit(self._current_history_session()))
        search_row.addWidget(self.history_search_input, 1); search_row.addWidget(search); search_row.addWidget(all_button)
        table_card.box.addLayout(search_row)
        layout.addWidget(table_card, 1)

        export_card = Card("导出字幕", "导出前不需要停止记录，当前会话已写入的片段都会参与导出。")
        format_row = QHBoxLayout()
        self.export_format = QComboBox()
        for fmt, label in (("srt", "SRT 字幕"), ("vtt", "WebVTT 字幕"), ("ass", "ASS 字幕"), ("txt", "TXT 纯文本"), ("json", "JSON 数据")):
            self.export_format.addItem(label, fmt)
        self.export_format.setCurrentIndex(max(0, self.export_format.findData(getattr(self.settings, "export_format", "srt"))))
        self.export_content = QComboBox()
        for key, label in CONTENT_MODES.items():
            self.export_content.addItem(label, key)
        self.export_content.setCurrentIndex(max(0, self.export_content.findData(getattr(self.settings, "export_content", "bilingual"))))
        export_button = QPushButton("导出当前会话")
        export_button.setObjectName("primary")
        export_button.clicked.connect(self._emit_export)
        format_row.addWidget(QLabel("格式")); format_row.addWidget(self.export_format)
        format_row.addWidget(QLabel("内容")); format_row.addWidget(self.export_content)
        format_row.addWidget(export_button); format_row.addStretch()
        export_card.box.addLayout(format_row)
        self.export_status = QLabel("尚未导出")
        self.export_status.setObjectName("muted")
        self.export_status.setWordWrap(True)
        export_card.box.addWidget(self.export_status)
        layout.addWidget(export_card)
        return page

    def _start_history(self):
        self.history_start.emit({
            "name": self.session_name.text().strip(),
            "target_language": self.translation_target.currentData(),
            "translation_style": self.translation_style.currentData(),
            "provider": self.translation_provider.currentData(),
        })

    def _select_history_session(self):
        if getattr(self, "_history_updating", False):
            return
        session_id = self.session_list.currentData()
        if session_id:
            self.history_select.emit(session_id)

    def _current_history_session(self) -> str:
        return self.session_list.currentData() or ""

    def _delete_history_session(self):
        session_id = self.session_list.currentData()
        if session_id:
            self.history_delete.emit(session_id)

    def _emit_history_search(self):
        self.history_search.emit({
            "query": self.history_search_input.text().strip(),
            "session_id": self.session_list.currentData() or None,
        })

    def _emit_export(self):
        self.settings.export_format = self.export_format.currentData()
        self.settings.export_content = self.export_content.currentData()
        self.export_requested.emit({
            "format": self.export_format.currentData(),
            "content": self.export_content.currentData(),
            "session_id": self.session_list.currentData() or None,
        })
        self.changed.emit()

    def set_history_sessions(self, sessions):
        self._history_updating = True
        current = self.session_list.currentData()
        self.session_list.clear()
        for session in sessions:
            stamp = datetime.fromtimestamp(session.started_at / 1000).strftime("%m-%d %H:%M")
            label = f"{'● ' if session.is_open else ''}{stamp} · {session.name} · {session.segment_count} 条"
            self.session_list.addItem(label, session.id)
        index = self.session_list.findData(current)
        if index < 0 and self.session_list.count():
            index = 0
        if index >= 0:
            self.session_list.setCurrentIndex(index)
        self._history_updating = False
        if self.session_list.count():
            self.history_select.emit(self.session_list.currentData())
        else:
            self.set_history_segments([])

    def set_history_segments(self, segments):
        self.history_table.setRowCount(0)
        for row, segment in enumerate(segments):
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.history_table.setItem(row, 1, QTableWidgetItem(f"{ms_to_clock(segment.start_ms)}"))
            source_item = QTableWidgetItem(segment.source_text)
            translated_item = QTableWidgetItem(segment.translated_text)
            for item in (source_item, translated_item):
                item.setTextAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.history_table.setItem(row, 2, source_item)
            self.history_table.setItem(row, 3, translated_item)
        self.history_table.resizeRowsToContents()

    def set_storage_stats(self, data):
        self.storage_stats.setText(
            f"会话 {data.get('sessions', 0)} · 片段 {data.get('segments', 0)} "
            f"· L1 内存 {data.get('memory_cache', 0)} · L2 SQLite {data.get('cache', 0)} "
            f"· 术语 {data.get('glossary', 0)} · 数据库 {data.get('path', '')}"
        )
        self.storage_stats.setToolTip("L1 是带 TTL 的内存 LRU，L2 是重启后仍然生效的 SQLite 持久缓存")

    def set_export_result(self, text):
        self.export_status.setText(text)
        self.statusBar().showMessage(text)

    def set_recording_state(self, recording: bool):
        self.history_start_btn.setText("记录中…" if recording else "开始记录")
        status = "正在记录" if recording else "记录已停止"
        self.statusBar().showMessage(f"历史记录 · {status}")

    # -- translation provider editing -------------------------------------
    def _current_preset(self):
        provider_id = self.translation_provider.currentData()
        return PRESETS.get(provider_id, PRESETS["ollama"])

    def _stored_endpoint(self, provider_id: str) -> tuple[str, str]:
        override = self.provider_endpoints.get(provider_id) or {}
        return str(override.get("base_url", "") or ""), str(override.get("model", "") or "")

    def _legacy_endpoint(self, provider_id: str) -> tuple[str, str]:
        fields = LEGACY_ENDPOINT_FIELDS.get(provider_id)
        if not fields:
            return "", ""
        return str(getattr(self.settings, fields[0], "") or ""), str(getattr(self.settings, fields[1], "") or "")

    def _provider_selected(self) -> None:
        preset = self._current_preset()
        base_url, model = self._stored_endpoint(preset.provider_id)
        if not base_url and not model:
            base_url, model = self._legacy_endpoint(preset.provider_id)
        self._loading_provider = True
        self.provider_base_edit.setText(base_url)
        self.provider_model_edit.setText(model)
        self._loading_provider = False
        self.provider_base_edit.setPlaceholderText(preset.base_url)
        self.provider_model_label.setText(preset.model_label)
        self.provider_model_edit.setPlaceholderText(preset.model_placeholder or preset.model)
        self.provider_model_edit.setToolTip(
            "火山方舟推理接入点 ID，形如 ep-20250102123456-abcde"
            if preset.requires_endpoint_id else "留空则使用预设默认模型"
        )
        self.provider_note.setText(preset.note or "")
        self._validate_provider_entry()

    def _mark_translation_dirty(self) -> None:
        """Highlights the apply button while edits are not committed."""
        self.translation_dirty = True
        self.translation_apply_button.setObjectName("dirty")
        self.translation_apply_button.setText("应用翻译设置 ●")
        self._refresh_widget_style(self.translation_apply_button)

    def _clear_translation_dirty(self) -> None:
        self.translation_dirty = False
        self.translation_apply_button.setObjectName("primary")
        self.translation_apply_button.setText("应用翻译设置")
        self._refresh_widget_style(self.translation_apply_button)

    @staticmethod
    def _refresh_widget_style(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def set_translation_testing(self, testing: bool) -> None:
        """Loading state for the connection test."""
        self.translation_test_button.setEnabled(not testing)
        self.translation_test_button.setText("测试中…" if testing else "测试连接")
        self.translation_test_button.setObjectName("primary" if testing else "")
        self._refresh_widget_style(self.translation_test_button)

    def _provider_entry_edited(self) -> None:
        if getattr(self, "_loading_provider", False):
            return
        self._mark_translation_dirty()
        preset = self._current_preset()
        entry = dict(self.provider_endpoints.get(preset.provider_id) or {})
        entry["base_url"] = self.provider_base_edit.text().strip()
        entry["model"] = self.provider_model_edit.text().strip()
        self.provider_endpoints[preset.provider_id] = entry
        self._validate_provider_entry()

    def _validate_provider_entry(self) -> str:
        preset = self._current_preset()
        problem = preset.validate(self.provider_base_edit.text().strip(), self.provider_model_edit.text().strip())
        invalid_style = "QLineEdit{border:1px solid #f87171;background:rgba(248,113,113,0.08);border-radius:6px;padding:5px 8px}"
        valid_style = ""
        self.provider_model_edit.setStyleSheet(invalid_style if problem else valid_style)
        self.provider_error.setText(problem)
        self.provider_error.setObjectName("muted")
        self.translation_test_button.setEnabled(not problem)
        self.translation_test_button.setToolTip(problem or "按当前 Provider 配置发送一次真实请求")
        return problem

    def _reload_chain(self) -> None:
        chain = [item for item in (getattr(self.settings, "translation_chain", None) or [])
                 if item in PRESETS]
        if not chain:
            chain = [self.settings.translation_provider, self.settings.translation_fallback_provider]
        self.chain_list.clear()
        for provider_id in chain:
            item = QListWidgetItem(PRESETS[provider_id].title)
            item.setData(Qt.UserRole, provider_id)
            self.chain_list.addItem(item)

    def _current_chain(self) -> list[str]:
        return [self.chain_list.item(row).data(Qt.UserRole) for row in range(self.chain_list.count())]

    def _move_chain_item(self, delta: int) -> None:
        row = self.chain_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.chain_list.count():
            return
        item = self.chain_list.takeItem(row)
        self.chain_list.insertItem(target, item)
        self.chain_list.setCurrentRow(target)
        self._mark_translation_dirty()

    def _add_chain_item(self) -> None:
        provider_id = self.translation_provider.currentData()
        if provider_id in self._current_chain():
            return
        item = QListWidgetItem(PRESETS[provider_id].title)
        item.setData(Qt.UserRole, provider_id)
        self.chain_list.addItem(item)
        self._mark_translation_dirty()

    def _remove_chain_item(self) -> None:
        row = self.chain_list.currentRow()
        if row >= 0:
            self.chain_list.takeItem(row)
            self._mark_translation_dirty()

    def _emit_translation_settings(self):
        self._clear_translation_dirty()
        self.translation_apply.emit({
            "provider": self.translation_provider.currentData(),
            "target_language": self.translation_target.currentData(),
            "style": self.translation_style.currentData(),
            "endpoints": dict(self.provider_endpoints),
            "chain": self._current_chain(),
        })

    def _emit_translation_test(self):
        problem = self._validate_provider_entry()
        if problem:
            self.show_toast(problem, ok=False)
            return
        self.translation_test.emit({
            "provider_id": self.translation_provider.currentData(),
            "base_url": self.provider_base_edit.text().strip(),
            "model": self.provider_model_edit.text().strip(),
        })

    def set_translation_status(self, text):
        self.translation_status.setText(text); self.statusBar().showMessage(text)

    def set_translation_metrics(self, data):
        provider = data.get("provider", "--")
        if data.get("cached"):
            cache_text = "内存命中" if data.get("cache_source") == "memory" else "SQLite命中"
        else:
            cache_text = "未命中"
        self.translation_metrics.set_value("provider", str(provider))
        self.translation_metrics.set_value("model", str(data.get("model", "--") or "--"))
        self.translation_metrics.set_value("p50", f"{data.get('p50_ms', 0)} ms")
        self.translation_metrics.set_value("p95", f"{data.get('p95_ms', 0)} ms")
        self.translation_metrics.set_value("hit", f"{data.get('cache_hit_rate', 0) * 100:.0f}%")
        self.translation_metrics.cards["hit"].setToolTip(
            f"最近 {data.get('count', 0)} 次 · 本次{cache_text}"
        )
        series = data.get("series") or []
        if series:
            self.latency_chart.set_values(series)
        self.statusBar().showMessage(
            f"翻译 {provider} · {data.get('latency_ms', 0)} ms · {cache_text}"
        )

    def _style_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card(
            "字幕视觉",
            "字体 / 颜色 / 排列均可实时预览并自动保存；颜色默认跟随 Windows 主题，手动修改后锁定。",
        )
        if self.style_manager is not None:
            self.style_panel = StylePanel(self.preset_manager, self.style_manager, self.bus)
            card.box.addWidget(self.style_panel)
            self.style_panel.sync_from(self.style_manager.get_style())
            self.show_source = self.style_panel.show_source
        else:  # pragma: no cover - only happens in legacy test harnesses
            note = QLabel("样式管理器未初始化")
            note.setObjectName("muted")
            card.box.addWidget(note)
            self.show_source = QCheckBox("显示原文")
            self.show_source.setToolTip("是否显示识别原文 · 实时预览")
            card.box.addWidget(self.show_source)
        self.topmost_checkbox = QCheckBox("窗口置顶")
        self.topmost_checkbox.setToolTip("字幕始终显示在所有窗口最前方")
        self.topmost_checkbox.clicked.connect(self._on_topmost_clicked)
        card.box.addWidget(self.topmost_checkbox)
        layout.addWidget(card)
        anchor_card = Card(
            "字幕位置（9 宫格锚点）",
            "点击格子把字幕快速对齐到屏幕工作区的对应位置，不影响大小、置顶与样式。",
        )
        anchor_grid = QGridLayout()
        anchor_grid.setSpacing(6)
        self.anchor_buttons = {}
        anchor_group = QButtonGroup(self)
        anchor_group.setExclusive(True)
        for anchor, row, column in ANCHOR_ORDER:
            button = QPushButton()
            button.setObjectName("anchorButton")
            button.setCheckable(True)
            button.setToolTip(ANCHOR_TIPS[anchor])
            button.setProperty("anchor", anchor)
            anchor_group.addButton(button)
            self.anchor_buttons[anchor] = button
            anchor_grid.addWidget(button, row, column)
        anchor_group.buttonClicked.connect(
            lambda button: self.anchor_selected.emit(button.property("anchor"))
        )
        anchor_card.box.addLayout(anchor_grid)
        layout.addWidget(anchor_card)
        player = Card("播放器跟随", "字幕可跟随当前前台播放器，并为窗口化与全屏分别保存布局。")
        self.follow_player = QCheckBox("跟随前台播放器窗口")
        self.follow_player.setChecked(self.settings.follow_player)
        self.hide_minimized = QCheckBox("播放器最小化时隐藏字幕")
        self.hide_minimized.setChecked(self.settings.hide_when_player_minimized)
        self.auto_fullscreen = QCheckBox("全屏切换时自动使用全屏布局")
        self.auto_fullscreen.setChecked(self.settings.auto_fullscreen_profile)
        for option in (self.follow_player, self.hide_minimized, self.auto_fullscreen):
            option.toggled.connect(self._apply_player_settings)
            player.box.addWidget(option)
        layout.addWidget(player)
        layout.addStretch()
        return page

    def _apply_player_settings(self):
        self.settings.follow_player = self.follow_player.isChecked()
        self.settings.hide_when_player_minimized = self.hide_minimized.isChecked()
        self.settings.auto_fullscreen_profile = self.auto_fullscreen.isChecked()
        self.changed.emit()

    def _pick_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 whisper-cli", filter="Executable (*.exe);;All files (*)")
        if path:
            self.asr_executable.setText(path)

    def _pick_server(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 whisper-server", filter="Executable (*.exe);;All files (*)")
        if path:self.asr_server.setText(path)

    def _pick_benchmark(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择测试音频", filter="WAV audio (*.wav)")
        if path:self.benchmark_path.setText(path)

    def _pick_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 GGML 模型", filter="GGML model (*.bin);;All files (*)")
        if path:
            self.asr_model.setText(path)

    def _emit_asr_settings(self):
        payload = {
            "executable": self.asr_executable.text().strip(),
            "model": self.asr_model.text().strip(),
            "language": self.asr_language.currentData(),
            "use_gpu": self.asr_gpu.isChecked(),
            "cpu_fallback": self.asr_cpu_fallback.isChecked(),
            "backend_mode": self.asr_backend_mode.currentData(),
            "server_executable": self.asr_server.text().strip(),
            "server_port": self.asr_server_port.value(),
            "server_fallback": self.asr_server_fallback.isChecked(),
        }
        self.asr_apply.emit(payload)

    def set_audio_devices(self, devices):
        current = self.audio_device.currentData()
        self.audio_device.clear()
        kind = self.audio_kind.currentData()
        for device in devices:
            if device.source_kind.value == kind:
                self.audio_device.addItem(("★ " if device.is_default else "") + device.name, device.device_id)
        index = self.audio_device.findData(current)
        if index >= 0:
            self.audio_device.setCurrentIndex(index)

    def set_audio_status(self, text):
        self.audio_status.setText(text)
        self.statusBar().showMessage(text)

    def set_audio_level(self, value):
        self.level_label.setText(f"音量  {value:.1f} dBFS")

    def set_vad_probability(self, value):
        self.prob_label.setText(f"人声概率  {value * 100:.0f}%")
        # 识别页同步显示，便于验证端点检测灵敏度与响应延迟
        percent = float(value) * 100
        self.asr_vad_label.setText(f"VAD 激活概率  {percent:.0f}%")

    def set_audio_activity(self, value):
        self.activity_label.setText(f"状态  {value}")

    def set_audio_health(self, data):
        self.health_label.setText(
            f"健康  队列 {data.get('queue_depth', 0)} · 丢包 {data.get('packets_dropped', 0)} "
            f"· 草稿 {data.get('draft_segments', 0)} · 完整 {data.get('final_segments', 0)} · 重启 {data.get('restart_count', 0)}"
        )

    def set_benchmark_result(self,data):
        self.set_asr_status(f"基准完成 · {data.get('latency_ms',0)} ms · {data.get('realtime_factor',0):.2f}x · {data.get('language','--')}")

    def set_asr_status(self, text):
        self.asr_status.setText(text)
        self.statusBar().showMessage(text)

    def set_language_state(self, data):
        state = "已锁定" if data.get("locked") else "检测中"
        self.language_label.setText(f"语言锁定  {state} · {data.get('detected', 'auto')} · 置信 {data.get('confidence', 0)*100:.0f}%")

    def set_transcript_state(self, data):
        committed = data.get("committed", "")
        draft = data.get("draft", "")
        self.transcript_label.setText(f"稳定文本  {committed}｜{draft}")

    def set_asr_metrics(self, data):
        self.asr_metrics.setText(
            f"延迟  {data.get('latency_ms', 0)} ms · 实时率 {data.get('realtime_factor', 0):.2f}x "
            f"· 状态 {data.get('status', '--')} · 语言 {data.get('language', '--')} · 队列 {data.get('queue_depth', 0)}"
        )

    def _on_topmost_clicked(self, checked: bool) -> None:
        self.topmost_toggled.emit(checked)

    def set_topmost_state(self, enabled: bool) -> None:
        """Syncs the settings-panel switch from the shared state (no echo)."""
        self.topmost_checkbox.setChecked(bool(enabled))

    def set_anchor_state(self, anchor: str) -> None:
        """Syncs the 9-grid picker highlight from the shared state (no echo)."""
        for key, button in self.anchor_buttons.items():
            button.setChecked(key == anchor)

    def closeEvent(self, event) -> None:
        """Closing the control centre collapses it into the tray."""
        event.ignore()
        self.hide()
        self.minimized_to_tray.emit()

    def show_toast(self, text: str, ok: bool = True, duration: int = 3000) -> None:
        """Shows a transient message. Used instead of modal dialogs."""
        self.toast.pop(text, ok, duration)

    def apply_theme_mode(self, light: bool) -> None:
        """Keeps ad-hoc widgets in sync with the active Windows theme."""
        set_theme_mode(light)
        tokens = current_tokens()
        self.latency_chart.set_colors(tokens["accent"], tokens["warn"])
        self.status_pill.setStyleSheet(
            f"background:{tokens['card']};color:{tokens['accent']};"
            "padding:8px 13px;border-radius:12px;font-weight:700"
        )

    def sync(self):
        self.lock_btn.setText("解除锁定" if self.overlay.locked else "锁定位置")
        self.through_btn.setText("关闭穿透" if self.overlay.through else "开启穿透")
        self.set_visibility_state(self.overlay.isVisible())

    def set_visibility_state(self, visible: bool) -> None:
        """Syncs the show/hide control from the shared state (no echo)."""
        self.visible_btn.setText("隐藏字幕" if visible else "显示字幕")
