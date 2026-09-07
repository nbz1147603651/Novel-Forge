"""Tests for streaming segments, reasoning capture, and incremental ingest."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.task_observation import (
    StreamSegment,
    TaskObservationStore,
)


def _qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


@pytest.fixture(autouse=True)
def _qapp_fixture():
    _qapp()
    yield


def _event(step: str, payload: dict, at: str) -> DesktopJobEvent:
    return DesktopJobEvent(at=at, step=step, payload=payload)


def _job(
    job_id: str = "j1",
    *,
    events: list[DesktopJobEvent] | None = None,
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind="run_chapter",
        label=f"任务 {job_id}",
        project_id="book",
        status=DesktopJobState.RUNNING,
        updated_at="2026-06-18T10:00:05+00:00",
        events=events or [],
    )


class TestStreamSegments:
    """Verify that ordered content/reasoning segments are preserved."""

    def test_segments_from_delta_events(self) -> None:
        """llm_stream_delta with segments payload populates ObservedStreamState.segments."""
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1", "task": "DRAFT_CHAPTER"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [
                    {"kind": "reasoning", "text": "思考过程"},
                    {"kind": "content", "text": "正文"},
                ],
            }, "t1"),
        ])
        store.ingest_jobs([job])
        streams = store._streams_by_job.get("j1", ())
        assert len(streams) == 1
        stream = streams[0]
        assert len(stream.segments) == 2
        assert stream.segments[0] == StreamSegment(kind="reasoning", text="思考过程")
        assert stream.segments[1] == StreamSegment(kind="content", text="正文")
        assert stream.text == "正文"
        assert stream.reasoning_text == "思考过程"

    def test_segments_interleaved_order_preserved(self) -> None:
        """Multiple delta events with alternating kinds preserve real interleaving."""
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "reasoning", "text": "R1"}],
            }, "t1"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "C1"}],
            }, "t2"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "reasoning", "text": "R2"}],
            }, "t3"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "C2"}],
            }, "t4"),
        ])
        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]
        kinds = [seg.kind for seg in stream.segments]
        assert kinds == ["reasoning", "content", "reasoning", "content"]
        texts = [seg.text for seg in stream.segments]
        assert texts == ["R1", "C1", "R2", "C2"]

    def test_same_kind_consecutive_segments_merged(self) -> None:
        """Consecutive same-kind chunks merge into one segment."""
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "hello "}],
            }, "t1"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "world"}],
            }, "t2"),
        ])
        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]
        assert len(stream.segments) == 1
        assert stream.segments[0].text == "hello world"

    def test_legacy_delta_field_still_works(self) -> None:
        """Old-style delta field (without segments) is treated as content."""
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "delta": "legacy text",
            }, "t1"),
        ])
        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]
        assert stream.text == "legacy text"
        assert len(stream.segments) == 1
        assert stream.segments[0].kind == "content"

    def test_legacy_text_after_structured_segments_is_ignored(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "正文"}],
            }, "t1"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "text": "正文正文",
                "delta": "正文",
            }, "t2"),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.text == "正文"
        assert stream.segments == (StreamSegment(kind="content", text="正文"),)

    def test_stream_end_adds_final_reasoning_when_delta_had_only_content(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "正文"}],
            }, "t1"),
            _event("llm_stream_end", {
                "stream_id": "s1",
                "text": "正文",
                "reasoning": "最终思考",
            }, "t2"),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.status == "complete"
        assert stream.text == "正文"
        assert stream.reasoning_text == "最终思考"
        assert stream.segments[0] == StreamSegment(kind="reasoning", text="最终思考")

    def test_stream_end_preserves_reasoning_when_final_text_is_normalized(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [
                    {"kind": "reasoning", "text": "R1"},
                    {"kind": "content", "text": "```text\n正文\n```"},
                ],
            }, "t1"),
            _event("llm_stream_end", {
                "stream_id": "s1",
                "text": "正文",
                "reasoning": "R1",
            }, "t2"),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.text == "正文"
        assert stream.reasoning_text == "R1"

    def test_transport_restart_clears_failed_route_output_without_discarding_stream(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "operation_id": "op1", "output_kind": "text"},
                "t0",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "segments": [
                        {"kind": "reasoning", "text": "旧思考"},
                        {"kind": "content", "text": "旧正文"},
                    ],
                },
                "t1",
            ),
            _event(
                "llm_stream_restart",
                {"stream_id": "s1", "reset_output": True, "message": "切换备用路由"},
                "t2",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "segments": [
                        {"kind": "reasoning", "text": "新思考"},
                        {"kind": "content", "text": "新正文"},
                    ],
                },
                "t3",
            ),
            _event(
                "llm_stream_end",
                {"stream_id": "s1", "text": "新正文", "reasoning": "新思考"},
                "t4",
            ),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.status == "complete"
        assert stream.discarded is False
        assert stream.text == "新正文"
        assert stream.reasoning_text == "新思考"
        assert stream.operation_id == "op1"

    def test_json_stream_uses_backend_validation_and_authoritative_snapshot(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event(
                "llm_stream_start",
                {
                    "stream_id": "json1",
                    "operation_id": "op-json",
                    "output_kind": "json",
                },
                "t0",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "json1",
                    "segments": [{"kind": "content", "text": '{"summary":"草稿"}'}],
                },
                "t1",
            ),
            _event(
                "llm_stream_end",
                {
                    "stream_id": "json1",
                    "output_kind": "json",
                    "validation_status": "validating",
                    "text": '{"summary":"草稿"}',
                },
                "t2",
            ),
            _event(
                "llm_stream_validation",
                {
                    "stream_id": "json1",
                    "output_kind": "json",
                    "validation_status": "validated",
                    "repair_source": "local",
                    "text": '{"summary":"已修复"}',
                    "text_length": len('{"summary":"已修复"}'),
                },
                "t3",
            ),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.status == "validated"
        assert stream.output_kind == "json"
        assert stream.validation_status == "validated"
        assert stream.repair_source == "local"
        assert stream.text == '{"summary":"已修复"}'
        assert stream.text_length == len(stream.text)

    def test_syntactically_valid_json_is_diagnostic_when_backend_validation_fails(self) -> None:
        store = TaskObservationStore()
        job = _job(events=[
            _event(
                "llm_stream_start",
                {"stream_id": "json1", "output_kind": "json"},
                "t0",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "json1",
                    "segments": [{"kind": "content", "text": '{"summary":"合法 JSON"}'}],
                },
                "t1",
            ),
            _event(
                "llm_stream_validation",
                {
                    "stream_id": "json1",
                    "validation_status": "failed",
                    "error": "缺少业务字段 chapter_id",
                },
                "t2",
            ),
        ])

        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]

        assert stream.status == "validation_failed"
        assert stream.validation_status == "failed"
        assert stream.error == "缺少业务字段 chapter_id"
        assert stream.text == '{"summary":"合法 JSON"}'


class TestIncrementalIngest:
    """Verify the fingerprint-based incremental cursor and sliding window fallback."""

    def test_incremental_only_processes_new_events(self) -> None:
        """Second ingest with appended events only processes the new tail."""
        store = TaskObservationStore()
        job1 = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "part1"}],
            }, "t1"),
        ])
        store.ingest_jobs([job1])
        stream = store._streams_by_job["j1"][0]
        assert stream.text == "part1"

        # Append new events; the store should only process the new tail.
        job2 = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "part1"}],
            }, "t1"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "part2"}],
            }, "t2"),
        ])
        store.ingest_jobs([job2])
        stream = store._streams_by_job["j1"][0]
        assert stream.text == "part1part2"

    def test_sliding_window_eviction_triggers_full_replay(self) -> None:
        """When the event window slides past the cursor, a full replay occurs."""
        store = TaskObservationStore()
        # First ingest with one event.
        job1 = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
        ])
        store.ingest_jobs([job1])

        # Second ingest: the old event is gone (simulating sliding window),
        # replaced by completely different events.  The fingerprint won't
        # match, so a full replay should occur.
        job2 = _job(events=[
            _event("llm_stream_start", {"stream_id": "s2"}, "t1"),
            _event("llm_stream_delta", {
                "stream_id": "s2",
                "segments": [{"kind": "content", "text": "replayed"}],
            }, "t2"),
        ])
        store.ingest_jobs([job2])
        # The old stream s1 should be gone (full replay reset accumulators).
        streams = store._streams_by_job["j1"]
        stream_ids = {s.stream_id for s in streams}
        assert "s1" not in stream_ids
        assert "s2" in stream_ids
        assert streams[0].text == "replayed"

    def test_job_removal_clears_accumulators(self) -> None:
        """When a job disappears, its accumulators are cleaned up."""
        store = TaskObservationStore()
        job = _job(events=[
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
        ])
        store.ingest_jobs([job])
        assert "j1" in store._stream_accum

        store.ingest_jobs([])
        assert "j1" not in store._stream_accum

    def test_repeated_ingest_same_events_idempotent(self) -> None:
        """Ingesting the same events twice produces the same state."""
        store = TaskObservationStore()
        events = [
            _event("llm_stream_start", {"stream_id": "s1"}, "t0"),
            _event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "text"}],
            }, "t1"),
        ]
        job = _job(events=events)
        store.ingest_jobs([job])
        stream1 = store._streams_by_job["j1"][0]

        store.ingest_jobs([job])
        stream2 = store._streams_by_job["j1"][0]
        assert stream1.text == stream2.text
        assert stream1.segments == stream2.segments


class TestAccumulatorTruncation:
    """Verify the 50KB accumulator limit drops oldest segments."""

    def test_long_stream_truncates_oldest(self) -> None:
        """When total text exceeds the limit, oldest segments are dropped."""
        store = TaskObservationStore()
        # Build events with enough text to exceed 50KB.
        big_chunk = "x" * 10_000  # 10KB per chunk
        events = [_event("llm_stream_start", {"stream_id": "s1"}, "t0")]
        for i in range(10):  # 100KB total, well over 50KB limit
            events.append(_event("llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": big_chunk}],
            }, f"t{i+1}"))
        job = _job(events=events)
        store.ingest_jobs([job])
        stream = store._streams_by_job["j1"][0]
        # Total text should be under the limit (oldest dropped).
        assert stream.text_length <= 50_000
        assert len(stream.text) <= 50_000
        assert stream.truncated is True
