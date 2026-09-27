"""Qt dependent service tests. Skipped automatically when PySide6 is unavailable."""

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for Qt service objects")

from app.translation.service import TranslationService  # noqa: E402


def test_default_line_budget_is_two():
    service = TranslationService()
    assert service.max_lines == 2


def test_set_max_lines_clamps_out_of_range_values():
    service = TranslationService()
    assert service.set_max_lines(0) is True
    assert service.max_lines == 1
    assert service.set_max_lines(99) is True
    assert service.max_lines == 8
    assert service.set_max_lines(4) is True
    assert service.max_lines == 4


def test_set_max_lines_reports_no_change_when_identical():
    service = TranslationService()
    assert service.set_max_lines(2) is False
    service.set_max_lines(6)
    assert service.set_max_lines(6) is False
