import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtWidgets import QApplication  # noqa: E402

from dataclasses import asdict  # noqa: E402

from app.core.events import EventBus  # noqa: E402
from app.storage.appearance import AppearanceRepository, OverlayAppearance  # noqa: E402
from app.storage.config_store import ConfigRepository  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.presets import (  # noqa: E402
    CUSTOM_PRESET_ID, SUBTITLE_PRESETS, ACTIVE_PRESET_KEY,
)
from app.ui.overlay.style_manager import SubtitleStyleManager  # noqa: E402
from app.ui.overlay.anchor import SubtitleAnchorManager  # noqa: E402
from app.ui.overlay.preset_manager import SubtitlePresetManager  # noqa: E402
from app.ui.overlay.topmost import WindowTopmostManager  # noqa: E402


@pytest.fixture
def app_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def preset_env(app_instance, tmp_path):
    bus = EventBus()
    db = Database(tmp_path / "presets.db")
    appearance_repo = AppearanceRepository(db)
    config = ConfigRepository(db)
    style_manager = SubtitleStyleManager(bus, appearance_repo)
    anchor_manager = SubtitleAnchorManager(bus, config)
    topmost_manager = WindowTopmostManager(bus, config)
    manager = SubtitlePresetManager(
        bus, style_manager, anchor_manager, topmost_manager, config,
    )
    return bus, config, style_manager, anchor_manager, topmost_manager, manager


def test_preset_specs_match_spec_table():
    cinema = SUBTITLE_PRESETS["cinema"]
    meeting = SUBTITLE_PRESETS["meeting"]
    reading = SUBTITLE_PRESETS["reading"]
    assert cinema.style["subtitle_font_weight"] == 700
    assert cinema.style["subtitle_font_size"] == 20
    assert cinema.style["subtitle_translation_color"].upper() == "#FFD700"
    assert cinema.anchor == "bottom-center" and cinema.topmost is True
    assert meeting.style["subtitle_font_weight"] == 400
    assert meeting.style["subtitle_font_size"] == 16
    assert meeting.anchor == "top-center" and meeting.topmost is True
    assert reading.style["subtitle_font_family"] == "Consolas"
    assert reading.style["subtitle_font_size"] == 18
    assert reading.anchor == "bottom-left" and reading.topmost is False


def test_apply_cinema_sets_everything(preset_env):
    _bus, _config, sm, am, tm, manager = preset_env
    manager.apply("cinema")
    assert sm.get_style().subtitle_font_size == 20
    assert sm.get_style().subtitle_translation_color.upper() == "#FFD700"
    assert am.getAnchor() == "bottom-center"
    assert tm.isTopmost is True
    assert manager.current() == "cinema"


def test_apply_persists_active_preset_key(preset_env, tmp_path):
    _bus, config, _sm, _am, _tm, manager = preset_env
    manager.apply("meeting")
    assert config.get_string(ACTIVE_PRESET_KEY, "") == "meeting"
    # A freshly built manager reads the persisted preset back.
    bus2 = EventBus()
    db2 = Database(tmp_path / "presets.db")
    reloaded = SubtitlePresetManager(
        bus2, SubtitleStyleManager(bus2, AppearanceRepository(db2)),
        SubtitleAnchorManager(bus2, ConfigRepository(db2)),
        WindowTopmostManager(bus2, ConfigRepository(db2)),
        ConfigRepository(db2),
    )
    assert reloaded.current() == "meeting"


def test_manual_edit_after_preset_marks_custom(preset_env):
    _bus, _config, sm, _am, _tm, manager = preset_env
    manager.apply("cinema")
    received = []
    manager.presetApplied.connect(received.append)
    sm.update(subtitle_font_size=22)  # manual deviation
    assert manager.current() == CUSTOM_PRESET_ID
    assert received and received[-1] == CUSTOM_PRESET_ID


def test_manual_anchor_edit_marks_custom(preset_env):
    _bus, _config, _sm, am, _tm, manager = preset_env
    manager.apply("cinema")
    am.setAnchor("top-left")  # deviates from cinema's bottom-center
    assert manager.current() == CUSTOM_PRESET_ID


def test_manual_topmost_edit_marks_custom(preset_env):
    _bus, _config, _sm, _am, tm, manager = preset_env
    manager.apply("cinema")
    tm.set_topmost(False)  # deviates from cinema's on
    assert manager.current() == CUSTOM_PRESET_ID


def test_unknown_preset_is_ignored(preset_env):
    _bus, _config, _sm, _am, _tm, manager = preset_env
    before = OverlayAppearance(**asdict(_sm.get_style()))
    before_anchor = _am.getAnchor()
    before_topmost = _tm.isTopmost
    manager.apply("does-not-exist")
    assert manager.current() == CUSTOM_PRESET_ID
    after = _sm.get_style()
    assert after.subtitle_font_size == before.subtitle_font_size
    assert after.subtitle_original_color == before.subtitle_original_color
    assert _am.getAnchor() == before_anchor
    assert _tm.isTopmost == before_topmost


def test_apply_emits_presetApplied(preset_env):
    _bus, _config, _sm, _am, _tm, manager = preset_env
    received = []
    manager.presetApplied.connect(received.append)
    manager.apply("reading")
    assert "reading" in received
