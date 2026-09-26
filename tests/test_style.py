import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.events import EventBus  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.storage.appearance import (  # noqa: E402
    LAYOUT_DUAL_LINE, LAYOUT_SINGLE_ALTERNATE, OverlayAppearance,
    dim_color, parse_color, to_rgba_string,
)
from app.storage.database import Database  # noqa: E402
from app.storage.appearance import AppearanceRepository  # noqa: E402
from app.storage.fonts import list_font_families  # noqa: E402
from app.ui.overlay.style_manager import SubtitleStyleManager  # noqa: E402
from app.ui.overlay.window import OverlayWindow  # noqa: E402


@pytest.fixture
def app_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def styled_overlay(app_instance, tmp_path):
    bus = EventBus()
    manager = _manager(tmp_path, bus)
    overlay = OverlayWindow(Settings(), style_manager=manager, bus=bus)
    overlay.show()
    return overlay, manager, bus


def _manager(tmp_path, bus=None):
    bus = bus or EventBus()
    repo = AppearanceRepository(Database(tmp_path / "style.db"))
    return SubtitleStyleManager(bus, repo)


# -- colour helpers ---------------------------------------------------------
def test_parse_color_hex6():
    assert parse_color("#ff8800") == (255, 136, 0, 1.0)


def test_parse_color_hex8_alpha():
    r, g, b, a = parse_color("#ff880080")
    assert (r, g, b) == (255, 136, 0)
    assert abs(a - 128 / 255) < 1e-6


def test_parse_color_rgb_and_rgba():
    assert parse_color("rgb(10,20,30)") == (10, 20, 30, 1.0)
    assert parse_color("rgba(10,20,30,0.5)") == (10, 20, 30, 0.5)
    assert parse_color("rgba(10,20,30,128)") == (10, 20, 30, 128 / 255)


def test_parse_color_invalid_returns_default():
    assert parse_color("not-a-colour") == (255, 255, 255, 1.0)
    assert parse_color(None) == (255, 255, 255, 1.0)


def test_to_rgba_string_roundtrip():
    assert to_rgba_string(255, 136, 0, 0.5) == "rgba(255,136,0,0.5)"


def test_dim_color_scales_alpha():
    assert dim_color("rgba(255,0,0,1.0)", 0.6) == "rgba(255,0,0,0.6)"
    assert dim_color("#00ff00", 0.5) == "rgba(0,255,0,0.5)"


# -- manager ----------------------------------------------------------------
def test_manager_update_emits_and_persists(tmp_path):
    bus = EventBus()
    captured = []
    bus.subtitleStyleChanged.connect(captured.append)
    manager = _manager(tmp_path, bus)
    manager.update(subtitle_font_size=22)
    assert captured[-1].subtitle_font_size == 22
    manager._flush()
    reloaded = SubtitleStyleManager(bus, AppearanceRepository(Database(tmp_path / "style.db")))
    assert reloaded.get_style().subtitle_font_size == 22


def test_manager_theme_lock_persists(tmp_path):
    bus = EventBus()
    manager = _manager(tmp_path, bus)
    manager.update(subtitle_original_color="#123456")
    assert manager.get_style().subtitle_colors_customized is True
    manager._flush()
    reloaded = SubtitleStyleManager(bus, AppearanceRepository(Database(tmp_path / "style.db")))
    assert reloaded.get_style().subtitle_colors_customized is True


def test_manager_theme_defaults_respects_lock(tmp_path):
    bus = EventBus()
    manager = _manager(tmp_path, bus)
    manager.update(subtitle_bg_color="rgba(1,2,3,0.4)")  # locks colours
    before = manager.get_style().subtitle_bg_color
    manager.apply_theme_defaults(light=True)  # should be ignored (locked)
    assert manager.get_style().subtitle_bg_color == before


def test_manager_reset_colors_to_theme_unlocks(tmp_path):
    bus = EventBus()
    manager = _manager(tmp_path, bus)
    manager.update(subtitle_bg_color="rgba(1,2,3,0.4)")
    manager.reset_colors_to_theme(light=True)
    assert manager.get_style().subtitle_colors_customized is False
    assert manager.get_style().subtitle_bg_color.startswith("rgba(255")


def test_list_font_families_shape():
    common, mono = list_font_families()
    assert isinstance(common, list) and isinstance(mono, list)


# -- overlay wiring ---------------------------------------------------------
def test_overlay_renders_color_from_style(styled_overlay):
    overlay, manager, _ = styled_overlay
    manager.update(subtitle_translation_color="#ff0000")
    assert "rgba(255,0,0,1.0)" in overlay.translation.styleSheet()


def test_overlay_single_alternate_visibility_and_timer(styled_overlay):
    overlay, manager, _ = styled_overlay
    manager.update(subtitle_layout_mode=LAYOUT_SINGLE_ALTERNATE)
    # Initial flip shows the source line, hides the translation line.
    assert overlay.source.isVisible() is True
    assert overlay.translation.isVisible() is False
    assert overlay._alt_timer.isActive() is True
    overlay._toggle_alternation()
    assert overlay.source.isVisible() is False
    assert overlay.translation.isVisible() is True


def test_overlay_single_alternate_hover_pauses(styled_overlay):
    overlay, manager, _ = styled_overlay
    manager.update(subtitle_layout_mode=LAYOUT_SINGLE_ALTERNATE)
    overlay.enterEvent(QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0)))
    assert overlay._alt_paused is True
    assert overlay._alt_timer.isActive() is False
    overlay.leaveEvent(QEvent(QEvent.Leave))
    assert overlay._alt_paused is False
    assert overlay._alt_timer.isActive() is True


def test_overlay_dual_line_shows_both(styled_overlay):
    overlay, manager, _ = styled_overlay
    manager.update(subtitle_layout_mode=LAYOUT_DUAL_LINE)
    assert overlay.source.isVisible() is True
    assert overlay.translation.isVisible() is True
    assert overlay._alt_timer.isActive() is False


def test_overlay_legacy_construction_still_works():
    # When no manager is supplied the overlay falls back to a local appearance.
    app = QApplication.instance() or QApplication([])
    overlay = OverlayWindow(Settings())
    assert overlay.appearance.subtitle_layout_mode == LAYOUT_DUAL_LINE
