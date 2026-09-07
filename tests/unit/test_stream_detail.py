"""Tests for StreamDetailWidget and SegmentWidget."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.phase_progress import PhaseProgressBar, _chapter_phase_index
from novel_forge.desktop.components.stream_behavior import StreamFollowController
from novel_forge.desktop.components.stream_detail import (
    _REASONING_INITIAL_HEIGHT,
    _REASONING_MAX_HEIGHT,
    SegmentWidget,
    StreamDetailWidget,
)
from novel_forge.desktop.components.task_focus import (
    FloatingStreamWindow,
    TaskFocusPanel,
    TaskModelCallPanel,
    _html_from_text,
)
from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.task_observation import (
    ObservedStreamState,
    ObservedTaskState,
    StreamSegment,
    TaskFocusScope,
    TaskObservationStore,
)


def _qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


class TestStreamFollowController:
    def test_document_replacement_keeps_manual_review_at_an_absolute_offset(self) -> None:
        app = _qapp()
        browser = QTextBrowser()
        try:
            browser.resize(520, 180)
            browser.show()
            browser.setPlainText("\n".join(f"旧输出 {index}" for index in range(500)))
            app.processEvents()
            controller = StreamFollowController(browser)
            bar = browser.verticalScrollBar()
            bar.setValue(bar.maximum() // 3)
            app.processEvents()
            anchor = controller.capture_content_anchor()

            assert anchor[0] is False
            previous_value = anchor[1]

            browser.setPlainText("\n".join(f"新输出 {index}" for index in range(1500)))
            controller.restore_content_anchor(anchor)
            app.processEvents()

            assert controller.following_latest is False
            assert bar.value() == min(previous_value, bar.maximum())
        finally:
            browser.close()
            browser.deleteLater()

    def test_document_replacement_keeps_latest_reader_at_bottom(self) -> None:
        app = _qapp()
        browser = QTextBrowser()
        try:
            browser.resize(520, 180)
            browser.show()
            browser.setPlainText("\n".join(f"旧输出 {index}" for index in range(500)))
            app.processEvents()
            controller = StreamFollowController(browser)
            bar = browser.verticalScrollBar()
            bar.setValue(bar.maximum())
            app.processEvents()
            anchor = controller.capture_content_anchor()

            assert anchor[0] is True

            browser.setPlainText("\n".join(f"新输出 {index}" for index in range(1500)))
            controller.restore_content_anchor(anchor)
            app.processEvents()

            assert controller.following_latest is True
            assert bar.value() == bar.maximum()
        finally:
            browser.close()
            browser.deleteLater()


@pytest.fixture(autouse=True)
def _qapp_fixture():
    _qapp()
    yield


class TestSegmentWidget:
    def test_content_segment_has_no_toggle(self) -> None:
        seg = StreamSegment(kind="content", text="正文内容")
        widget = SegmentWidget(seg)
        assert not hasattr(widget, "_toggle_label")

    def test_content_segment_streaming_cursor(self) -> None:
        seg = StreamSegment(kind="content", text="正文内容")
        widget = SegmentWidget(seg)

        widget.set_streaming(True)
        assert "▍" in widget._body.text()

        widget.set_streaming(False)
        assert "▍" not in widget._body.text()

    def test_content_segment_does_not_force_horizontal_growth(self) -> None:
        seg = StreamSegment(kind="content", text="超长文本" * 200)
        widget = SegmentWidget(seg)

        assert widget.minimumWidth() == 0
        assert widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
        assert widget._body.minimumWidth() == 0
        assert widget._body.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    def test_content_html_collapses_repeated_blank_lines(self) -> None:
        html = SegmentWidget._content_html("第一段\n\n\n第二段")

        assert html.count("&nbsp;") == 1
        assert "0.32em" in html
        assert "line-height: 1.62" in html

    def test_content_segment_uses_shared_json_renderer(self) -> None:
        widget = SegmentWidget(StreamSegment(kind="content", text='{"chapter": 3, "ready": true}'))

        assert "json-card" in widget._body.text()
        assert "JSON 输出" in widget._body.text()

        widget.update_segment(StreamSegment(kind="content", text='{"chapter": 3, "ready": true'))
        assert "JSON 块尚未完成" in widget._body.text()

    def test_reasoning_segment_has_toggle(self) -> None:
        seg = StreamSegment(kind="reasoning", text="思考过程")
        widget = SegmentWidget(seg)
        assert hasattr(widget, "_toggle_label")
        assert "思考" in widget._toggle_label.text()

    def test_reasoning_toggle_collapses_body(self) -> None:
        seg = StreamSegment(kind="reasoning", text="思考过程")
        widget = SegmentWidget(seg)
        assert not widget._body.isHidden()
        widget._toggle(None)
        assert widget._body.isHidden()
        widget._toggle(None)
        assert not widget._body.isHidden()

    def test_collapse_and_expand_methods(self) -> None:
        seg = StreamSegment(kind="reasoning", text="思考")
        widget = SegmentWidget(seg)
        assert not widget._body.isHidden()
        widget.collapse()
        assert widget._body.isHidden()
        widget.expand()
        assert not widget._body.isHidden()

    def test_reasoning_segment_can_start_collapsed(self) -> None:
        seg = StreamSegment(kind="reasoning", text="思考")
        widget = SegmentWidget(seg, collapsed=True)

        assert widget._body.isHidden()
        assert widget._toggle_label.text().startswith("▶")

    def test_expanded_reasoning_starts_compact_then_grows_to_its_cap(self) -> None:
        app = _qapp()
        host = QWidget()
        host.resize(420, 560)
        layout = QVBoxLayout(host)
        widget = SegmentWidget(StreamSegment(kind="reasoning", text="短思考"), collapsed=True)
        layout.addWidget(widget)
        try:
            host.show()
            app.processEvents()

            widget.expand()
            app.processEvents()
            assert widget._body_scroll is not None
            assert widget._body_scroll.height() == _REASONING_INITIAL_HEIGHT

            widget.update_segment(StreamSegment(kind="reasoning", text="增长中的思考。" * 1600))
            app.processEvents()
            assert widget._body_scroll.height() == _REASONING_MAX_HEIGHT
            assert widget._body_scroll.verticalScrollBar().maximum() > 0
            assert widget._body_scroll.accessibleName() == "思考内容，可滚动"
        finally:
            host.close()
            host.deleteLater()

    def test_expanded_reasoning_follows_growth_and_resumes_at_bottom(self) -> None:
        app = _qapp()
        host = QWidget()
        host.resize(420, 560)
        layout = QVBoxLayout(host)
        widget = SegmentWidget(
            StreamSegment(kind="reasoning", text="初始思考。"),
            collapsed=True,
        )
        layout.addWidget(widget)
        try:
            host.show()
            app.processEvents()

            widget.expand()
            app.processEvents()
            assert widget._body_scroll is not None
            assert widget._body_follow is not None
            scrollbar = widget._body_scroll.verticalScrollBar()

            widget.update_segment(StreamSegment(kind="reasoning", text="初始思考。" * 1600))
            app.processEvents()
            assert scrollbar.maximum() > 0
            assert scrollbar.value() == scrollbar.maximum()
            assert widget._body_follow.following_latest is True

            scrollbar.setValue(0)
            app.processEvents()
            assert widget._body_follow.following_latest is False

            widget.update_segment(StreamSegment(kind="reasoning", text="初始思考。" * 2400))
            app.processEvents()
            assert scrollbar.value() < scrollbar.maximum()

            scrollbar.setValue(scrollbar.maximum())
            app.processEvents()
            assert widget._body_follow.following_latest is True
        finally:
            host.close()
            host.deleteLater()


class TestStreamDetailWidget:
    def _make_stream(
        self,
        segments: tuple[StreamSegment, ...],
        stream_id: str = "s1",
        *,
        source: str = "llm_stream",
        truncated: bool = False,
        status: str = "streaming",
        task: str = "DRAFT_CHAPTER",
        error: str = "",
        output_kind: str = "",
        validation_status: str = "",
    ) -> ObservedStreamState:
        text = "".join(seg.text for seg in segments if seg.kind == "content")
        return ObservedStreamState(
            job_id="j1",
            stream_id=stream_id,
            segments=segments,
            status=status,
            source=source,
            truncated=truncated,
            task=task,
            attempt=2,
            text=text,
            text_length=len(text),
            error=error,
            model="mock-model",
            output_kind=output_kind,
            validation_status=validation_status,
        )

    def test_empty_stream_clears_view(self) -> None:
        widget = StreamDetailWidget()
        widget.set_stream(None)
        assert widget.segment_count == 0
        assert widget.minimumWidth() == 0
        assert widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    def test_renders_all_segments(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (
                StreamSegment(kind="reasoning", text="R1"),
                StreamSegment(kind="content", text="C1"),
                StreamSegment(kind="reasoning", text="R2"),
            )
        )
        widget.set_stream(stream)
        assert widget.segment_count == 3

    def test_failed_json_is_labeled_as_diagnostic_not_completed_result(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (StreamSegment(kind="content", text='{"summary":"语法合法"}'),),
            status="validation_failed",
            error="缺少业务字段 chapter_id",
            output_kind="json",
            validation_status="failed",
        )

        widget.set_stream(stream)

        assert "JSON 诊断" in widget._header_bar._label.text()
        assert "校验失败" in widget._header_bar._label.text()
        assert widget._validation_hint is not None
        assert "不会作为正式结果" in widget._validation_hint.text()
        assert widget._error_card is not None
        assert widget._error_card.text() == "缺少业务字段 chapter_id"

    def test_first_reasoning_segment_is_expanded_and_later_ones_fold(self) -> None:
        """All reasoning segments should be collapsed by default."""
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (
                StreamSegment(kind="reasoning", text="R1"),
                StreamSegment(kind="content", text="C1"),
                StreamSegment(kind="reasoning", text="R2"),
            )
        )

        widget.set_stream(stream)

        # All reasoning segments collapsed by default (body hidden).
        assert widget._segment_widgets[0]._body.isHidden()
        assert widget._segment_widgets[2]._body.isHidden()

    def test_reasoning_expand_all_applies_to_future_segments(self) -> None:
        widget = StreamDetailWidget()
        stream1 = self._make_stream((StreamSegment(kind="reasoning", text="R1"),))
        widget.set_stream(stream1)
        widget.expand_all_reasoning()

        stream2 = self._make_stream(
            (
                StreamSegment(kind="reasoning", text="R1"),
                StreamSegment(kind="content", text="C1"),
                StreamSegment(kind="reasoning", text="R2"),
            )
        )
        widget.set_stream(stream2)

        assert not widget._segment_widgets[2]._body.isHidden()

    def test_model_call_empty_stream_uses_model_call_card(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream((), source="model_call")

        widget.set_stream(stream)

        assert widget.segment_count == 0
        assert widget._model_call_card is not None
        assert (
            widget._model_call_card._title.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Ignored
        )
        assert (
            widget._model_call_card._preview.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Ignored
        )
        assert not widget._header_bar.isHidden()
        assert not widget._placeholder.isVisible()

    def test_model_call_card_shows_segment_statistics(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (
                StreamSegment(kind="reasoning", text="RR"),
                StreamSegment(kind="content", text="CC"),
            ),
            source="model_call",
            status="complete",
        )

        widget.set_stream(stream)

        assert widget._model_call_card is not None
        stats = widget._model_call_card._stats_label.text()
        assert "思考 50%" in stats
        assert "正文 50%" in stats
        assert "共 2 段" in stats

    def test_header_bar_renders_stream_metadata(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream((StreamSegment(kind="content", text="正文"),))

        widget.set_stream(stream)

        assert not widget._header_bar.isHidden()
        assert "第 2 轮" in widget._header_bar._label.text()
        assert "mock-model" in widget._header_bar._label.text()
        assert widget._header_bar._label.minimumWidth() == 0
        assert (
            widget._header_bar._label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
        )

    def test_scroll_host_is_clamped_to_viewport_width(self) -> None:
        app = _qapp()
        widget = StreamDetailWidget()
        try:
            widget.resize(320, 240)
            widget.show()
            app.processEvents()
            widget._sync_host_width()

            assert widget._host.maximumWidth() <= widget.viewport().width()
        finally:
            widget.close()
            widget.deleteLater()

    def test_streaming_cursor_tracks_latest_content_segment(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream((StreamSegment(kind="content", text="正文"),))
        widget.set_stream(stream)

        assert widget._segment_widgets[0]._streaming is True

        complete = self._make_stream(
            (StreamSegment(kind="content", text="正文"),),
            status="complete",
        )
        widget.set_stream(complete)

        assert widget._segment_widgets[0]._streaming is False

    def test_error_stream_shows_error_card(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (StreamSegment(kind="content", text="部分输出"),),
            status="error",
            error="模型失败",
        )

        widget.set_stream(stream)

        assert widget._error_card is not None
        assert "模型失败" in widget._error_card.text()

    def test_truncated_stream_shows_folded_history_hint(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (StreamSegment(kind="content", text="recent"),),
            truncated=True,
        )

        widget.set_stream(stream)

        assert widget.segment_count == 1
        assert widget._truncation_hint is not None
        assert "前文已折叠" in widget._truncation_hint.text()

    def test_incremental_append_same_stream(self) -> None:
        widget = StreamDetailWidget()
        stream1 = self._make_stream((StreamSegment(kind="content", text="part1"),))
        widget.set_stream(stream1)
        assert widget.segment_count == 1

        stream2 = self._make_stream(
            (
                StreamSegment(kind="content", text="part1"),
                StreamSegment(kind="content", text="part2"),
            )
        )
        widget.set_stream(stream2)
        assert widget.segment_count == 2

    def test_same_segment_growth_updates_existing_widget(self) -> None:
        widget = StreamDetailWidget()
        stream1 = self._make_stream((StreamSegment(kind="content", text="part1"),))
        widget.set_stream(stream1)
        assert widget.segment_count == 1

        stream2 = self._make_stream((StreamSegment(kind="content", text="part1part2"),))
        widget.set_stream(stream2)
        assert widget.segment_count == 1
        assert widget._segment_widgets[0].segment.text == "part1part2"

    def test_follows_new_output_only_until_reader_moves_the_scrollbar(self) -> None:
        app = _qapp()
        widget = StreamDetailWidget()
        widget.resize(360, 240)
        first = self._make_stream((StreamSegment(kind="content", text="第一批输出\n" * 80),))
        try:
            widget.show()
            widget.set_stream(first)
            app.processEvents()
            scrollbar = widget.verticalScrollBar()
            assert scrollbar.value() == scrollbar.maximum()
            assert widget._follow_controller.following_latest is True

            scrollbar.setValue(0)
            scrollbar.sliderMoved.emit(0)
            scrollbar.sliderReleased.emit()
            app.processEvents()
            assert widget._follow_controller.following_latest is False

            second = self._make_stream((StreamSegment(kind="content", text="第一批输出\n" * 120),))
            widget.set_stream(second)
            app.processEvents()
            assert scrollbar.value() == 0

            scrollbar.setValue(scrollbar.maximum())
            scrollbar.sliderReleased.emit()
            app.processEvents()
            assert widget._follow_controller.following_latest is True

            third = self._make_stream((StreamSegment(kind="content", text="第一批输出\n" * 160),))
            widget.set_stream(third)
            app.processEvents()
            assert scrollbar.value() == scrollbar.maximum()
        finally:
            widget.close()
            widget.deleteLater()

    def test_reasoning_segment_growth_updates_header(self) -> None:
        widget = StreamDetailWidget()
        stream1 = self._make_stream((StreamSegment(kind="reasoning", text="R1"),))
        widget.set_stream(stream1)

        stream2 = self._make_stream((StreamSegment(kind="reasoning", text="R1R2"),))
        widget.set_stream(stream2)
        segment_widget = widget._segment_widgets[0]
        assert segment_widget.segment.text == "R1R2"
        assert "4 字" in segment_widget._toggle_label.text()

    def test_different_stream_rebuilds(self) -> None:
        widget = StreamDetailWidget()
        stream1 = self._make_stream((StreamSegment(kind="content", text="A"),), stream_id="s1")
        widget.set_stream(stream1)
        assert widget.segment_count == 1

        stream2 = self._make_stream(
            (
                StreamSegment(kind="content", text="B"),
                StreamSegment(kind="content", text="C"),
            ),
            stream_id="s2",
        )
        widget.set_stream(stream2)
        assert widget.segment_count == 2

    def test_collapse_all_reasoning(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream(
            (
                StreamSegment(kind="reasoning", text="R1"),
                StreamSegment(kind="content", text="C1"),
                StreamSegment(kind="reasoning", text="R2"),
            )
        )
        widget.set_stream(stream)
        widget.collapse_all_reasoning()
        for w in widget._segment_widgets:
            if w.segment.kind == "reasoning":
                assert w._body.isHidden()

    def test_expand_all_reasoning(self) -> None:
        widget = StreamDetailWidget()
        stream = self._make_stream((StreamSegment(kind="reasoning", text="R1"),))
        widget.set_stream(stream)
        widget.collapse_all_reasoning()
        widget.expand_all_reasoning()
        for w in widget._segment_widgets:
            if w.segment.kind == "reasoning":
                assert not w._body.isHidden()


class TestTaskFocusPanelStreamSignature:
    def test_stream_browser_is_bounded_and_wraps_to_widget_width(self) -> None:
        panel = TaskFocusPanel(prominent=True)
        try:
            assert panel._stream_browser.maximumHeight() == 420
            assert panel._stream_browser.minimumWidth() == 0
            assert (
                panel._stream_browser.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
            )
            assert panel._stream_browser.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
        finally:
            panel.shutdown()

    def test_compact_stream_ticker_does_not_force_horizontal_growth(self) -> None:
        panel = TaskFocusPanel(compact=True)
        try:
            assert (
                panel._compact_preview.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
            )
            assert panel._ticker_label.minimumWidth() == 0
            assert panel._ticker_label.wordWrap() is True
            assert panel._ticker_label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
        finally:
            panel.shutdown()

    def test_stream_html_uses_inline_wrap_guards(self) -> None:
        html = _html_from_text("A" * 400)

        assert "word-break: break-all" in html
        assert "overflow-wrap: anywhere" in html

    def test_stream_html_renders_eval_json_as_report_card(self) -> None:
        html = _html_from_text(
            json.dumps(
                {
                    "overall_score": 8.7,
                    "passed": True,
                    "scores": [
                        {
                            "dimension": "continuity",
                            "score": 8,
                            "comment": "承接稳定。",
                        }
                    ],
                    "summary": "质量可用。",
                },
                ensure_ascii=False,
            )
        )

        assert "report-card" in html
        assert "质量评估报告" in html
        assert "各维度评分" in html
        assert "json-card" not in html

    def test_reasoning_only_growth_changes_signature(self) -> None:
        panel = TaskFocusPanel()
        job = DesktopJobRecord(
            job_id="j1",
            kind="run_chapter",
            label="章节任务",
            status=DesktopJobState.RUNNING,
        )

        def state_with_reasoning(text: str) -> ObservedTaskState:
            stream = ObservedStreamState(
                job_id="j1",
                stream_id="s1",
                status="streaming",
                reasoning_text=text,
                reasoning_length=len(text),
                segments=(StreamSegment(kind="reasoning", text=text),),
                updated_at="same-timestamp",
            )
            return ObservedTaskState(
                job=job,
                focus_reason="当前节点输出",
                current_node="模型调用中",
                status_label="输出中",
                status_tone="warning",
                stream=stream,
            )

        try:
            assert panel._state_signature(state_with_reasoning("R1")) != panel._state_signature(
                state_with_reasoning("R1R2")
            )
        finally:
            panel.shutdown()


class TestPhaseProgressBar:
    def test_chapter_phase_index_maps_core_steps(self) -> None:
        assert _chapter_phase_index("run_chapter", "plan_chapter") == 0
        assert _chapter_phase_index("run_chapter", "draft_wave") == 1
        assert _chapter_phase_index("run_chapter", "continuity_repair") == 2
        assert _chapter_phase_index("run_chapter", "polish_reextract_canon") == 3
        assert _chapter_phase_index("run_chapter", "post_alignment_humanize") == 4
        assert _chapter_phase_index("run_chapter", "memory_updated") == 5

    def test_phase_progress_bar_updates_segment_state(self) -> None:
        bar = PhaseProgressBar()

        bar.set_job_state("run_chapter", "continuity_repair", "running")

        assert bar.isVisible()
        states = [segment.property("state") for segment in bar._segments]
        assert states[:10] == ["complete"] * 8 + ["current", "pending"]

    def test_phase_progress_bar_ignores_prompt_pressure_current_step(self) -> None:
        bar = PhaseProgressBar()
        job = DesktopJobRecord(
            job_id="j-prompt-pressure",
            kind="run_chapter",
            label="章节任务",
            status=DesktopJobState.RUNNING,
            current_step="prompt_pressure",
            events=[
                DesktopJobEvent(at="t1", step="draft"),
                DesktopJobEvent(
                    at="t2",
                    step="prompt_pressure",
                    payload={"task": "DRAFT_CHAPTER", "token_pressure": 0.82},
                ),
            ],
        )

        bar.set_job(job)

        states = [segment.property("state") for segment in bar._segments]
        assert states[:6] == ["complete"] * 4 + ["current", "pending"]

    def test_phase_progress_bar_uses_current_step_not_stale_future_history(self) -> None:
        bar = PhaseProgressBar()
        job = DesktopJobRecord(
            job_id="j-resume",
            kind="resolve_chapter_checkpoint_finalize",
            label="归档执行 · 山风与归人 / 第 1 章",
            status=DesktopJobState.RUNNING,
            current_step="post_guard_repair",
            events=[
                DesktopJobEvent(at="t1", step="memory_updated"),
                DesktopJobEvent(at="t2", step="post_guard_repair"),
            ],
        )

        bar.set_job(job)

        states = [segment.property("state") for segment in bar._segments]
        assert states == [
            "complete",
            "current",
            "pending",
            "pending",
            "pending",
            "pending",
            "pending",
        ]

    def test_phase_progress_bar_never_regresses_or_starts_all_grey(self) -> None:
        bar = PhaseProgressBar()
        job = DesktopJobRecord(
            job_id="j-monotonic",
            kind="resolve_chapter_checkpoint_finalize",
            label="归档执行 · 山风与归人 / 第 1 章",
            status=DesktopJobState.RUNNING,
            current_step="",
        )

        bar.set_job(job)
        assert [segment.property("state") for segment in bar._segments][0] == "current"

        job.current_step = "polish_reextract_canon"
        bar.set_job(job)
        job.current_step = "post_guard_repair"
        bar.set_job(job)

        states = [segment.property("state") for segment in bar._segments]
        assert states[:3] == ["complete", "complete", "current"]

    def test_phase_progress_bar_complete_segment_click_emits_index(self) -> None:
        bar = PhaseProgressBar()
        clicked: list[int] = []
        bar.phase_clicked.connect(clicked.append)

        bar.set_job_state("run_chapter", "continuity_repair", "running")
        bar.show()
        _qapp().processEvents()

        QTest.mouseClick(bar._segments[0], Qt.MouseButton.LeftButton)
        QTest.mouseClick(bar._segments[8], Qt.MouseButton.LeftButton)

        assert clicked == [0]
        assert bar.step_key_at(0) == "state_packet"

    def test_phase_progress_bar_uses_task_flow_for_finalize_job(self) -> None:
        bar = PhaseProgressBar()
        job = DesktopJobRecord(
            job_id="j-finalize",
            kind="resolve_chapter_checkpoint_finalize",
            label="归档执行 · 山风与归人 / 第 1 章",
            status=DesktopJobState.RUNNING,
            current_step="state_adjudication_final",
            events=[
                DesktopJobEvent(at="t1", step="guard_checkpoint"),
                DesktopJobEvent(at="t2", step="post_guard_repair"),
                DesktopJobEvent(at="t3", step="polish_reextract_canon"),
                DesktopJobEvent(at="t4", step="state_adjudication_final"),
            ],
        )

        bar.set_job(job)

        labels = [segment.text() for segment in bar._segments]
        states = [segment.property("state") for segment in bar._segments]
        assert labels[:4] == ["归档选择", "归档前修复", "状态提取", "正文落盘"]
        assert states[:5] == ["complete", "complete", "current", "pending", "pending"]

    def test_task_focus_panel_phase_click_emits_artifact_context(self) -> None:
        panel = TaskFocusPanel()
        job = DesktopJobRecord(
            job_id="j-artifact",
            kind="run_chapter",
            label="方案执行 · 山风与归人 / 第 2 章",
            project_id="long_demo",
            status=DesktopJobState.RUNNING,
            current_step="continuity_repair",
        )
        panel._current_state = ObservedTaskState(
            job=job,
            focus_reason="当前节点输出",
            current_node="连贯性修复",
            status_label="运行中",
            status_tone="warning",
        )
        received: list[tuple[str, str, str, str, int]] = []
        panel.artifact_requested.connect(
            lambda project_id, job_kind, step_key, step_label, chapter_number: received.append(
                (project_id, job_kind, step_key, step_label, chapter_number)
            )
        )

        try:
            panel._phase_progress.set_job(job)
            panel._on_phase_clicked(0)
        finally:
            panel.shutdown()

        assert received == [("long_demo", "run_chapter", "state_packet", "准备上下文", 2)]


class TestFloatingStreamWindow:
    def test_window_uses_theme_managed_background_and_tab_style(self) -> None:
        window = FloatingStreamWindow()
        try:
            assert window.objectName() == "floatingStreamWindow"
            from novel_forge.desktop.theme import get_stylesheet

            assert window.styleSheet() == ""
            assert "QFrame#floatingStreamWindow" in get_stylesheet()
            assert window._tabs.objectName() == "floatingStreamTabs"
            assert window._tabs.documentMode() is True
            assert window.width() <= 560
        finally:
            window.close()
            window.deleteLater()

    def test_stream_selector_can_show_previous_step_reasoning(self) -> None:
        store = TaskObservationStore()
        job = DesktopJobRecord(
            job_id="j-history",
            kind="run_chapter",
            label="章节任务",
            status=DesktopJobState.RUNNING,
            events=[
                DesktopJobEvent(
                    at="2026-06-18T10:00:00+00:00",
                    step="llm_stream_start",
                    payload={"stream_id": "s1", "task": "PLAN_CHAPTER", "attempt": 1},
                ),
                DesktopJobEvent(
                    at="2026-06-18T10:00:01+00:00",
                    step="llm_stream_delta",
                    payload={
                        "stream_id": "s1",
                        "segments": [{"kind": "reasoning", "text": "旧步骤思考"}],
                    },
                ),
                DesktopJobEvent(
                    at="2026-06-18T10:00:02+00:00",
                    step="llm_stream_end",
                    payload={"stream_id": "s1"},
                ),
                DesktopJobEvent(
                    at="2026-06-18T10:00:03+00:00",
                    step="llm_stream_start",
                    payload={"stream_id": "s2", "task": "DRAFT_CHAPTER", "attempt": 1},
                ),
                DesktopJobEvent(
                    at="2026-06-18T10:00:04+00:00",
                    step="llm_stream_delta",
                    payload={
                        "stream_id": "s2",
                        "segments": [{"kind": "reasoning", "text": "新步骤思考"}],
                    },
                ),
            ],
        )
        store.ingest_jobs([job])
        state = store.focus_for_scope(TaskFocusScope.GLOBAL)
        window = FloatingStreamWindow()
        try:
            window.bind_store(store)
            window.update_state(state)

            assert window._stream_select.count() == 3
            assert window._stream_detail._current_stream is not None
            assert window._stream_detail._current_stream.stream_id == "s2"

            old_index = next(
                index
                for index in range(window._stream_select.count())
                if window._stream_select.itemData(index) == "s1"
            )
            window._stream_select.setCurrentIndex(old_index)

            assert window._stream_detail._current_stream is not None
            assert window._stream_detail._current_stream.stream_id == "s1"
            assert window._stream_detail._segment_widgets[0].segment.text == "旧步骤思考"
        finally:
            window.close()
            window.deleteLater()

    def test_model_call_panel_collapsed_height_leaves_wrapped_hint_room(self) -> None:
        panel = TaskModelCallPanel()
        try:
            assert panel.maximumHeight() >= 120
            assert panel._content_scroll.isVisible() is False
        finally:
            panel.deleteLater()

    def test_position_near_anchor_uses_screen_coordinates(self) -> None:
        app = _qapp()
        anchor = QWidget()
        window = FloatingStreamWindow()
        try:
            anchor.resize(80, 80)
            anchor.move(100, 100)
            anchor.show()
            app.processEvents()

            window.position_near_anchor(anchor)

            screen = QApplication.screenAt(anchor.mapToGlobal(QPoint(40, 40)))
            screen = screen or QApplication.primaryScreen()
            assert screen is not None
            available = screen.availableGeometry()
            assert available.contains(window.geometry().topLeft())
            assert window.width() <= 560
        finally:
            anchor.close()
            window.close()
            anchor.deleteLater()
            window.deleteLater()
