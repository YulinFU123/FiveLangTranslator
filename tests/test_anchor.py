import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.events import EventBus  # noqa: E402
from app.player.layout import Rect, anchor_rect  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.storage.config_store import ConfigRepository  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.ui.overlay.anchor import (  # noqa: E402
    ANCHOR_MARGIN,
    ANCHOR_ORDER,
    DEFAULT_ANCHOR,
    SubtitleAnchorManager,
    nearest_anchor,
)
from app.ui.overlay.window import OverlayWindow  # noqa: E402


@pytest.fixture
def app_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(app_instance):
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    bus = EventBus()
    mgr = SubtitleAnchorManager(bus, ConfigRepository(db))
    yield mgr, bus
    db.close()


@pytest.fixture
def overlay(app_instance):
    window = OverlayWindow(Settings())
    window.show()
    yield window
    window.close()


def test_default_anchor_is_bottom_center(manager):
    mgr, _bus = manager
    assert mgr.load() == DEFAULT_ANCHOR
    assert mgr.used_default() is True


def test_load_invalid_falls_back_to_default(manager):
    mgr, _bus = manager
    mgr._config.set_string("subtitle_anchor_point", "nonsense")
    assert mgr.load() == DEFAULT_ANCHOR
    assert mgr.used_default() is True


def test_set_anchor_emits_and_tracks(manager):
    mgr, bus = manager
    events = []
    bus.subtitleAnchorChanged.connect(events.append)
    mgr.setAnchor("top-right")
    assert mgr.getAnchor() == "top-right"
    assert events == ["top-right"]
    mgr.setAnchor("top-right")  # no change -> no event
    assert events == ["top-right"]
    mgr.setAnchor("not-a-real-anchor")  # invalid -> ignored
    assert mgr.getAnchor() == "top-right"
    mgr.setAnchor("center")
    assert events == ["top-right", "center"]


def test_persist_is_debounced(manager):
    mgr, _bus = manager
    mgr.setAnchor("bottom-left")
    assert mgr._config.get_string("subtitle_anchor_point", "") == ""
    QTest.qWait(350)
    assert mgr._config.get_string("subtitle_anchor_point", "") == "bottom-left"


def test_apply_anchor_repositions_window(overlay):
    overlay.hide()
    overlay.apply_anchor("top-left")
    screen = overlay._screen_for_center()
    available = screen.availableGeometry()
    expected = anchor_rect(
        "tl",
        Rect(available.left(), available.top(), available.width(), available.height()),
        overlay.width(), overlay.height(), ANCHOR_MARGIN,
    )
    got = overlay.geometry()
    assert got.left() == expected.left
    assert got.top() == expected.top
    assert got.width() == expected.width
    assert got.height() == expected.height


def test_apply_anchor_keeps_size(overlay):
    overlay.hide()
    original = overlay.geometry()
    overlay.apply_anchor("bottom-center")
    assert overlay.width() == original.width()
    assert overlay.height() == original.height()


def test_apply_anchor_bottom_center_centered(overlay):
    overlay.hide()
    overlay.apply_anchor("bottom-center")
    screen = overlay._screen_for_center()
    available = screen.availableGeometry()
    expected = anchor_rect(
        "bc",
        Rect(available.left(), available.top(), available.width(), available.height()),
        overlay.width(), overlay.height(), ANCHOR_MARGIN,
    )
    got = overlay.geometry()
    # horizontally centred within the work area
    assert got.left() == expected.left
    assert got.top() == expected.top


def test_nearest_anchor_picks_closest():
    screen = Rect(0, 0, 1000, 800)
    width, height = 400, 200
    for anchor, _row, _col in ANCHOR_ORDER:
        target = anchor_rect(
            {"top-left": "tl", "top-center": "tc", "top-right": "tr",
             "middle-left": "ml", "center": "mc", "middle-right": "mr",
             "bottom-left": "bl", "bottom-center": "bc", "bottom-right": "br"}[anchor],
            screen, width, height, ANCHOR_MARGIN,
        )
        cx = target.left + width / 2.0
        cy = target.top + height / 2.0
        assert nearest_anchor(cx, cy, screen, width, height) == anchor


def test_nearest_anchor_tiebreak_prefers_bottom_center():
    screen = Rect(0, 0, 1000, 800)
    width, height = 400, 200
    # Midpoint between bottom-left (x=216) and bottom-center (x=500) -> equidistant.
    midpoint_x = (216 + 500) / 2.0
    bottom_y = screen.height - ANCHOR_MARGIN - height / 2.0
    assert nearest_anchor(midpoint_x, bottom_y, screen, width, height) == "bottom-center"


def test_snap_to_nearest_anchor_emits(overlay):
    events = []
    overlay.anchor_requested.connect(events.append)
    overlay.snap_to_nearest_anchor()
    assert len(events) == 1
    screen = overlay._screen_for_center()
    available = screen.availableGeometry()
    expected = nearest_anchor(
        overlay.x() + overlay.width() / 2.0,
        overlay.y() + overlay.height() / 2.0,
        Rect(available.left(), available.top(), available.width(), available.height()),
        overlay.width(), overlay.height(),
    )
    assert events[0] == expected


def _double_click(overlay, x, y):
    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(x, y),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )
    overlay.mouseDoubleClickEvent(event)


def test_double_click_background_snaps(overlay):
    events = []
    overlay.anchor_requested.connect(events.append)
    _double_click(overlay, 5, 5)
    assert len(events) == 1


def test_double_click_text_area_ignored(overlay):
    events = []
    overlay.anchor_requested.connect(events.append)
    center = overlay.source.geometry().center()
    assert overlay.source.geometry().width() > 0  # layout actually placed the label
    _double_click(overlay, center.x(), center.y())
    assert events == []

