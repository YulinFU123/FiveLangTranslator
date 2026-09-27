"""Qt tests for the P6 UI work. Skipped when PySide6 is unavailable."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for widget tests")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings import Settings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


class FakeOverlay:
    locked = False
    through = False
    fullscreen_mode = False
    player_rect = None

    def isVisible(self):
        return True

    def refresh(self):
        return None

    def save_profile(self):
        return None


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    main = MainWindow(Settings(), FakeOverlay())
    main.show()
    yield main
    main.close()


def test_metric_cards_are_filled_from_metrics(window):
    window.set_translation_metrics({
        "provider": "deepseek", "model": "deepseek-chat",
        "p50_ms": 210, "p95_ms": 640, "cache_hit_rate": 0.4,
        "count": 10, "series": [100, 200, 300, 400], "cached": False,
    })
    assert window.translation_metrics.cards["provider"].value_label.text() == "deepseek"
    assert window.translation_metrics.cards["model"].value_label.text() == "deepseek-chat"
    assert window.translation_metrics.cards["p50"].value_label.text() == "210 ms"
    assert window.translation_metrics.cards["p95"].value_label.text() == "640 ms"
    assert window.translation_metrics.cards["hit"].value_label.text() == "40%"
    assert window.latency_chart.values == [100, 200, 300, 400]


def test_unapplied_changes_highlight_the_apply_button(window):
    assert window.translation_dirty is False
    window.translation_target.setCurrentIndex(1)
    assert window.translation_dirty is True
    assert window.translation_apply_button.objectName() == "dirty"
    assert "●" in window.translation_apply_button.text()

    window._emit_translation_settings()
    assert window.translation_dirty is False
    assert window.translation_apply_button.objectName() == "primary"


def test_test_button_shows_a_loading_state(window):
    window.set_translation_testing(True)
    assert window.translation_test_button.text() == "测试中…"
    assert window.translation_test_button.isEnabled() is False
    window.set_translation_testing(False)
    assert window.translation_test_button.text() == "测试连接"
    assert window.translation_test_button.isEnabled() is True


def test_toast_reports_failures_without_blocking(window):
    window.show_toast("翻译失败 · HTTP 429", ok=False)
    assert "HTTP 429" in window.toast.text()
    assert window.toast.timer.isActive() is True


def test_closing_collapses_into_the_tray(window):
    emitted = []
    window.minimized_to_tray.connect(lambda: emitted.append(True))
    window.close()
    assert emitted == [True]
    assert window.isVisible() is False


def test_theme_mode_updates_ad_hoc_widgets(window):
    window.apply_theme_mode(True)
    assert window.latency_chart.colour.name() == "#059669"
    window.apply_theme_mode(False)
    assert window.latency_chart.colour.name() == "#6ee7b7"


def test_settings_widgets_carry_tooltips(window):
    assert window.translation_target.toolTip()
    assert window.translation_style.toolTip()
    assert window.translation_apply_button.toolTip()
    assert window.show_source.toolTip()
