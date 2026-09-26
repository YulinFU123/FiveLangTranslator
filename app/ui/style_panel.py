from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QScreen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.storage.appearance import LAYOUT_DUAL_LINE, LAYOUT_SINGLE_ALTERNATE, parse_color, to_rgba_string
from app.storage.fonts import WEIGHT_LABELS, WEIGHT_LEVELS, list_font_families
from app.ui.overlay.style_manager import safe_family


def _nearest_weight(value: int) -> int:
    return min(WEIGHT_LEVELS, key=lambda level: abs(level - value))


class ColorField(QWidget):
    """A colour swatch + rgba readout that edits through ``QColorDialog`` (alpha)."""

    committed = Signal(str)

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = "#ffffff"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel(label))
        self.button = QPushButton()
        self.button.setFixedSize(46, 26)
        self.button.clicked.connect(self._pick)
        row.addWidget(self.button)
        self.readout = QLabel()
        self.readout.setObjectName("muted")
        row.addWidget(self.readout, 1)

    def set_color_string(self, value: str) -> None:
        self._value = value
        r, g, b, a = parse_color(value)
        self.button.setStyleSheet(
            f"background:rgba({r},{g},{b},{a});border:1px solid #888;border-radius:6px"
        )
        self.readout.setText(value)

    def _pick(self) -> None:
        initial = QColor()
        r, g, b, a = parse_color(self._value)
        initial.setRgb(r, g, b, int(a * 255))
        color = QColorDialog.getColor(
            initial, self, self._value, QColorDialog.ShowAlphaChannel
        )
        if color.isValid():
            self._commit(color)

    def _commit(self, color: QColor) -> None:
        rgba = to_rgba_string(color.red(), color.green(), color.blue(), color.alphaF())
        self.set_color_string(rgba)
        self.committed.emit(rgba)


# Recommended quick-pick colours for the subtitle box.
_RECOMMENDED = (
    "#ffffff", "#000000", "#cbd5e1", "#fbbf24", "#34d399",
    "#60a5fa", "#f87171", "#a78bfa",
)


class EyedropperOverlay(QWidget):
    """Full-screen transparent catcher that samples the pixel under the cursor."""

    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        geo = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(geo)
        self.setStyleSheet("background:rgba(0,0,0,1)")
        hint = QLabel("点击任意位置取色 · Esc 取消", self)
        hint.setStyleSheet(
            "color:#fff;background:rgba(0,0,0,140);padding:6px 10px;border-radius:6px"
        )
        hint.move(24, 24)

    def mousePressEvent(self, event) -> None:
        pos = QCursor.pos()
        screen: QScreen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        try:
            ratio = screen.devicePixelRatio()
            pixel = screen.grabWindow(
                0, int(pos.x() * ratio), int(pos.y() * ratio), 1, 1
            )
            color = pixel.toImage().pixelColor(0, 0)
            self.callback(color)
        except Exception:  # pragma: no cover - depends on the display stack
            pass
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()


class StylePanel(QWidget):
    """Font / colour / layout controls for the subtitle box, wired to the manager.

    Every edit flows through ``SubtitleStyleManager.update``, so the floating box
    previews instantly and the change is fanned out (and debounce-persisted) via
    the ``subtitleStyleChanged`` event. Subscribing to that same event keeps the
    controls in sync when the Windows theme switches the colour defaults.
    """

    def __init__(self, style_manager, bus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sm = style_manager
        self.bus = bus
        self._recent: list[str] = []
        self._active: ColorField | None = None
        self._build()
        bus.subtitleStyleChanged.connect(self.sync_from)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        self.show_source = QCheckBox("显示原文")
        self.show_source.setToolTip("是否显示识别原文 · 实时预览")
        self.show_source.toggled.connect(lambda checked: self.sm.update(show_source=checked))
        root.addWidget(self.show_source)

        family_row = QHBoxLayout()
        family_row.addWidget(QLabel("字体"))
        self.family = QComboBox()
        self.family.setMinimumWidth(220)
        self._populate_families()
        self.family.currentIndexChanged.connect(self._on_family)
        family_row.addWidget(self.family, 1)
        root.addLayout(family_row)

        weight_row = QHBoxLayout()
        weight_row.addWidget(QLabel("字重"))
        self.weight = QComboBox()
        for level in WEIGHT_LEVELS:
            self.weight.addItem(WEIGHT_LABELS[level], level)
        self.weight.currentIndexChanged.connect(self._on_weight)
        weight_row.addWidget(self.weight, 1)
        root.addLayout(weight_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("字号"))
        self.size = QSpinBox()
        self.size.setRange(8, 72)
        self.size.valueChanged.connect(
            lambda number: self.sm.update(subtitle_font_size=number)
        )
        size_row.addWidget(self.size)
        size_row.addStretch()
        root.addLayout(size_row)

        self.original = ColorField("原文颜色")
        self.translation = ColorField("译文颜色")
        self.bg = ColorField("译文底色")
        for field in (self.original, self.translation, self.bg):
            field.button.clicked.connect(lambda _=None, fld=field: self._set_active(fld))
            field.committed.connect(self._on_color_committed)
            root.addWidget(field)

        root.addWidget(self._build_swatches())
        root.addWidget(self._build_recent())

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("排列"))
        self.layout_mode = QComboBox()
        self.layout_mode.addItem("上下双行", LAYOUT_DUAL_LINE)
        self.layout_mode.addItem("单行交替", LAYOUT_SINGLE_ALTERNATE)
        self.layout_mode.setToolTip("单行交替：原文/译文每 3 秒轮换，鼠标悬停暂停")
        self.layout_mode.currentIndexChanged.connect(self._on_layout)
        layout_row.addWidget(self.layout_mode, 1)
        layout_row.addStretch()
        root.addLayout(layout_row)
        root.addStretch()

    def _populate_families(self) -> None:
        common, monospace = list_font_families()
        for family in sorted(common):
            self.family.addItem(family, family)
            self.family.setItemData(self.family.count() - 1, QFont(family), Qt.FontRole)
        if monospace:
            self.family.insertSeparator(self.family.count())
            for family in sorted(monospace):
                self.family.addItem("⟨等宽⟩ " + family, family)
                self.family.setItemData(self.family.count() - 1, QFont(family), Qt.FontRole)

    def _on_family(self, index: int) -> None:
        family = self.family.itemData(index)
        if family:
            self.sm.update(subtitle_font_family=family)

    def _on_weight(self, _index: int) -> None:
        self.sm.update(subtitle_font_weight=self.weight.currentData())

    def _on_layout(self, _index: int) -> None:
        self.sm.update(subtitle_layout_mode=self.layout_mode.currentData())

    def _set_active(self, field: ColorField) -> None:
        self._active = field

    def _key_for(self, field: ColorField) -> str:
        if field is self.original:
            return "subtitle_original_color"
        if field is self.translation:
            return "subtitle_translation_color"
        return "subtitle_bg_color"

    def _on_color_committed(self, value: str) -> None:
        sender = self.sender()
        key = self._key_for(sender) if isinstance(sender, ColorField) else "subtitle_bg_color"
        self._push_recent(value)
        self.sm.update(**{key: value})

    def _build_swatches(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        eyedropper = QPushButton("取色吸管")
        eyedropper.setToolTip("点击后在屏幕上任意位置吸取颜色")
        eyedropper.clicked.connect(self._start_eyedropper)
        row.addWidget(eyedropper)
        row.addWidget(QLabel("推荐"))
        for hex_color in _RECOMMENDED:
            button = QPushButton()
            button.setFixedSize(20, 20)
            button.setStyleSheet(
                f"background:{hex_color};border:1px solid #888;border-radius:4px"
            )
            button.clicked.connect(lambda _=None, c=hex_color: self._apply_swatch(c))
            row.addWidget(button)
        row.addStretch()
        return widget

    def _build_recent(self) -> QWidget:
        widget = QWidget()
        self.recent_layout = QHBoxLayout(widget)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(6)
        self.recent_layout.addWidget(QLabel("最近"))
        self._recent_container = QHBoxLayout()
        self.recent_layout.addLayout(self._recent_container)
        self.recent_layout.addStretch()
        self._rebuild_recent()
        return widget

    def _push_recent(self, value: str) -> None:
        if value in self._recent:
            self._recent.remove(value)
        self._recent.insert(0, value)
        self._recent = self._recent[:8]
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        container = self._recent_container
        while container.count():
            item = container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for value in self._recent:
            button = QPushButton()
            button.setFixedSize(20, 20)
            r, g, b, a = parse_color(value)
            button.setStyleSheet(
                f"background:rgba({r},{g},{b},{a});border:1px solid #888;border-radius:4px"
            )
            button.clicked.connect(lambda _=None, c=value: self._apply_swatch(c))
            container.addWidget(button)

    def _apply_swatch(self, value: str) -> None:
        target = self._active or self.original
        target.set_color_string(value)
        self._on_color_committed(value)

    def _start_eyedropper(self) -> None:
        self._eyedropper = EyedropperOverlay(self._on_eyedrop)
        self._eyedropper.show()

    def _on_eyedrop(self, color: QColor) -> None:
        rgba = to_rgba_string(color.red(), color.green(), color.blue(), color.alphaF())
        target = self._active or self.original
        target.set_color_string(rgba)
        self._on_color_committed(rgba)

    def sync_from(self, appearance) -> None:
        """Refreshes controls from the shared state without echoing back."""
        self.show_source.blockSignals(True)
        self.show_source.setChecked(appearance.show_source)
        self.show_source.blockSignals(False)

        index = self.family.findData(appearance.subtitle_font_family)
        if index < 0:
            index = self.family.findData(safe_family(appearance.subtitle_font_family))
        if index >= 0:
            self.family.blockSignals(True)
            self.family.setCurrentIndex(index)
            self.family.blockSignals(False)

        weight_index = self.weight.findData(appearance.subtitle_font_weight)
        if weight_index < 0:
            weight_index = self.weight.findData(_nearest_weight(appearance.subtitle_font_weight))
        if weight_index >= 0:
            self.weight.blockSignals(True)
            self.weight.setCurrentIndex(weight_index)
            self.weight.blockSignals(False)

        self.size.blockSignals(True)
        self.size.setValue(appearance.subtitle_font_size)
        self.size.blockSignals(False)

        self.original.set_color_string(appearance.subtitle_original_color)
        self.translation.set_color_string(appearance.subtitle_translation_color)
        self.bg.set_color_string(appearance.subtitle_bg_color)

        layout_index = self.layout_mode.findData(appearance.subtitle_layout_mode)
        if layout_index >= 0:
            self.layout_mode.blockSignals(True)
            self.layout_mode.setCurrentIndex(layout_index)
            self.layout_mode.blockSignals(False)
