import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtCore import QEvent, QPoint, QSize, Qt  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings import Settings  # noqa: E402
from app.ui.overlay.window import LINE_BUDGET_DEBOUNCE_MS, OverlayWindow  # noqa: E402


@pytest.fixture
def overlay():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    window = OverlayWindow(Settings())
    window.show()
    QTest.qWait(30)
    yield window
    window.close()


def test_line_budget_is_measured_after_first_show(overlay):
    assert overlay.published_line_budget is not None
    assert 1 <= overlay.published_line_budget <= 8
    assert overlay.visible_lines() == overlay.published_line_budget


def test_bigger_translation_font_yields_fewer_lines(overlay):
    baseline = overlay.visible_lines()
    overlay.settings.translation_font = overlay.settings.translation_font + 22
    overlay.refresh()
    QTest.qWait(LINE_BUDGET_DEBOUNCE_MS + 60)
    assert overlay.visible_lines() < baseline or baseline == 1


def test_resize_emits_only_final_value(overlay):
    emitted = []
    overlay.line_budget_changed.connect(emitted.append)

    overlay.resize(QSize(640, 420))
    overlay.resize(QSize(640, 300))
    overlay.resize(QSize(640, 180))
    QTest.qWait(LINE_BUDGET_DEBOUNCE_MS + 60)
    QTest.qWait(LINE_BUDGET_DEBOUNCE_MS + 60)
    assert len(emitted) <= 1


def test_no_emit_when_budget_is_unchanged(overlay):
    emitted = []
    overlay.line_budget_changed.connect(emitted.append)
    overlay.publish_line_budget()
    overlay.publish_line_budget()
    assert emitted == []


def test_screen_change_recomputes(overlay):
    overlay.resize(QSize(640, 200))
    QTest.qWait(LINE_BUDGET_DEBOUNCE_MS + 60)
    overlay.move(QPoint(0, 0))
    overlay.changeEvent(QEvent(QEvent.Type.ScreenChangeInternal))
    QTest.qWait(LINE_BUDGET_DEBOUNCE_MS + 60)
    assert overlay.published_line_budget == overlay.visible_lines()
    assert overlay.visible_lines() >= 1


def test_set_layout_mode_swaps_arrangement(overlay):
    panel_layout = overlay.panel.layout()
    overlay.set_layout_mode("inline")
    assert overlay.appearance.subtitle_layout_mode == "inline"
    assert panel_layout.indexOf(overlay.inline_layout) >= 0
    assert panel_layout.indexOf(overlay.stacked_layout) < 0
    overlay.set_layout_mode("stacked")
    assert overlay.appearance.subtitle_layout_mode == "stacked"
    assert panel_layout.indexOf(overlay.stacked_layout) >= 0
    assert panel_layout.indexOf(overlay.inline_layout) < 0


def test_set_layout_mode_is_idempotent(overlay):
    overlay.set_layout_mode("stacked")
    overlay.set_layout_mode("stacked")
    assert overlay.appearance.subtitle_layout_mode == "stacked"
    assert overlay.panel.layout().indexOf(overlay.stacked_layout) >= 0


def test_topmost_flag_is_off_by_default(overlay):
    assert not (overlay.windowFlags() & Qt.WindowStaysOnTopHint)


def test_set_topmost_toggles_flag(overlay):
    overlay.set_topmost(True)
    assert overlay.windowFlags() & Qt.WindowStaysOnTopHint
    overlay.set_topmost(False)
    assert not (overlay.windowFlags() & Qt.WindowStaysOnTopHint)


def test_set_topmost_when_hidden_does_not_force_show():
    app = QApplication.instance() or QApplication([])
    window = OverlayWindow(Settings())
    assert not window.isVisible()
    window.set_topmost(True)
    assert not window.isVisible()
    assert window.windowFlags() & Qt.WindowStaysOnTopHint
    window.close()


def test_set_visible_toggles_hidden_state(overlay):
    overlay.set_visible(False)
    assert not overlay.isVisible()
    assert overlay._user_hidden is True
    overlay.set_visible(True)
    assert overlay.isVisible()
    assert overlay._user_hidden is False


def test_update_subtitle_respects_hidden(overlay):
    overlay.set_visible(False)
    value = SimpleNamespace(
        stable_source_text="", draft_source_text="",
        source_text="hello", translated_text="你好",
    )
    overlay.update_subtitle(value)
    assert not overlay.isVisible()  # hiding must not be overridden by incoming text



