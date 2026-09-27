from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class Sparkline(QWidget):
    """Mini latency chart. Pure QPainter, no chart dependency."""

    def __init__(self, values=None, colour: str = "#6ee7b7", warning: str = "#fbbf24") -> None:
        super().__init__()
        self.values: list[int] = list(values or [])
        self.colour = QColor(colour)
        self.warning = QColor(warning)
        self.setMinimumHeight(34)
        self.setMaximumHeight(34)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("最近若干次翻译延迟（毫秒）走势")

    def set_values(self, values) -> None:
        self.values = [int(value) for value in values]
        self.update()

    def set_colors(self, colour: str, warning: str) -> None:
        self.colour = QColor(colour)
        self.warning = QColor(warning)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if len(self.values) < 2:
            painter.setPen(QPen(QColor("#64748b"), 1, Qt.DashLine))
            painter.drawText(event.rect(), Qt.AlignCenter, "等待数据")
            return
        highest = max(max(self.values), 1)
        width = self.width() - 4
        height = self.height() - 6
        step = width / (len(self.values) - 1)
        points = []
        for index, value in enumerate(self.values):
            x = 2 + index * step
            y = 3 + height - (value / highest) * height
            points.append((x, y))
        slowest = self.values[-1] >= max(self.values) * 0.95 and max(self.values) > 0
        pen = QPen(self.warning if slowest else self.colour, 2)
        painter.setPen(pen)
        for start, end in zip(points, points[1:]):
            painter.drawLine(int(start[0]), int(start[1]), int(end[0]), int(end[1]))
        painter.setPen(QPen(pen.color(), 1))
        painter.drawText(event.rect().adjusted(0, 0, -4, 0), Qt.AlignRight | Qt.AlignTop, f"{max(self.values)} ms")


class MetricCard(QFrame):
    """One number with a caption, used by the translation status area."""

    def __init__(self, caption: str, value: str = "--", tooltip: str = "") -> None:
        super().__init__()
        self.setObjectName("metric")
        self.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("muted")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)
        if tooltip:
            self.setToolTip(tooltip)
            self.value_label.setToolTip(tooltip)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class MetricRow(QWidget):
    """Horizontal row of metric cards."""

    def __init__(self, captions: dict[str, str]) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.cards: dict[str, MetricCard] = {}
        for key, caption in captions.items():
            card = MetricCard(caption)
            self.cards[key] = card
            layout.addWidget(card)
        layout.addStretch()

    def set_value(self, key: str, value: str) -> None:
        card = self.cards.get(key)
        if card is not None:
            card.set_value(value)
