from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QBuffer, QByteArray, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.vision.ocr_engine import OCR_AVAILABLE, WindowsOcrEngine

DEFAULT_INTERVAL_MS = 1000

logger = logging.getLogger(__name__)


class RegionOcrService(QObject):
    """Grabs the framed region on a timer, OCRs it and emits stable text.

    The capture skips a frame whenever the text has not changed, so the
    translation pipeline is only woken for genuinely new content. The region frame
    is hidden for the duration of the grab so it never ends up in its own capture.
    """

    text_recognized = Signal(str)
    status_changed = Signal(str)

    def __init__(self, region_window, language_tag: str = "en-US") -> None:
        super().__init__()
        self.region_window = region_window
        self.engine = WindowsOcrEngine(language_tag)
        self.interval_ms = DEFAULT_INTERVAL_MS
        self._running = False
        self._busy = False
        self._last_text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        # Each capture hides the frame and shows it again. A hide in the middle of
        # a drag cancels the mouse press, so the frame must stop capturing while
        # the user is moving/resizing it. getattr keeps plain test doubles working.
        for name, slot in (
            ("interaction_started", self._pause_for_interaction),
            ("interaction_ended", self._resume_after_interaction),
        ):
            signal = getattr(self.region_window, name, None)
            if signal is not None:
                signal.connect(slot)

    # -- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if not OCR_AVAILABLE:
            self.status_changed.emit("OCR 后端不可用：未安装 winsdk")
            return
        if self._running:
            return
        self._running = True
        self._timer.start(self.interval_ms)
        self.status_changed.emit(
            f"区域识别已启动 · 每 {self.interval_ms // 1000 or 1}s · {self.engine.language_tag}"
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self.status_changed.emit("区域识别已停止")

    def _pause_for_interaction(self) -> None:
        """Stands the capture down while the frame is being dragged/resized."""
        if self._running and self._timer.isActive():
            self._timer.stop()

    def _resume_after_interaction(self) -> None:
        """Restarts capture once the user lets go of the frame."""
        if self._running and not self._timer.isActive():
            self._timer.start(self.interval_ms)

    def set_interval(self, seconds: float) -> None:
        """Sets the recognition period in seconds (clamped to a sane range)."""
        seconds = max(0.2, min(30.0, float(seconds)))
        self.interval_ms = int(seconds * 1000)
        self._timer.setInterval(self.interval_ms)

    def set_language(self, language_tag: str) -> None:
        self.engine.set_language(language_tag)

    # -- capture + recognise ---------------------------------------------
    def _grab_region(self, rect: QRect) -> bytes:
        """Screenshots ``rect`` as PNG bytes, hiding our own frame meanwhile."""
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        if screen is None:
            return b""
        origin = screen.geometry().topLeft()
        was_visible = self.region_window.isVisible()
        if was_visible:
            self.region_window.hide()
            QApplication.processEvents()
        try:
            pixmap = screen.grabWindow(
                0,
                rect.x() - origin.x(),
                rect.y() - origin.y(),
                rect.width(),
                rect.height(),
            )
        finally:
            if was_visible:
                self.region_window.show()
        if pixmap.isNull():
            return b""
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        image.save(buffer, "PNG")
        return bytes(QByteArray(buffer.data()))

    def _tick(self) -> None:
        """Queues one recognition pass; overlapping passes are skipped."""
        if self._busy or not self._running:
            return
        rect = self.region_window.region()
        if rect.width() < 8 or rect.height() < 8:
            return
        self._busy = True
        png = self._grab_region(rect)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:  # pragma: no cover - no loop in some test hosts
            self._busy = False
            return
        task = loop.create_task(self._recognize(png))
        task.add_done_callback(lambda _: setattr(self, "_busy", False))

    async def _recognize(self, png: bytes) -> None:
        try:
            text = await self.engine.recognize_png(png)
        except Exception as exc:  # noqa: BLE001 - never break the timer loop
            logger.error("区域 OCR 异常：%s", exc)
            return
        if text and text != self._last_text:
            self._last_text = text
            self.text_recognized.emit(text)
