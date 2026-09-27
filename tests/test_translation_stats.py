from app.translation.stats import LatencyTracker
from app.ui.theme import build_style, system_prefers_light, tokens_for


def test_percentiles_over_a_rolling_window():
    tracker = LatencyTracker(50)
    for value in range(1, 101):
        tracker.record(value, cached=False)
    assert len(tracker.samples) == 50, "the window keeps only the recent samples"
    assert len(tracker.latencies()) == 50
    # The window holds 51..100 after eviction.
    assert tracker.p50 >= 70
    assert tracker.p95 >= 95
    assert tracker.p50 <= tracker.p95


def test_single_sample_percentile():
    tracker = LatencyTracker()
    tracker.record(120, cached=False)
    assert tracker.p50 == 120
    assert tracker.p95 == 120


def test_cache_hit_rate_counts_cached_samples():
    tracker = LatencyTracker()
    tracker.record(0, cached=True)
    tracker.record(200, cached=False)
    tracker.record(0, cached=True)
    tracker.record(150, cached=False)
    assert tracker.cache_hit_rate == 0.5


def test_snapshot_reports_provider_and_model():
    tracker = LatencyTracker()
    tracker.record(180, cached=False, provider="deepseek", model="deepseek-chat")
    snapshot = tracker.snapshot()
    assert snapshot["provider"] == "deepseek"
    assert snapshot["model"] == "deepseek-chat"
    assert snapshot["latest_ms"] == 180
    assert snapshot["count"] == 1
    assert snapshot["series"] == [180]


def test_empty_tracker_is_safe():
    tracker = LatencyTracker()
    snapshot = tracker.snapshot()
    assert snapshot["p50_ms"] == 0
    assert snapshot["p95_ms"] == 0
    assert snapshot["cache_hit_rate"] == 0.0
    assert tracker.latest() is None


def test_window_evicts_oldest_samples():
    tracker = LatencyTracker(3)
    for value in (10, 20, 30, 40):
        tracker.record(value, cached=False)
    assert tracker.latencies() == [20, 30, 40]


def test_clear_resets_the_window():
    tracker = LatencyTracker()
    tracker.record(1, cached=True)
    tracker.clear()
    assert tracker.latencies() == []
    assert tracker.cache_hit_rate == 0.0


def test_theme_has_light_and_dark_tokens():
    assert build_style(True) != build_style(False)
    assert tokens_for(True)["window"] != tokens_for(False)["window"]
    # A missing registry must not crash the theme probe.
    assert isinstance(system_prefers_light(), bool)
