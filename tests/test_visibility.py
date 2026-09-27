import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.events import EventBus  # noqa: E402
from app.storage.config_store import ConfigRepository  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.ui.overlay.visibility import SubtitleVisibilityManager  # noqa: E402


@pytest.fixture
def app_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(app_instance):
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    bus = EventBus()
    mgr = SubtitleVisibilityManager(bus, ConfigRepository(db))
    yield mgr, bus
    db.close()


def test_default_visible_true(manager):
    mgr, _bus = manager
    assert mgr.load() is True


def test_toggle_emits_and_tracks_state(manager):
    mgr, bus = manager
    events = []
    bus.subtitleVisibilityChanged.connect(events.append)
    mgr.set_visible(False)
    assert mgr.isVisible() is False
    assert events == [False]
    mgr.set_visible(False)  # no change -> no event
    assert events == [False]
    mgr.set_visible(True)
    assert events == [False, True]


def test_persist_is_debounced(manager):
    mgr, _bus = manager
    mgr.set_visible(False)
    # The write is debounced, so it must not hit the database immediately.
    assert mgr._config.get_bool("subtitle_visibility", True) is True
    QTest.qWait(350)
    assert mgr._config.get_bool("subtitle_visibility", True) is False


def test_toggle_debounce_ignores_rapid_press(manager):
    mgr, bus = manager
    events = []
    bus.subtitleVisibilityChanged.connect(events.append)
    mgr.toggle()
    mgr.toggle()  # within the 100ms debounce window -> ignored
    assert events == [False]
