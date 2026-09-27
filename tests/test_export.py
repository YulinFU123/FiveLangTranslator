import json

from app.export.exporters import (
    ass_timestamp,
    build_timeline,
    export_extension,
    export_text,
    suggest_filename,
    srt_timestamp,
    vtt_timestamp,
    write_export,
)
from app.storage.models import SegmentRecord, SessionRecord


def make_segment(row_id, start_ms, end_ms, source_text, translated_text):
    return SegmentRecord(
        row_id, "session-1", f"seg{row_id}", 3, "final", start_ms, end_ms,
        source_text, translated_text, "en", "zh", "ollama", "system_audio",
    )


SESSION = SessionRecord("session-1", "测试会话", "system_audio", "zh", "cinema", "ollama", 1_700_000_000_000, None, 3)

SEGMENTS = [
    make_segment(2, 3600000, 3602500, "The truth is out there.", "真相就在外面。"),
    make_segment(1, 0, 1800, "He left yesterday.", "他昨天离开了。"),
    make_segment(3, 3661500, 3661500, "Trust no one.", "不要相信任何人。"),
]


def test_timestamps_are_formatted_for_each_format():
    assert srt_timestamp(3661250) == "01:01:01,250"
    assert vtt_timestamp(3661250) == "01:01:01.250"
    assert ass_timestamp(3661250) == "1:01:01.25"


def test_timeline_is_sorted_and_monotonic():
    entries = build_timeline(SEGMENTS)
    assert [entry.index for entry in entries] == [1, 2, 3]
    assert entries[0].source_text == "He left yesterday."
    for previous, entry in zip(entries, entries[1:]):
        assert entry.start_ms >= previous.end_ms
    # zero length fallback uses the minimum duration
    assert entries[2].end_ms == entries[2].start_ms + 500


def test_empty_segments_are_skipped():
    entries = build_timeline([make_segment(1, 0, 1000, "  ", "  "), make_segment(2, 2000, 3000, "Hi", "你好")])
    assert len(entries) == 1
    assert entries[0].source_text == "Hi"


def test_srt_export_contains_cues():
    text = export_text("srt", build_timeline(SEGMENTS), SESSION, "bilingual")
    assert "00:00:00,000 --> 00:00:01,800" in text
    assert "He left yesterday.\n他昨天离开了。" in text
    assert text.startswith("1\n")


def test_vtt_export_has_header_and_dot_timestamps():
    text = export_text("vtt", build_timeline(SEGMENTS), SESSION, "translation")
    assert text.startswith("WEBVTT")
    assert "01:01:01,250" not in text
    assert "01:00:00.000 --> 01:00:02.500" in text
    assert "He left yesterday." not in text


def test_ass_export_is_bilingual_on_one_cue():
    text = export_text("ass", build_timeline(SEGMENTS), SESSION, "bilingual")
    assert "[Script Info]" in text
    assert "Style: Default,Microsoft YaHei" in text
    assert "Dialogue: 0,0:00:00.00,0:00:01.80,Default,,0,0,0,,He left yesterday.\\N他昨天离开了。" in text


def test_txt_export_uses_clock_prefix():
    text = export_text("txt", build_timeline(SEGMENTS), SESSION, "source")
    assert text.splitlines()[0].startswith("[00:00:00] He left yesterday.")
    assert "他昨天离开了。" not in text


def test_json_export_keeps_metadata():
    payload = json.loads(export_text("json", build_timeline(SEGMENTS), SESSION, "bilingual"))
    assert payload["session"]["name"] == "测试会话"
    assert payload["content"] == "bilingual"
    assert len(payload["segments"]) == 3
    assert payload["segments"][0]["start"] == "00:00:00,000"
    assert payload["segments"][0]["translated_text"] == "他昨天离开了。"


def test_write_export_creates_file(tmp_path):
    target = tmp_path / "out" / "subs.srt"
    path = write_export(target, "srt", build_timeline(SEGMENTS), SESSION, "bilingual")
    assert path.exists()
    assert path.read_text(encoding="utf-8").count("-->") == 3
    assert export_extension("vtt") == ".vtt"


def test_suggested_filename_is_safe(tmp_path):
    session = SessionRecord("1", "测试/会话:1", "system_audio", "zh", "cinema", "", 0, None, 0)
    name = suggest_filename(session, "srt")
    assert name.endswith(".srt")
    assert "/" not in name[: len(name) - 20]
    assert ":" not in name[: len(name) - 20]
