"""Qt dependent translation page tests. Skipped when PySide6 is unavailable."""

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
    settings = Settings()
    settings.translation_chain = ["ollama", "openai_compatible"]
    main = MainWindow(settings, FakeOverlay())
    main.show()
    yield main
    main.close()


def test_dropdown_offers_four_providers(window):
    ids = [window.translation_provider.itemData(index) for index in range(window.translation_provider.count())]
    assert ids == ["ollama", "openai_compatible", "deepseek", "doubao"]


def test_selecting_doubao_switches_the_model_slot_to_endpoint_id(window):
    window.translation_provider.setCurrentIndex(window.translation_provider.findData("doubao"))
    assert window.provider_model_label.text() == "Endpoint ID"
    assert "ep-" in window.provider_model_edit.placeholderText()


def test_doubao_blocks_testing_without_endpoint_id(window):
    window.translation_provider.setCurrentIndex(window.translation_provider.findData("doubao"))
    window.provider_model_edit.setText("")
    assert "endpoint id" in window.provider_error.text()
    assert window.translation_test_button.isEnabled() is False

    window.provider_model_edit.setText("gpt-4o")
    assert "格式不正确" in window.provider_error.text()
    assert window.translation_test_button.isEnabled() is False

    window.provider_model_edit.setText("ep-20250102123456-abcde")
    assert window.provider_error.text() == ""
    assert window.translation_test_button.isEnabled() is True


def test_invalid_entry_shows_a_toast_instead_of_calling_the_provider(window):
    window.translation_provider.setCurrentIndex(window.translation_provider.findData("doubao"))
    window.provider_model_edit.setText("")
    payloads = []
    window.translation_test.connect(payloads.append)
    window._emit_translation_test()
    assert payloads == []
    assert "endpoint id" in window.toast.text()
    assert window.toast.timer.isActive() is True

    window.provider_model_edit.setText("ep-20250102123456-abcde")
    window._emit_translation_test()
    assert len(payloads) == 1
    assert payloads[0]["provider_id"] == "doubao"


def test_endpoint_edits_are_kept_per_provider(window):
    window.translation_provider.setCurrentIndex(window.translation_provider.findData("deepseek"))
    window.provider_model_edit.setText("deepseek-reasoner")
    assert window.provider_endpoints["deepseek"]["model"] == "deepseek-reasoner"

    window.translation_provider.setCurrentIndex(window.translation_provider.findData("ollama"))
    window.provider_model_edit.setText("qwen3:1.7b")
    assert window.provider_endpoints["deepseek"]["model"] == "deepseek-reasoner"
    assert window.provider_endpoints["ollama"]["model"] == "qwen3:1.7b"


def test_chain_can_be_reordered_and_emitted(window):
    window.chain_list.setCurrentRow(1)
    window._move_chain_item(-1)
    assert window._current_chain() == ["openai_compatible", "ollama"]

    window.translation_provider.setCurrentIndex(window.translation_provider.findData("deepseek"))
    window._add_chain_item()
    payloads = []
    window.translation_apply.connect(payloads.append)
    window._emit_translation_settings()
    assert payloads[0]["chain"] == ["openai_compatible", "ollama", "deepseek"]
    assert payloads[0]["endpoints"] == window.provider_endpoints


def test_chain_reload_filters_unknown_providers(window):
    window.settings.translation_chain = ["ollama", "not-a-provider"]
    window._reload_chain()
    assert window._current_chain() == ["ollama"]
