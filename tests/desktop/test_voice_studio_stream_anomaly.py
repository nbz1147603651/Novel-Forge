"""Unit tests for streaming script preview anomaly detection and throttling.

Covers:
- detect_stream_anomalies pure function (duplicates, JSON echo, inflation)
- Anomaly badge rendering in the streaming preview
- Render coalescing under high-frequency deltas
"""

from __future__ import annotations

from unittest.mock import patch

from pytestqt.qtbot import QtBot

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.helpers import (
    StreamAnomalyReport,
    detect_stream_anomalies,
)
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream

# ─── detect_stream_anomalies: duplicates ─────────────────────────────


def test_detect_adjacent_duplicate_segments() -> None:
    """Identical text in consecutive segments flags the later index."""
    segments = [
        {"text": "沈岸猛地睁开眼，回到了现实。"},
        {"text": "沈岸猛地睁开眼，回到了现实。"},
        {"text": "窗外的雨声变得遥远。"},
    ]
    report = detect_stream_anomalies(segments, received_chars=100, source_chars=100)
    assert report.duplicate_indices == (1,)
    assert report.has_anomalies


def test_detect_window_duplicate_segments() -> None:
    """A-B-A pattern within a 3-segment window is flagged."""
    segments = [
        {"text": "段落甲"},
        {"text": "段落乙"},
        {"text": "段落甲"},
    ]
    report = detect_stream_anomalies(segments, received_chars=30, source_chars=30)
    assert report.duplicate_indices == (2,)


def test_no_duplicates_for_distinct_segments() -> None:
    """Distinct segment texts produce no duplicate flags."""
    segments = [
        {"text": "第一段正文"},
        {"text": "第二段正文"},
        {"text": "第三段正文"},
        {"text": "第四段正文"},
    ]
    report = detect_stream_anomalies(segments, received_chars=40, source_chars=40)
    assert report.duplicate_indices == ()
    assert not report.has_anomalies


def test_whitespace_normalized_duplicate() -> None:
    """Leading/trailing whitespace does not evade duplicate detection."""
    segments = [
        {"text": "  相同文本。"},
        {"text": "相同文本。  "},
    ]
    report = detect_stream_anomalies(segments, received_chars=20, source_chars=20)
    assert report.duplicate_indices == (1,)


def test_empty_text_segments_never_flagged() -> None:
    """Empty text segments are skipped by duplicate detection."""
    segments = [{"text": ""}, {"text": ""}, {"text": "正文"}]
    report = detect_stream_anomalies(segments, received_chars=10, source_chars=10)
    assert report.duplicate_indices == ()


# ─── detect_stream_anomalies: JSON echo ──────────────────────────────


def test_detect_json_echo_in_segment_text() -> None:
    """Raw JSON structural keys inside prose are flagged."""
    segments = [
        {"text": "正常旁白文本。"},
        {"text": '"segment_index": 3, "segment_type": "narration"'},
    ]
    report = detect_stream_anomalies(segments, received_chars=80, source_chars=80)
    assert report.json_echo_indices == (1,)
    assert report.has_anomalies


def test_json_echo_pattern_variants() -> None:
    """Both segment_index and segment_type key patterns are detected."""
    segments = [
        {"text": '然后他说："你好"。'},  # normal dialogue, no false positive
        {"text": '{"segment_type" : "narration"}'},
        {"text": '前置文本 "segment_index":12 后置'},
    ]
    report = detect_stream_anomalies(segments, received_chars=90, source_chars=90)
    assert report.json_echo_indices == (1, 2)
    # Normal dialogue with Chinese quotes must not trigger
    assert 0 not in report.json_echo_indices


def test_no_json_echo_false_positive_on_prose() -> None:
    """Ordinary prose containing quotes or colons is not flagged."""
    segments = [
        {"text": "他说：“segment_index 是什么？”然后笑了。"},
        {"text": 'The word "segment_type" appeared in the essay.'},
    ]
    report = detect_stream_anomalies(segments, received_chars=60, source_chars=60)
    # First: no colon after the key inside quotes pattern
    # Second: no colon after "segment_type"
    assert report.json_echo_indices == ()


# ─── detect_stream_anomalies: char inflation ─────────────────────────


def test_char_inflation_triggers_above_threshold() -> None:
    """Received chars > 3x source triggers inflation flag."""
    report = detect_stream_anomalies([], received_chars=3001, source_chars=1000)
    assert report.char_inflation is True
    assert report.has_anomalies


def test_char_inflation_not_triggered_at_boundary() -> None:
    """Exactly 3x source chars does not trigger (strictly greater)."""
    report = detect_stream_anomalies([], received_chars=3000, source_chars=1000)
    assert report.char_inflation is False


def test_char_inflation_not_triggered_without_source() -> None:
    """source_chars=0 (unknown) never triggers inflation."""
    report = detect_stream_anomalies([], received_chars=99999, source_chars=0)
    assert report.char_inflation is False


# ─── StreamAnomalyReport ─────────────────────────────────────────────


def test_report_has_anomalies_property() -> None:
    assert not StreamAnomalyReport().has_anomalies
    assert StreamAnomalyReport(duplicate_indices=(1,)).has_anomalies
    assert StreamAnomalyReport(json_echo_indices=(0,)).has_anomalies
    assert StreamAnomalyReport(char_inflation=True).has_anomalies


# ─── Rendering: anomaly badges in streaming preview ──────────────────


def _page(qtbot: QtBot) -> VoiceStudioPage:
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=False)
    qtbot.addWidget(page)
    return page


def _stream_with_segments(*texts: str, source_chars: int = 5000) -> _ChapterScriptStream:
    """Build a stream buffer whose text parses into complete segments."""
    import json

    segments = [
        {"segment_index": i, "segment_type": "narration", "text": text}
        for i, text in enumerate(texts)
    ]
    raw = json.dumps({"segments": segments}, ensure_ascii=False)
    # Truncate the trailing "]}" to simulate an in-flight stream (the parser
    # only needs balanced objects, not a closed array).
    stream_text = raw[:-2]
    return _ChapterScriptStream(
        text=stream_text,
        active=True,
        progress_data={"source_chars": source_chars},
    )


def test_render_stream_shows_duplicate_badge(qtbot: QtBot) -> None:
    """Duplicated segment text renders a warning badge in the preview."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    page._script_streams[1] = _stream_with_segments(
        "沈岸猛地睁开眼，回到了现实。",
        "沈岸猛地睁开眼，回到了现实。",
        "窗外的雨声变得遥远。",
    )

    page._render_script_stream()

    html = page._script_browser.toHtml()
    assert "与上段重复" in html
    assert "检测到模型输出异常" in html
    assert "预览待校验" not in html  # replaced by anomaly title


def test_render_stream_shows_json_echo_badge(qtbot: QtBot) -> None:
    """JSON structural echo renders a danger badge with muted text colour."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    page._script_streams[1] = _stream_with_segments(
        "正常旁白。",
        '"segment_index": 3, "segment_type": "narration", "text": "雨声停了。"',
    )

    page._render_script_stream()

    html = page._script_browser.toHtml()
    assert "结构异常" in html
    # The broken segment's text is rendered in the muted colour (Qt rich
    # text does not support CSS opacity).
    plain = page._script_browser.toPlainText()
    assert "segment_index" in plain


def test_render_stream_shows_inflation_warning(qtbot: QtBot) -> None:
    """Char inflation appends a warning to the status line."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    # source_chars=10 but stream text is much longer than 30 chars
    page._script_streams[1] = _stream_with_segments(
        "这是一段很长很长很长很长很长的旁白文本，用来触发字符膨胀检测。",
        source_chars=10,
    )

    page._render_script_stream()

    html = page._script_browser.toHtml()
    assert "输出量异常偏大" in html


def test_render_stream_clean_output_shows_preview_label(qtbot: QtBot) -> None:
    """Without anomalies the title shows the pending-validation suffix."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    page._script_streams[1] = _stream_with_segments(
        "第一段正文。",
        "第二段正文。",
    )

    page._render_script_stream()

    html = page._script_browser.toHtml()
    assert "预览待校验" in html
    assert "与上段重复" not in html
    assert "检测到模型输出异常" not in html


# ─── Render coalescing ───────────────────────────────────────────────


def test_schedule_stream_render_coalesces_rapid_deltas(qtbot: QtBot) -> None:
    """10 rapid schedule calls within the throttle window render at most twice."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    page._script_streams[1] = _ChapterScriptStream(text="{}", active=True)

    render_count = 0
    original = page._render_script_stream

    def counting_render() -> None:
        nonlocal render_count
        render_count += 1
        original()

    # Keep the patch active while the coalesced timer fires.
    with patch.object(page, "_render_script_stream", counting_render):
        for _ in range(10):
            page._schedule_stream_render()

        # First call renders immediately; the rest coalesce into one timer.
        assert render_count == 1
        assert page._stream_render_scheduled is True

        # Let the scheduled timer fire (throttle interval is 400 ms).
        qtbot.wait(500)
        assert render_count == 2
        assert page._stream_render_scheduled is False


def test_schedule_stream_render_immediate_after_interval(qtbot: QtBot) -> None:
    """A call after the throttle interval has elapsed renders immediately."""
    page = _page(qtbot)
    page._active_chapter_number = 1
    page._script_streams[1] = _ChapterScriptStream(text="{}", active=True)
    # Simulate that the last render happened long ago.
    page._stream_last_render_at = 0.0

    render_count = 0
    original = page._render_script_stream

    def counting_render() -> None:
        nonlocal render_count
        render_count += 1
        original()

    with patch.object(page, "_render_script_stream", counting_render):
        page._schedule_stream_render()

    assert render_count == 1
    assert page._stream_render_scheduled is False


def test_stream_end_flushes_final_render(qtbot: QtBot) -> None:
    """llm_stream_end triggers a direct render regardless of throttle state."""
    page = _page(qtbot)
    page._generating_chapter = 1
    page._active_chapter_number = 1
    page._script_streams[1] = _ChapterScriptStream(
        stream_id="s1", text='{"segments": [', active=True
    )
    # Simulate a pending throttle window (recently rendered).
    import time

    page._stream_last_render_at = time.monotonic()

    render_count = 0
    original = page._render_script_stream

    def counting_render() -> None:
        nonlocal render_count
        render_count += 1
        original()

    with patch.object(page, "_render_script_stream", counting_render):
        page._on_step_progress(
            "llm_stream_end",
            {
                "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
                "chapter": 1,
                "stream_id": "s1",
                "text": '{"segments": [{"text": "最终文本"}]}',
            },
        )

    # llm_stream_end calls _render_script_stream directly (flush).
    assert render_count == 1
    assert page._script_streams[1].text == '{"segments": [{"text": "最终文本"}]}'


# ─── Regression: batch event must not overwrite source_chars ─────────


def test_batch_event_does_not_overwrite_source_chars(qtbot: QtBot) -> None:
    """tts_script_llm_batch_start must not replace the full-chapter source_chars
    set by tts_script_start; otherwise the inflation check uses a shrunken
    denominator and false-positives."""
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=False)
    qtbot.addWidget(page)
    page._active_chapter_number = 1
    page._generating_chapter = 1
    page._script_streams[1] = _ChapterScriptStream()

    # tts_script_start sets the full-chapter source_chars.
    page._update_script_generation_progress(
        "tts_script_start", {"chapter": 1, "source_chars": 8000}
    )
    assert page._script_streams[1].progress_data["source_chars"] == 8000

    # tts_script_llm_batch_start carries a per-batch source_chars.
    page._update_script_generation_progress(
        "tts_script_llm_batch_start",
        {"chapter": 1, "batch_index": 1, "batch_count": 4, "source_chars": 2000},
    )
    # The full-chapter value must be preserved.
    assert page._script_streams[1].progress_data["source_chars"] == 8000


def test_json_overhead_does_not_trigger_inflation(qtbot: QtBot) -> None:
    """Raw JSON stream text naturally exceeds 3x source due to structural
    metadata; the inflation check must use segment text length instead."""
    import json

    page = _page(qtbot)
    page._active_chapter_number = 1

    # Build segments whose TEXT is short but whose JSON representation is long
    # due to metadata fields (segment_index, segment_type, emotion, tone_hint).
    segments = [
        {
            "segment_index": i,
            "segment_type": "narration",
            "character_name": "旁白",
            "emotion": "neutral",
            "tone_hint": "平稳叙述",
            "speed_override": 1.0,
            "text": f"第{i + 1}段正文。",
        }
        for i in range(10)
    ]
    raw = json.dumps({"segments": segments}, ensure_ascii=False)
    stream_text = raw[:-2]  # simulate in-flight stream

    # source_chars is generous enough that segment TEXT (< 100 chars total)
    # does not exceed 3x, but the raw JSON (>> 300 chars) would.
    source_chars = 100
    page._script_streams[1] = _ChapterScriptStream(
        text=stream_text,
        active=True,
        progress_data={"source_chars": source_chars},
    )

    page._render_script_stream()

    html = page._script_browser.toHtml()
    # The false-positive warning must NOT appear.
    assert "输出量异常偏大" not in html
    # The preview should show the normal pending-validation label.
    assert "预览待校验" in html
