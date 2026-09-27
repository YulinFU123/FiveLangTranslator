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
from app.ui.overlay.topmost import WindowTopmostManager  # noqa: E402


@pytest.fixture
def repo():
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    yield ConfigRepository(db)
    db.close()


@pytest.fixture
def app_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(app_instance):
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    bus = EventBus()
    mgr = WindowTopmostManager(bus, ConfigRepository(db))
    yield mgr, bus
    db.close()


def test_config_default_is_false(repo):
    assert repo.get_bool("subtitle_always_on_top", False) is False


def test_config_roundtrip(repo):
    repo.set_bool("subtitle_always_on_top", True)
    assert repo.get_bool("subtitle_always_on_top", False) is True
    repo.set_bool("subtitle_always_on_top", False)
    assert repo.get_bool("subtitle_always_on_top", False) is False


def test_load_reads_persisted(manager):
    mgr, _bus = manager
    mgr._config.set_bool("subtitle_always_on_top", True)
    assert mgr.load() is True
    assert mgr.isTopmost is True


def test_toggle_emits_and_tracks_state(manager):
    mgr, bus = manager
    events = []
    bus.topmost_changed.connect(events.append)
    assert mgr.isTopmost is False
    mgr.set_topmost(True)
    assert mgr.isTopmost is True
    assert events == [True]
    mgr.set_topmost(True)  # no change -> no event
    assert events == [True]
    mgr.set_topmost(False)
    assert events == [True, False]
    assert mgr.isTopmost is False


def test_persist_is_debounced(manager):
    mgr, _bus = manager
    mgr.set_topmost(True)
    # The write is debounced, so it must not hit the database immediately.
    assert mgr._config.get_bool("subtitle_always_on_top", False) is False
    QTest.qWait(350)
    assert mgr._config.get_bool("subtitle_always_on_top", False) is True
