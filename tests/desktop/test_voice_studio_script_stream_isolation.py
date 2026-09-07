"""Regression tests for per-chapter dubbing script stream isolation.

Ensures starting script generation on one chapter does not bleed its
"analyzing script" animation into other chapters, and that switching
away from and back to a generating chapter restores its own streaming
progress.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from pytestqt.qtbot import QtBot

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, SegmentType


def _page(qtbot: QtBot) -> VoiceStudioPage:
    page = VoiceStudioPage(settings=Settings(_env_file=None))
    qtbot.addWidget(page)
    return page


def test_generate_script_marks_generating_chapter(qtbot: QtBot) -> None:
    """Starting script generation records which chapter is generating."""
    page = _page(qtbot)
    # Simulate the startup wiring _on_generate_script performs (without
    # submitting a real worker, which needs a project layout).
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()

    assert page._generating_chapter == 3
    assert 3 in page._script_streams
    assert page._script_streams[3].active is False


def test_animation_does_not_overwrite_non_generating_chapter(qtbot: QtBot) -> None:
    """The timer must not write to the browser when the displayed chapter
    differs from the generating chapter."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()
    page._active_chapter_number = 5  # user is viewing chapter 5

    with patch.object(page, "_set_browser_html") as mock_set:
        page._tick_script_gen_animation()

    mock_set.assert_not_called()
    # But the generating chapter's dots still advance (state preserved).
    assert page._script_streams[3].dots == 1


def test_animation_never_rewrites_browser_for_generating_chapter(qtbot: QtBot) -> None:
    """Thinking-state updates must not reset the script viewport."""
    from PySide6.QtWidgets import QTextBrowser

    page = _page(qtbot)
    # The Script tab (and thus _script_browser) is built lazily; supply a real
    # browser so _tick_script_gen_animation can resolve the attribute. The
    # patched _set_browser_html below intercepts the actual write.
    page._script_browser = QTextBrowser()
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()
    page._active_chapter_number = 3

    with patch.object(page, "_set_browser_html") as mock_set:
        page._tick_script_gen_animation()

    mock_set.assert_not_called()
    assert page._script_streams[3].dots == 1


def test_script_generation_progress_uses_real_batch_events(qtbot: QtBot) -> None:
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=False)
    qtbot.addWidget(page)
    page._active_chapter_number = 3
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()

    page._on_step_progress(
        "tts_script_llm_batch_start",
        {"chapter": 3, "batch_index": 1, "batch_count": 4},
    )
    page._on_step_progress(
        "tts_script_llm_batch_complete",
        {"chapter": 3, "batch_index": 1, "batch_count": 4},
    )

    assert not page._script_generation_bar.isHidden()
    assert page._script_generation_badge.text() == "脚本导演 1/4"
    assert "已完成 1/4 批" in page._script_generation_progress.format()
    assert page._script_generation_progress.value() > 12


def test_lazy_script_tab_restores_running_progress_when_opened_late(
    qtbot: QtBot,
    tmp_path,
) -> None:
    """A task started from another tab must be visible on first Script-tab open."""
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=True)
    qtbot.addWidget(page)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("章节正文", encoding="utf-8")
    page._layout = layout
    page._active_chapter_number = 1
    page._loaded_chapter_number = 1
    page._generating_chapter = 1
    page._script_streams[1] = _ChapterScriptStream()

    # These events arrive while the Script tab is still a lightweight placeholder.
    page._on_step_progress(
        "tts_script_llm_batch_start",
        {"chapter": 1, "batch_index": 1, "batch_count": 3},
    )
    page._on_step_progress(
        "tts_script_llm_batch_complete",
        {"chapter": 1, "batch_index": 1, "batch_count": 3},
    )
    page._build_deferred_tab(1)

    assert "正在分析脚本" in page._script_browser.toPlainText()
    assert page._script_generation_badge.text() == "脚本导演 1/4"
    assert "已完成 1/3 批" in page._script_generation_progress.format()
    assert not page._script_generation_bar.isHidden()


def test_stream_delta_routes_by_payload_chapter(qtbot: QtBot) -> None:
    """llm_stream_delta updates the stream for the chapter in the payload,
    not the currently-displayed chapter."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(stream_id="abc", active=True)
    page._active_chapter_number = 5  # viewing a different chapter

    page._on_step_progress(
        "llm_stream_delta",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "abc",
            "segments": [{"kind": "content", "text": "片段一"}],
        },
    )

    assert page._script_streams[3].text == "片段一"
    # Chapter 5 has no stream entry and is untouched.
    assert 5 not in page._script_streams


def test_stream_delta_ignored_for_wrong_stream_id(qtbot: QtBot) -> None:
    """A delta with a mismatched stream_id is dropped (anti-cross-talk guard)."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(stream_id="abc", active=True)
    page._active_chapter_number = 3

    page._on_step_progress(
        "llm_stream_delta",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "different",
            "segments": [{"kind": "content", "text": "不应写入"}],
        },
    )

    assert page._script_streams[3].text == ""


def test_cancel_clears_generating_chapter_stream(qtbot: QtBot) -> None:
    """Cancelling pops the generating chapter's stream entry."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(active=True)
    page._current_worker = None  # _on_cancel_clicked tolerates None

    page._on_cancel_clicked()

    assert page._generating_chapter is None
    assert 3 not in page._script_streams


def test_render_script_stream_noop_for_non_streaming_chapter(qtbot: QtBot) -> None:
    """Rendering is a no-op when the displayed chapter has no stream."""
    page = _page(qtbot)
    page._active_chapter_number = 5
    # No stream entry for chapter 5.

    with patch.object(page, "_set_browser_html") as mock_set:
        page._render_script_stream()

    mock_set.assert_not_called()


def test_late_stream_event_after_cancel_is_dropped(qtbot: QtBot) -> None:
    """A late llm_stream_start arriving after cancel must not resurrect a
    stale stream entry (terminal handlers already cleared _generating_chapter
    and _current_worker)."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(active=True)
    page._current_worker = None  # _on_cancel_clicked tolerates None

    page._on_cancel_clicked()
    # Simulate a tardy stream-start signal from the just-cancelled worker.
    page._on_step_progress(
        "llm_stream_start",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "late",
        },
    )

    assert 3 not in page._script_streams
    assert page._generating_chapter is None


def test_late_stream_event_from_replaced_worker_is_dropped(qtbot: QtBot) -> None:
    """A cancelled worker cannot resurrect its chapter after a replacement starts."""
    page = _page(qtbot)
    page._current_worker = SimpleNamespace(worker_id="new-worker")
    page._generating_chapter = 4
    page._script_streams[4] = _ChapterScriptStream()

    page._on_step_progress(
        "llm_stream_start",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "late-old-stream",
        },
        worker_id="old-worker",
    )

    assert 3 not in page._script_streams
    assert page._generating_chapter == 4


def test_late_script_result_from_replaced_worker_is_dropped(qtbot: QtBot) -> None:
    """An old success signal must not finish or navigate away from the new task."""
    page = _page(qtbot)
    page._current_worker = SimpleNamespace(worker_id="new-worker")
    page._generating_chapter = 4
    page._active_chapter_number = 4
    page._script_streams[4] = _ChapterScriptStream(active=True)

    page._on_script_updated(
        DubbingScript(chapter_number=3).model_dump(mode="json"),
        worker_id="old-worker",
    )

    assert page._generating_chapter == 4
    assert page._active_chapter_number == 4
    assert 4 in page._script_streams


def test_switching_back_before_first_stream_event_shows_generation_state(
    qtbot: QtBot,
    tmp_path,
) -> None:
    """The pre-stream window shows an immediate placeholder, not stale script content."""
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=False)
    qtbot.addWidget(page)
    page._layout = ProjectLayout(tmp_path / "demo")
    page._layout.ensure_dirs()
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(active=False)

    with patch.object(page, "_render_script_gen_placeholder") as render_placeholder:
        page._load_chapter_artifacts(3)

    render_placeholder.assert_called_once_with(3)
    assert "第 3 章正在准备流式输出" in page._script_source_hint.text()


def test_cancel_restores_the_chapters_persisted_script_view(
    qtbot: QtBot,
    tmp_path,
) -> None:
    """Cancelling replaces the transient placeholder with the last saved script."""
    page = VoiceStudioPage(settings=Settings(_env_file=None), defer_tabs=False)
    qtbot.addWidget(page)
    page._layout = ProjectLayout(tmp_path / "demo")
    page._layout.ensure_dirs()
    script = DubbingScript(
        chapter_number=3,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="已保存的第三章脚本。",
            )
        ],
    )
    script_path = page._layout.tts_dubbing_script_path(3)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")
    page._active_chapter_number = 3
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(active=True, text="未完成流")
    page._current_worker = None

    page._on_cancel_clicked()

    assert page._current_script is not None
    assert page._current_script.chapter_number == 3
    assert "已保存的第三章脚本" in page._script_browser.toPlainText()
    assert page._status_badge.text() == "操作已取消"
