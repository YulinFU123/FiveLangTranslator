from app.player.layout import Rect, clamp_rect, profile_to_rect
from app.settings import Profile, Settings


def test_profile_to_player_relative_rect():
    profile = Profile(.1, .75, .8, .15)
    anchor = Rect(100, 50, 1920, 1080)
    result = profile_to_rect(profile, anchor)
    assert result.left == 292
    assert result.top == 860
    assert result.width == 1536
    assert result.height == 162


def test_rect_keeps_recovery_handle_visible():
    screen = Rect(0, 0, 1920, 1080)
    escaped = Rect(3000, 2000, 800, 200)
    result = clamp_rect(escaped, screen)
    assert result.left <= 1830
    assert result.top <= 1035


def test_settings_migrates_legacy_profile():
    legacy = Profile(.2, .7, .6, .2)
    settings = Settings(profile=legacy)
    assert settings.windowed_profile is legacy
    assert settings.fullscreen_profile is not None
