from app.windows.window_styles import is_windows, set_click_through


def test_non_windows_click_through_is_safe():
    if not is_windows():
        assert set_click_through(0, True) is False
