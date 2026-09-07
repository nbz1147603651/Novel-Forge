from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy

import novel_forge.desktop.components.task_focus as task_focus_components
from novel_forge.app_service.contracts import JobEvent, JobEventType
from novel_forge.desktop.components.task_focus import (
    FloatingTaskCompanion,
    TaskFocusDialog,
    TaskFocusPanel,
    TaskModelCallPanel,
    TaskSwitcherBar,
    _html_from_text,
)
from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobManager,
    DesktopJobRecord,
    DesktopJobState,
    _coalesce_stream_delta_events,
    _compact_payload,
)
from novel_forge.desktop.model_call_observation import load_model_call_snapshot
from novel_forge.desktop.task_observation import TaskFocusScope, TaskObservationStore


def _qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


@pytest.fixture(autouse=True)
def _flush_qt_deferred_deletes():
    yield
    app = QApplication.instance()
    if app is not None:
        for widget in list(app.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _event(step: str, payload: dict, at: str) -> DesktopJobEvent:
    return DesktopJobEvent(at=at, step=step, payload=payload)


def _job(
    job_id: str,
    *,
    kind: str = "run_chapter",
    label: str | None = None,
    project_id: str = "book",
    status: DesktopJobState = DesktopJobState.RUNNING,
    events: list[DesktopJobEvent] | None = None,
    updated_at: str = "2026-06-18T10:00:05+00:00",
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind=kind,
        label=label or f"任务 {job_id} / 第 3 章",
        project_id=project_id,
        status=status,
        updated_at=updated_at,
        events=events or [],
    )


def _streaming_events(
    stream_id: str,
    *,
    text: str = "正文",
    task: str = "DRAFT_CHAPTER",
    at: str = "2026-06-18T10:00:01+00:00",
) -> list[DesktopJobEvent]:
    return [
        _event(
            "llm_stream_start",
            {"stream_id": stream_id, "task": task},
            "2026-06-18T10:00:00+00:00",
        ),
        _event(
            "llm_stream_delta",
            {"stream_id": stream_id, "delta": text[-1:] or text, "text": text},
            at,
        ),
    ]


def _decision_events(
    decision_id: str,
    *,
    title: str = "确认动作",
    at: str = "2026-06-18T10:00:00+00:00",
) -> list[DesktopJobEvent]:
    return [
        _event(
            "human_decision_requested",
            {
                "decision_id": decision_id,
                "title": title,
                "message": "是否继续？",
                "options": [
                    {"id": "allow", "label": "允许"},
                    {"id": "deny", "label": "拒绝"},
                ],
                "default_option": "deny",
            },
            at,
        )
    ]


def _write_model_call(
    run_dir: Path,
    file_name: str,
    *,
    task: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    content: str = "输出正文",
    event: str = "api_call_done",
) -> Path:
    calls_dir = run_dir / "model_calls"
    calls_dir.mkdir(parents=True, exist_ok=True)
    path = calls_dir / file_name
    payload = {
        "event": event,
        "recorded_at": "2026-06-18T10:00:02+00:00",
        "call_id": file_name,
        "task": task,
        "provider": provider,
        "model": model,
        "route": "primary",
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": 0.012,
        "max_tokens": 4096,
        "temperature": 0.7,
        "request": {
            "task_type": task,
            "messages": [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "用户提示"},
            ],
        },
        "response": {
            "content": content,
            "model_id": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": 0.012,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_task_observation_candidates_and_state_lookup() -> None:
    store = TaskObservationStore()
    running = _job("running", updated_at="2026-06-18T10:00:02+00:00")
    streaming = _job(
        "streaming",
        events=_streaming_events("s1"),
        updated_at="2026-06-18T10:00:01+00:00",
    )
    decision = _job(
        "decision",
        events=_decision_events("d1"),
        updated_at="2026-06-18T09:59:00+00:00",
    )
    old_done = _job(
        "old",
        status=DesktopJobState.SUCCEEDED,
        updated_at="2020-01-01T00:00:00+00:00",
    )

    store.ingest_jobs([running, streaming, decision, old_done])

    candidates = store.candidates_for_scope(TaskFocusScope.GLOBAL)
    assert [candidate.job_id for candidate in candidates] == ["decision", "streaming", "running"]
    assert store.focus_for_scope(TaskFocusScope.GLOBAL).job_id == "decision"  # type: ignore[union-attr]
    assert store.state_for_job_id("streaming", TaskFocusScope.GLOBAL).job_id == "streaming"  # type: ignore[union-attr]
    assert store.state_for_job_id("missing", TaskFocusScope.GLOBAL) is None
    assert store.state_for_job_id("old", TaskFocusScope.GLOBAL) is None
    assert (
        store.state_for_job_id(
            "running",
            TaskFocusScope.CHAPTER,
            project_id="other",
            chapter_number=3,
        )
        is None
    )


def test_attention_groups_collapse_same_project_history_but_keep_stream_primary() -> None:
    store = TaskObservationStore()
    old_failed = _job(
        "old_failed",
        status=DesktopJobState.FAILED,
        updated_at="2020-01-01T00:00:00+00:00",
    )
    completed = _job(
        "completed",
        status=DesktopJobState.SUCCEEDED,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    streaming = _job(
        "streaming",
        events=_streaming_events("s1", text="流式正文"),
        updated_at="2026-06-18T10:00:05+00:00",
    )

    store.ingest_jobs([old_failed, completed, streaming])

    candidates = store.candidates_for_scope(TaskFocusScope.GLOBAL)
    assert [candidate.job_id for candidate in candidates] == ["streaming", "completed"]
    groups = store.attention_groups_for_scope(TaskFocusScope.GLOBAL)
    assert len(groups) == 1
    assert groups[0].label == "book"
    assert groups[0].primary.job_id == "streaming"
    assert groups[0].live_stream_count == 1
    assert groups[0].history_count == 1


def test_attention_count_is_project_level_for_multiple_active_jobs() -> None:
    store = TaskObservationStore()
    running = _job("running")
    streaming = _job("streaming", events=_streaming_events("s1"))
    other_project = _job("other", project_id="other-book")

    store.ingest_jobs([running, streaming, other_project])

    groups = store.attention_groups_for_scope(TaskFocusScope.GLOBAL)
    assert [group.label for group in groups] == ["book", "other-book"]
    assert groups[0].active_count == 2
    assert store.active_attention_count() == 2


def test_stale_failed_jobs_do_not_pollute_attention_candidates() -> None:
    store = TaskObservationStore()
    stale_failed = _job(
        "stale_failed",
        status=DesktopJobState.FAILED,
        updated_at="2020-01-01T00:00:00+00:00",
    )
    running = _job("running", project_id="current")

    store.ingest_jobs([stale_failed, running])

    candidates = store.candidates_for_scope(TaskFocusScope.GLOBAL)
    assert [candidate.job_id for candidate in candidates] == ["running"]
    assert store.active_attention_count() == 1


def test_model_call_snapshot_loads_totals_and_previews(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "run-1"
    _write_model_call(
        run_dir,
        "001_draft.json",
        task="DRAFT_CHAPTER",
        provider="openai",
        model="gpt-4o",
        prompt_tokens=120,
        completion_tokens=80,
        latency_ms=1500,
        content="第一段输出",
    )
    _write_model_call(
        run_dir,
        "002_wave.json",
        task="WAVE_CHAPTER",
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=200,
        completion_tokens=100,
        latency_ms=2500,
        content="第二段输出",
    )
    job = _job("calls")
    job.result["run_log_dir"] = str(run_dir)

    snapshot = load_model_call_snapshot(job)

    assert snapshot.total_calls == 2
    assert snapshot.total_tokens == 500
    assert snapshot.prompt_tokens == 320
    assert snapshot.completion_tokens == 180
    assert snapshot.provider_token_totals()[0] == ("deepseek / deepseek-chat", 300)
    assert "用户提示" in snapshot.records[0].request_preview
    assert snapshot.records[1].response_preview == "第二段输出"


def test_model_call_snapshot_explains_live_empty_response_retry(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "run-retry"
    path = _write_model_call(
        run_dir,
        "001_plan_empty_response.json",
        task="PLAN_CHAPTER",
        provider="opencode",
        model="deepseek-v4-flash",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=91940,
        content="",
        event="api_call_empty_response",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(
        {
            "response": {},
            "finish_reason": "stop",
            "attempt": 1,
            "max_attempts": 3,
            "will_retry": True,
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    job = _job(
        "retrying-call",
        events=[
            _event(
                "model_call_update",
                {
                    "event": "api_call_empty_response",
                    "status": "retrying",
                    "call_id": "call-1",
                    "task": "PLAN_CHAPTER",
                    "provider": "opencode",
                    "model": "deepseek-v4-flash",
                    "max_tokens": 19360,
                    "attempt": 1,
                    "max_attempts": 3,
                },
                "2026-06-18T10:01:33+00:00",
            )
        ],
    )
    job.result["run_log_dir"] = str(run_dir)

    snapshot = load_model_call_snapshot(job)

    assert snapshot.records[0].status == "empty_response"
    assert snapshot.records[0].will_retry is True
    assert "空响应" in snapshot.records[0].response_preview
    assert "自动发起" in snapshot.records[0].response_preview
    assert snapshot.retry_count == 1
    assert snapshot.terminal_failed_count == 0
    assert snapshot.activity is not None
    assert snapshot.activity.status == "retrying"


def test_model_call_snapshot_finds_run_dir_from_events_and_error_chain(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "run-err"
    _write_model_call(
        run_dir,
        "001_stream.json",
        task="DRAFT_CHAPTER",
        provider="openai",
        model="gpt-4o",
        prompt_tokens=40,
        completion_tokens=20,
        latency_ms=900,
        content="流式最终输出",
        event="api_stream_done",
    )
    event_job = _job(
        "event-dir",
        events=[
            _event(
                "run_log_started",
                {"run_log_dir": str(run_dir), "run_id": "run-err"},
                "2026-06-18T10:00:00+00:00",
            )
        ],
    )
    error_job = _job("error-dir", status=DesktopJobState.FAILED)
    error_job.error_summary = {
        "detail": "RuntimeError: boom",
        "chain": [f"RuntimeError: boom\n运行日志：{run_dir}"],
    }

    event_snapshot = load_model_call_snapshot(event_job)
    error_snapshot = load_model_call_snapshot(error_job)

    assert event_snapshot.total_calls == 1
    assert event_snapshot.records[0].status == "success"
    assert event_snapshot.records[0].response_preview == "流式最终输出"
    assert error_snapshot.total_calls == 1


def test_task_observation_stream_restart_discards_old_stream() -> None:
    store = TaskObservationStore()
    job = _job(
        "j1",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "task": "DRAFT_CHAPTER", "chapter": 3, "attempt": 1},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s1", "delta": "旧正文", "text_length": 3},
                "2026-06-18T10:00:01+00:00",
            ),
            _event(
                "llm_stream_restart",
                {"stream_id": "s1", "attempt": 1, "error": "retry"},
                "2026-06-18T10:00:02+00:00",
            ),
            _event(
                "llm_stream_start",
                {"stream_id": "s2", "task": "DRAFT_CHAPTER", "chapter": 3, "attempt": 2},
                "2026-06-18T10:00:03+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s2", "delta": "新正文", "text_length": 3},
                "2026-06-18T10:00:04+00:00",
            ),
            _event(
                "llm_stream_end",
                {"stream_id": "s2", "text_length": 3},
                "2026-06-18T10:00:05+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.stream_id == "s2"
    assert focus.stream.text == "新正文"
    assert focus.stream.status == "complete"
    assert any("旧预览已丢弃" in item for item in focus.diagnostics)


def test_model_call_running_is_visible_when_no_llm_stream_exists() -> None:
    store = TaskObservationStore()
    job = _job(
        "json-call",
        events=[
            _event(
                "model_call_update",
                {"call_id": "c1", "task": "PLAN_OUTLINE", "status": "running"},
                "2026-06-18T10:00:00+00:00",
            )
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.source == "model_call"
    assert focus.stream.status == "running"
    assert focus.has_live_stream is False
    assert focus.status_label == "调用中"
    groups = store.attention_groups_for_scope(TaskFocusScope.GLOBAL)
    assert groups[0].live_stream_count == 0


def test_model_call_retrying_remains_live_and_explains_reason() -> None:
    store = TaskObservationStore()
    job = _job(
        "json-retry",
        events=[
            _event(
                "model_call_update",
                {
                    "call_id": "c1",
                    "task": "PLAN_CHAPTER",
                    "status": "retrying",
                    "event": "api_call_length_truncated",
                    "max_tokens": 19360,
                    "next_max_tokens": 38720,
                    "attempt": 2,
                    "max_attempts": 9,
                },
                "2026-06-18T10:05:00+00:00",
            )
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.status == "running"
    assert focus.status_label == "调用中"
    assert any("模型调用自动重试" in event for event in focus.events)
    assert any("输出上限提升至 38720" in event for event in focus.events)


def test_stream_cursor_loss_rebuilds_from_run_log_before_tail_events(tmp_path: Path) -> None:
    run_dir = tmp_path / "logs" / "run-stream"
    events_path = run_dir / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    history_entries = [
        {
            "ts": "2026-06-18T10:00:00+00:00",
            "level": "INFO",
            "event": "step",
            "data": {
                "step": "llm_stream_start",
                "data": {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
            },
        },
        {
            "ts": "2026-06-18T10:00:01+00:00",
            "level": "INFO",
            "event": "step",
            "data": {
                "step": "llm_stream_delta",
                "data": {
                    "stream_id": "s1",
                    "segments": [{"kind": "content", "text": "hello "}],
                },
            },
        },
    ]
    events_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in history_entries) + "\n",
        encoding="utf-8",
    )
    first = _job(
        "j1",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s1", "segments": [{"kind": "content", "text": "hello "}]},
                "2026-06-18T10:00:01+00:00",
            ),
        ],
    )
    first.result["run_log_dir"] = str(run_dir)
    store = TaskObservationStore()
    store.ingest_jobs([first])

    tail_only = _job(
        "j1",
        events=[
            _event(
                "llm_stream_delta",
                {"stream_id": "s1", "segments": [{"kind": "content", "text": "world"}]},
                "2026-06-18T10:00:02+00:00",
            )
        ],
    )
    tail_only.result["run_log_dir"] = str(run_dir)

    store.ingest_jobs([tail_only])
    stream = store._streams_by_job["j1"][0]

    assert stream.text == "hello world"


def test_task_observation_pending_decision_wins_focus() -> None:
    store = TaskObservationStore()
    streaming = _job(
        "streaming",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s", "task": "WAVE_CHAPTER"},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s", "delta": "正文"},
                "2026-06-18T10:00:01+00:00",
            ),
        ],
    )
    decision = _job(
        "decision",
        updated_at="2026-06-18T09:59:00+00:00",
        events=[
            _event(
                "human_decision_requested",
                {
                    "decision_id": "d1",
                    "title": "确认升级修复",
                    "message": "是否允许全文修复？",
                    "options": [
                        {"id": "allow", "label": "允许"},
                        {"id": "deny", "label": "跳过"},
                    ],
                    "default_option": "deny",
                    "timeout_seconds": 300,
                },
                "2026-06-18T09:59:00+00:00",
            )
        ],
    )

    store.ingest_jobs([streaming, decision])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.job_id == "decision"
    assert focus.decision is not None
    assert focus.decision.status == "pending"


def test_task_observation_external_decision_request_is_visible_before_job_rebind() -> None:
    store = TaskObservationStore()
    job = _job("j1")
    store.ingest_jobs([job])
    store.ingest_decision_required(
        "j1",
        {
            "decision_id": "d2",
            "title": "确认 rewrite",
            "message": "策略诊断建议 rewrite。",
            "options": [{"id": "continue_without_escalation", "label": "保守继续"}],
            "default_option": "continue_without_escalation",
        },
    )

    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.decision is not None
    assert focus.decision.decision_id == "d2"


def test_task_observation_chapter_scope_keeps_same_project_fallback() -> None:
    store = TaskObservationStore()
    current_chapter = _job(
        "current",
        label="任务 current / 第 5 章",
        updated_at="2026-06-18T09:59:00+00:00",
    )
    other_chapter = _job(
        "other",
        label="任务 other / 第 9 章",
        updated_at="2026-06-18T10:00:00+00:00",
    )

    store.ingest_jobs([other_chapter])
    fallback = store.focus_for_scope(
        TaskFocusScope.CHAPTER,
        project_id="book",
        chapter_number=5,
    )
    assert fallback is not None
    assert fallback.job_id == "other"

    store.ingest_jobs([other_chapter, current_chapter])
    focus = store.focus_for_scope(
        TaskFocusScope.CHAPTER,
        project_id="book",
        chapter_number=5,
    )

    assert focus is not None
    assert focus.job_id == "current"


def test_stream_compact_payload_keeps_delta_and_metadata() -> None:
    payload = _compact_payload(
        "llm_stream_delta",
        {
            "stream_id": "s1",
            "task": "DRAFT_CHAPTER",
            "chapter": 3,
            "attempt": 2,
            "delta": "一段正在流出的正文",
            "text_length": 9,
            "ignored": "value",
        },
    )

    assert payload["stream_id"] == "s1"
    assert payload["task"] == "DRAFT_CHAPTER"
    assert payload["delta"] == "一段正在流出的正文"
    assert payload["text_length"] == 9
    assert "ignored" not in payload


def test_model_call_update_compact_payload_and_observation_preview() -> None:
    compacted = _compact_payload(
        "model_call_update",
        {
            "event": "api_call_done",
            "status": "success",
            "run_log_dir": "data/book/logs/run",
            "call_id": "c1",
            "task": "PLAN_CHAPTER",
            "total_tokens": 123,
            "response_preview": "结构化输出预览",
            "ignored": "value",
        },
    )
    assert compacted["response_preview"] == "结构化输出预览"
    assert compacted["total_tokens"] == 123
    assert "ignored" not in compacted

    store = TaskObservationStore()
    job = _job(
        "j1",
        events=[
            _event(
                "model_call_update",
                {
                    "event": "api_call_start",
                    "status": "running",
                    "call_id": "c1",
                    "task": "PLAN_CHAPTER",
                },
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "model_call_update",
                {
                    "event": "api_call_done",
                    "status": "success",
                    "call_id": "c1",
                    "task": "PLAN_CHAPTER",
                    "response_preview": "结构化输出预览",
                    "total_tokens": 123,
                },
                "2026-06-18T10:00:01+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.source == "model_call"
    assert focus.stream.text == "结构化输出预览"
    assert focus.status_label == "运行中"
    assert any("模型调用完成" in item for item in focus.events)


def test_model_call_retry_keeps_previous_block_preview_while_next_call_runs() -> None:
    store = TaskObservationStore()
    previous_json = json.dumps(
        {"人物": "魂玉", "状态": ["已生成", "等待格式重试"]},
        ensure_ascii=False,
        indent=2,
    )
    job = _job(
        "j1",
        events=[
            _event(
                "model_call_update",
                {
                    "event": "api_call_start",
                    "status": "running",
                    "call_id": "c1",
                    "task": "INIT_CHARACTER_PROFILE_BATCH",
                },
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "model_call_update",
                {
                    "event": "api_call_done",
                    "status": "success",
                    "call_id": "c1",
                    "task": "INIT_CHARACTER_PROFILE_BATCH",
                    "response_preview": previous_json,
                },
                "2026-06-18T10:00:01+00:00",
            ),
            _event(
                "model_call_update",
                {
                    "event": "api_call_start",
                    "status": "running",
                    "call_id": "c2",
                    "task": "INIT_CHARACTER_PROFILE_BATCH",
                },
                "2026-06-18T10:00:02+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.source == "model_call"
    assert focus.stream.stream_id == "c2"
    assert focus.status_label == "调用中"
    assert any("模型调用完成" in item for item in focus.events)
    assert any("模型调用中" in item for item in focus.events)


def test_task_focus_renders_json_preview_as_block() -> None:
    html = _html_from_text('{"人物":"魂玉","状态":["已生成"]}')

    assert "class='json-card'" in html
    assert "JSON 输出" in html
    assert "class='json-key'>人物</span>" in html
    assert "class='json-string'>魂玉</span>" in html
    assert "class='json-index'>1</span>" in html


def test_task_focus_renders_prefixed_json_preview_as_block() -> None:
    html = _html_from_text('上一轮模型输出预览（当前调用尚未返回）：\n\n{"人物":"魂玉"}')

    assert "上一轮模型输出预览" in html
    assert "class='json-card'" in html
    assert "class='json-key'>人物</span>" in html


def test_task_focus_keeps_incomplete_json_as_block_not_paragraphs() -> None:
    html = _html_from_text('{"人物":"魂玉"')

    assert "JSON 块尚未完成" in html
    assert "class='json-raw'" in html
    assert "<p>{&quot;人物&quot;" not in html


def test_stream_delta_does_not_update_current_step() -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    try:
        record = DesktopJobRecord(
            job_id="j1",
            kind="run_chapter",
            label="章节任务",
            project_id="book",
            status=DesktopJobState.RUNNING,
            current_step="draft",
        )
        manager._jobs[record.job_id] = record

        manager._handle_step(
            "j1",
            "llm_stream_delta",
            {"stream_id": "s1", "delta": "正文"},
        )

        assert manager._jobs["j1"].current_step == "draft"
        assert manager._jobs["j1"].events == []
    finally:
        manager.shutdown(wait_ms=100)


def test_app_service_stream_deltas_are_coalesced_before_desktop_handling() -> None:
    events = [
        JobEvent(
            job_id="j1",
            type=JobEventType.JOB_STEP,
            step="llm_stream_delta",
            payload={"stream_id": "s1", "delta": "你"},
        ),
        JobEvent(
            job_id="j1",
            type=JobEventType.JOB_STEP,
            step="llm_stream_delta",
            payload={"stream_id": "s1", "delta": "好"},
        ),
        JobEvent(
            job_id="j1",
            type=JobEventType.JOB_STEP,
            step="model_call_update",
            payload={"call_id": "c1", "status": "success"},
        ),
    ]

    coalesced = _coalesce_stream_delta_events(events)

    assert len(coalesced) == 2
    assert coalesced[0].step == "llm_stream_delta"
    assert coalesced[0].payload["delta"] == "你好"
    assert coalesced[1].step == "model_call_update"


def test_app_service_large_stream_batch_is_merged_in_one_group() -> None:
    events = [
        JobEvent(
            job_id="j1",
            type=JobEventType.JOB_STEP,
            step="llm_stream_delta",
            payload={"stream_id": "s1", "delta": str(index % 10)},
        )
        for index in range(1_000)
    ]

    coalesced = _coalesce_stream_delta_events(events)

    assert len(coalesced) == 1
    assert coalesced[0].event_id == events[-1].event_id
    assert coalesced[0].payload["delta"] == "".join(str(index % 10) for index in range(1_000))


def test_wake_flush_discards_only_stream_deltas_and_replays_terminal_event() -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    try:
        record = DesktopJobRecord(
            job_id="wake-job",
            kind="run_chapter",
            label="章节任务",
            project_id="book",
            status=DesktopJobState.RUNNING,
        )
        manager._jobs[record.job_id] = record
        manager._app_job_ids.add(record.job_id)

        queued = iter(
            [
                JobEvent(
                    job_id=record.job_id,
                    type=JobEventType.JOB_STEP,
                    step="llm_stream_delta",
                    payload={"stream_id": "s1", "delta": "过期增量"},
                ),
                JobEvent(
                    job_id=record.job_id,
                    type=JobEventType.JOB_STEP,
                    step="finalize",
                    payload={"tokens_so_far": 77},
                ),
                JobEvent(
                    job_id=record.job_id,
                    type=JobEventType.JOB_SUCCEEDED,
                    payload={"project_id": "book", "ok": True},
                ),
            ]
        )

        class _QueuedSubscription:
            @staticmethod
            def get(timeout: float = 0) -> JobEvent | None:  # noqa: ARG004
                return next(queued, None)

            @staticmethod
            def close() -> None:
                return None

        manager._app_event_subscription = _QueuedSubscription()

        discarded = manager.flush_stale_events_after_wake()

        assert discarded == 1
        assert record.status == DesktopJobState.SUCCEEDED
        assert record.current_step == "completed"
        assert record.cumulative_tokens == 77
        assert record.result["ok"] is True
        assert all(event.step != "llm_stream_delta" for event in record.events)
    finally:
        manager.shutdown(wait_ms=100)


def test_wake_flush_replays_large_backlog_without_blocking_gui_turn(qtbot) -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    try:
        record = DesktopJobRecord(
            job_id="wake-backlog",
            kind="run_chapter",
            label="章节任务",
            project_id="book",
            status=DesktopJobState.RUNNING,
        )
        manager._jobs[record.job_id] = record
        manager._app_job_ids.add(record.job_id)

        queued = iter(
            [
                JobEvent(
                    job_id=record.job_id,
                    type=JobEventType.JOB_STEP,
                    step=f"wake_step_{index}",
                    payload={"index": index},
                )
                for index in range(120)
            ]
            + [
                JobEvent(
                    job_id=record.job_id,
                    type=JobEventType.JOB_SUCCEEDED,
                    payload={"project_id": "book", "ok": True},
                )
            ]
        )

        class _QueuedSubscription:
            @staticmethod
            def get(timeout: float = 0) -> JobEvent | None:  # noqa: ARG004
                return next(queued, None)

            @staticmethod
            def close() -> None:
                return None

        manager._app_event_subscription = _QueuedSubscription()

        manager.flush_stale_events_after_wake()

        # Only the first bounded slice is handled synchronously; Qt regains
        # control before the terminal event at the end of the backlog.
        assert record.status == DesktopJobState.RUNNING
        assert manager._wake_replay_active is True
        assert manager._wake_replay_timer.isActive()
        assert not manager._app_event_timer.isActive()

        qtbot.waitUntil(lambda: record.status == DesktopJobState.SUCCEEDED, timeout=2_000)

        assert manager._wake_replay_active is False
        assert not manager._wake_replay_events
        assert manager._app_event_timer.isActive()
    finally:
        manager.shutdown(wait_ms=100)


def test_background_ui_throttles_job_render_signals() -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    try:
        manager.set_ui_active(False)
        manager._schedule_jobs_changed(high_frequency=True)
        manager._token_dirty = True

        assert manager._app_event_timer.interval() == 1_000
        assert manager._jobs_dirty is True
        assert not manager._jobs_bind_timer.isActive()
        assert not manager._token_timer.isActive()

        manager.set_ui_active(True)

        assert manager._app_event_timer.interval() == 150
        assert manager._jobs_bind_timer.isActive()
        assert manager._token_timer.isActive()
    finally:
        manager.shutdown(wait_ms=100)


def test_desktop_job_manager_shutdown_unsubscribes_global_event_bus() -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    bus = manager._event_bus
    callbacks = (
        manager._on_init_started,
        manager._on_init_completed,
        manager._on_outline_updated,
        manager._on_chapter_completed,
    )

    assert any(
        callback in registered for registered in bus._global_subs.values() for callback in callbacks
    )

    manager.shutdown(wait_ms=100)

    assert not any(
        callback in registered for registered in bus._global_subs.values() for callback in callbacks
    )


def test_run_log_and_model_call_steps_update_job_record_without_current_step() -> None:
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    try:
        record = DesktopJobRecord(
            job_id="j1",
            kind="init_long",
            label="长篇立项",
            project_id="book",
            status=DesktopJobState.RUNNING,
            current_step="plan",
        )
        manager._jobs[record.job_id] = record

        manager._handle_step(
            "j1",
            "run_log_started",
            {"run_log_dir": "/tmp/run-1", "run_id": "run-1"},
        )
        manager._handle_step(
            "j1",
            "model_call_update",
            {
                "event": "api_call_done",
                "status": "success",
                "call_id": "c1",
                "task": "PLAN_CHAPTER",
                "run_log_dir": "/tmp/run-1",
                "total_tokens": 321,
                "cost_usd": 0.02,
                "response_preview": "输出",
            },
        )

        updated = manager._jobs["j1"]
        assert updated.current_step == "plan"
        assert updated.result["run_log_dir"] == "/tmp/run-1"
        assert updated.cumulative_tokens == 321
        assert updated.cumulative_cost_usd == pytest.approx(0.02)
    finally:
        manager.shutdown(wait_ms=100)


def test_stream_delta_uses_payload_text_over_delta_replay() -> None:
    """When a delta payload carries cumulative `text`, the store uses it directly
    instead of joining individual deltas (A1 regression)."""
    store = TaskObservationStore()
    job = _job(
        "j1",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s1", "delta": "A", "text": "A"},
                "2026-06-18T10:00:01+00:00",
            ),
            _event(
                "llm_stream_delta",
                {"stream_id": "s1", "delta": "B", "text": "AB"},
                "2026-06-18T10:00:02+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.text == "AB"
    assert focus.stream.text_length == 2


def test_truncated_events_still_show_full_text() -> None:
    """When _MAX_JOB_EVENTS evicts early deltas, the latest delta's cumulative
    `text` field keeps the preview intact (A1 core fix)."""
    store = TaskObservationStore()
    # Simulate post-truncation state: only the latest delta survived, but it
    # carries the full cumulative text from the emitter.
    job = _job(
        "j1",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "task": "DRAFT_CHAPTER", "chapter": 3, "attempt": 1},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "delta": "新段落",
                    "text": "旧段落…新段落",
                    "text_length": 7,
                },
                "2026-06-18T10:00:05+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.text == "旧段落…新段落"
    assert focus.stream.text_length == 7


def test_stream_error_keeps_cumulative_text_when_error_payload_is_legacy() -> None:
    """Legacy error/restart events without `text` must not downgrade the
    previous cumulative delta text back to a truncated delta replay."""
    store = TaskObservationStore()
    job = _job(
        "j1",
        events=[
            _event(
                "llm_stream_start",
                {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
                "2026-06-18T10:00:00+00:00",
            ),
            _event(
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "delta": "尾段",
                    "text": "完整正文前半…尾段",
                },
                "2026-06-18T10:00:01+00:00",
            ),
            _event(
                "llm_stream_error",
                {"stream_id": "s1", "error": "timeout"},
                "2026-06-18T10:00:02+00:00",
            ),
        ],
    )

    store.ingest_jobs([job])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)

    assert focus is not None
    assert focus.stream is not None
    assert focus.stream.status == "error"
    assert focus.stream.text == "完整正文前半…尾段"
    assert focus.stream.text_length == len("完整正文前半…尾段")


def test_compact_payload_preserves_bool_fields() -> None:
    """Bool fields like timed_out must stay bool, not be coerced to 0/1 (A2)."""
    payload = _compact_payload(
        "human_decision_resolved",
        {
            "decision_id": "d1",
            "title": "确认",
            "timed_out": True,
            "choice": "allow",
        },
    )

    assert payload["timed_out"] is True
    assert isinstance(payload["timed_out"], bool)

    diagnosis = _compact_payload(
        "repair_strategy_diagnosis",
        {"dimension": "continuity", "accepted": False, "preferred_strategy": "patch"},
    )
    assert diagnosis["accepted"] is False
    assert isinstance(diagnosis["accepted"], bool)


def test_mark_decision_submitted_clears_pending() -> None:
    store = TaskObservationStore()
    store.ingest_jobs([_job("j1")])
    store.ingest_decision_required(
        "j1",
        {
            "decision_id": "d1",
            "title": "确认",
            "options": [{"id": "allow", "label": "允许"}],
            "default_option": "allow",
        },
    )

    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
    assert focus is not None
    assert focus.decision is not None
    assert focus.decision.status == "pending"

    store.mark_decision_submitted("j1", "d1", "allow")

    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
    assert focus is not None
    assert focus.decision is not None
    assert focus.decision.status == "resolved"
    assert focus.decision.choice == "allow"

    # After a fresh job rebind without decision events, the resolved decision
    # must not linger (external_decisions cleared on mark).
    store.ingest_jobs([_job("j1")])
    focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
    assert focus is not None
    assert focus.decision is None


def test_decision_required_signal_reaches_store() -> None:
    """manager.decision_required → store.ingest_decision_required wiring (E)."""
    _qapp()
    manager = DesktopJobManager(load_persisted_history=False)
    store = TaskObservationStore()
    manager.decision_required.connect(store.ingest_decision_required)
    try:
        manager.decision_required.emit(
            "j1",
            {
                "decision_id": "d9",
                "title": "信号链路确认",
                "options": [{"id": "ok", "label": "OK"}],
                "default_option": "ok",
            },
        )
        # Give the store a candidate job so focus_for_scope returns it.
        store.ingest_jobs([_job("j1")])

        focus = store.focus_for_scope(TaskFocusScope.GLOBAL)
        assert focus is not None
        assert focus.decision is not None
        assert focus.decision.decision_id == "d9"
        assert focus.decision.status == "pending"
    finally:
        manager.shutdown(wait_ms=100)


def test_floating_companion_visibility_follows_attention() -> None:
    _qapp()
    store = TaskObservationStore()
    companion = FloatingTaskCompanion()
    companion.bind_store(store)

    assert companion.isVisible()
    assert companion._meta.text() == "待命"

    store.ingest_jobs([_job("j1", status=DesktopJobState.RUNNING)])
    assert companion.isVisible()
    assert "运行中" in companion._meta.text()

    # SUCCEEDED jobs do not count as active attention -> companion returns to idle.
    store.ingest_jobs(
        [
            _job(
                "j1",
                status=DesktopJobState.SUCCEEDED,
                updated_at="2026-06-18T10:00:05+00:00",
            )
        ]
    )
    assert companion.isVisible()
    assert companion._meta.text() == "待命"
    companion.shutdown()


def test_floating_companion_paused_task_waits_instead_of_showing_paused() -> None:
    _qapp()
    store = TaskObservationStore()
    companion = FloatingTaskCompanion()
    companion.bind_store(store)
    try:
        store.ingest_jobs([_job("paused", status=DesktopJobState.PAUSED)])

        assert companion.isVisible()
        assert companion._pet_state == "paused"
        assert companion._meta.text() == "等待继续"
        assert "已暂停" not in companion._meta.text()
    finally:
        companion.shutdown()


def test_task_focus_panel_smart_scroll_preserves_user_position() -> None:
    """When the user scrolls up, a new delta must not yank them back (D1)."""
    _qapp()
    store = TaskObservationStore()
    panel = TaskFocusPanel(TaskFocusScope.GLOBAL)
    panel.bind_store(store)
    panel._stream_browser.resize(420, 200)
    panel.show()
    QApplication.processEvents()

    long_text = "\n".join(f"段落 {i}" for i in range(40))
    store.ingest_jobs(
        [
            _job(
                "j1",
                events=[
                    _event(
                        "llm_stream_start",
                        {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
                        "2026-06-18T10:00:00+00:00",
                    ),
                    _event(
                        "llm_stream_delta",
                        {"stream_id": "s1", "delta": "段", "text": long_text},
                        "2026-06-18T10:00:01+00:00",
                    ),
                ],
            )
        ]
    )
    panel._flush_render()
    QApplication.processEvents()
    sb = panel._stream_browser.verticalScrollBar()
    assert sb.maximum() > 0  # enough content to scroll
    assert sb.value() >= sb.maximum() - sb.singleStep() * 2

    # User scrolls up to review earlier output.
    sb.setValue(0)

    # A new delta arrives; smart scroll must NOT pull back to the bottom.
    longer_text = long_text + "\n新段落"
    store.ingest_jobs(
        [
            _job(
                "j1",
                events=[
                    _event(
                        "llm_stream_start",
                        {"stream_id": "s1", "task": "DRAFT_CHAPTER"},
                        "2026-06-18T10:00:00+00:00",
                    ),
                    _event(
                        "llm_stream_delta",
                        {"stream_id": "s1", "delta": "段", "text": long_text},
                        "2026-06-18T10:00:01+00:00",
                    ),
                    _event(
                        "llm_stream_delta",
                        {"stream_id": "s1", "delta": "新", "text": longer_text},
                        "2026-06-18T10:00:02+00:00",
                    ),
                ],
            )
        ]
    )
    panel._flush_render()
    QApplication.processEvents()
    assert sb.value() < sb.maximum()

    panel.shutdown()


def test_task_focus_panel_can_pin_and_clear_job() -> None:
    _qapp()
    store = TaskObservationStore()
    decision = _job(
        "decision",
        label="高优先级确认",
        events=_decision_events("d1"),
        updated_at="2026-06-18T09:59:00+00:00",
    )
    running = _job("running", label="普通运行任务", updated_at="2026-06-18T10:00:02+00:00")
    panel = TaskFocusPanel(TaskFocusScope.GLOBAL)
    try:
        store.ingest_jobs([decision, running])
        panel.bind_store(store)

        assert panel._last_job_id == "decision"

        panel.set_pinned_job("running")
        assert panel.pinned_job_id == "running"
        assert panel._last_job_id == "running"
        assert panel._title_label.text() == "普通运行任务"

        panel.clear_pinned_job()
        assert panel.pinned_job_id == ""
        assert panel._last_job_id == "decision"
    finally:
        panel.shutdown()


def test_task_focus_panel_invalidates_missing_pin() -> None:
    _qapp()
    store = TaskObservationStore()
    first = _job("first", label="第一任务")
    second = _job("second", label="第二任务")
    panel = TaskFocusPanel(TaskFocusScope.GLOBAL)
    invalidated: list[bool] = []
    panel.pin_invalidated.connect(lambda: invalidated.append(True))
    try:
        store.ingest_jobs([first, second])
        panel.bind_store(store)
        panel.set_pinned_job("second")

        store.ingest_jobs([first])

        assert panel.pinned_job_id == ""
        assert panel._last_job_id == "first"
        assert invalidated == [True]
    finally:
        panel.shutdown()


def test_task_focus_panel_skips_rerender_when_other_job_streams() -> None:
    _qapp()
    store = TaskObservationStore()
    pinned = _job(
        "pinned",
        label="固定任务",
        events=_streaming_events("pinned-stream", text="固定正文"),
        updated_at="2026-06-18T10:00:01+00:00",
    )
    other = _job(
        "other",
        label="其他任务",
        events=_streaming_events("other-stream", text="其他正文"),
        updated_at="2026-06-18T10:00:02+00:00",
    )
    other_changed = _job(
        "other",
        label="其他任务",
        events=_streaming_events(
            "other-stream",
            text="其他正文更新",
            at="2026-06-18T10:00:03+00:00",
        ),
        updated_at="2026-06-18T10:00:03+00:00",
    )
    panel = TaskFocusPanel(TaskFocusScope.GLOBAL)
    render_calls: list[str] = []
    original_render = panel._render_state

    def _record_render(state):  # type: ignore[no-untyped-def]
        render_calls.append(state.job_id)
        original_render(state)

    panel._render_state = _record_render  # type: ignore[method-assign]
    try:
        store.ingest_jobs([pinned, other])
        panel.bind_store(store)
        panel.set_pinned_job("pinned")
        render_calls.clear()

        store.ingest_jobs([pinned, other_changed])
        panel._flush_render()

        assert render_calls == []
        assert panel._last_job_id == "pinned"
        assert panel._stream_browser.toPlainText().strip() == "固定正文"
    finally:
        panel.shutdown()


def test_task_switcher_bar_rebuilds_only_when_candidate_order_changes() -> None:
    _qapp()
    store = TaskObservationStore()
    first = _job("first", updated_at="2026-06-18T10:00:01+00:00")
    second = _job("second", project_id="book-2", updated_at="2026-06-18T10:00:02+00:00")
    second_updated = _job(
        "second",
        project_id="book-2",
        updated_at="2026-06-18T10:00:03+00:00",
    )
    bar = TaskSwitcherBar(TaskFocusScope.GLOBAL)
    selected: list[str] = []
    bar.pin_requested.connect(selected.append)
    try:
        bar.bind_store(store)
        store.ingest_jobs([first])
        assert bar.isVisible()
        assert tuple(bar._chips) == ("", "first")

        store.ingest_jobs([second, first])
        assert bar.isVisible()
        first_chip = bar._chips["first"]
        assert tuple(bar._chips) == ("", "second", "first")

        store.ingest_jobs([second_updated, first])
        assert bar._chips["first"] is first_chip

        bar._chips["first"].click()
        assert selected == ["first"]
        assert bar._chips["first"].isChecked()
    finally:
        bar.shutdown()


def test_task_switcher_bar_keeps_remaining_chip_after_one_is_removed() -> None:
    _qapp()
    store = TaskObservationStore()
    first = _job("first", updated_at="2026-06-18T10:00:01+00:00")
    second = _job("second", project_id="book-2", updated_at="2026-06-18T10:00:02+00:00")
    bar = TaskSwitcherBar(TaskFocusScope.GLOBAL)
    try:
        bar.bind_store(store)
        store.ingest_jobs([second, first])
        assert bar.isVisible()
        assert tuple(bar._chips) == ("", "second", "first")

        store.ingest_jobs([second])

        assert bar.isVisible()
        assert tuple(bar._chips) == ("", "second")
        assert bar._count_badge.text() == "项目 1 项"
    finally:
        bar.shutdown()


def test_task_switcher_bar_collapses_same_project_jobs_into_one_chip() -> None:
    _qapp()
    store = TaskObservationStore()
    running = _job("running", updated_at="2026-06-18T10:00:01+00:00")
    streaming = _job(
        "streaming",
        events=_streaming_events("s1", text="实时输出"),
        updated_at="2026-06-18T10:00:02+00:00",
    )
    other = _job("other", project_id="other-book", updated_at="2026-06-18T10:00:03+00:00")
    bar = TaskSwitcherBar(TaskFocusScope.GLOBAL)
    try:
        bar.bind_store(store)
        store.ingest_jobs([running, streaming, other])

        assert tuple(bar._chips) == ("", "streaming", "other")
        assert "已合并同项目观察：2 条" in bar._chips["streaming"].toolTip()
        assert "实时输出" not in bar._chips["streaming"].text()
    finally:
        bar.shutdown()


def test_task_switcher_bar_close_button_emits_job_id() -> None:
    _qapp()
    store = TaskObservationStore()
    first = _job("first", updated_at="2026-06-18T10:00:01+00:00")
    second = _job("second", project_id="book-2", updated_at="2026-06-18T10:00:02+00:00")
    bar = TaskSwitcherBar(TaskFocusScope.GLOBAL)
    closed: list[str] = []
    bar.close_requested.connect(closed.append)
    try:
        bar.bind_store(store)
        store.ingest_jobs([second, first])

        close_button = next(
            button
            for button in bar._chip_host.findChildren(QPushButton, "taskSwitcherCloseButton")
            if button.property("job_id") == "first"
        )
        close_button.click()

        assert closed == ["first"]
        assert not bar._chips["first"].isChecked()
    finally:
        bar.shutdown()


def test_task_model_call_panel_renders_chart_and_call_preview(tmp_path: Path) -> None:
    _qapp()
    run_dir = tmp_path / "logs" / "run-2"
    _write_model_call(
        run_dir,
        "001_draft.json",
        task="DRAFT_CHAPTER",
        provider="openai",
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=1000,
        content="模型输出 A",
    )
    _write_model_call(
        run_dir,
        "002_wave.json",
        task="WAVE_CHAPTER",
        provider="openai",
        model="gpt-4o",
        prompt_tokens=150,
        completion_tokens=75,
        latency_ms=2000,
        content="模型输出 B",
    )
    job = _job("calls")
    job.result["run_log_dir"] = str(run_dir)
    panel = TaskModelCallPanel()
    try:
        panel.set_job(job)
        panel._toggle.click()

        assert panel._count_badge.text() == "2 次调用"
        assert "Token 375" in panel._token_badge.text()
        assert not panel._content.isHidden()
        assert panel._call_select.count() == 2
        assert panel._request_preview.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
        assert panel._response_preview.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
        assert panel._request_preview.maximumHeight() > 240
        assert panel._response_preview.maximumHeight() > 240
        assert panel._request_preview.accessibleName() == "模型调用请求与参数"
        assert panel._response_preview.accessibleName() == "模型调用响应或异常"
        assert "状态：成功" in panel._request_preview.toPlainText()
        assert "耗时：1.0s" in panel._request_preview.toPlainText()
        assert "Token：输入 100 / 输出 50 / 总计 150" in panel._request_preview.toPlainText()
        assert "用户提示" in panel._request_preview.toPlainText()
        assert "模型输出 A" in panel._response_preview.toPlainText()

        panel._call_select.setCurrentIndex(1)
        assert "模型输出 B" in panel._response_preview.toPlainText()
    finally:
        panel.deleteLater()


def test_task_focus_dialog_pinned_decision_uses_selected_job_id() -> None:
    _qapp()
    store = TaskObservationStore()
    top = _job(
        "top",
        label="更高优先级确认",
        events=_decision_events("d-top", at="2026-06-18T10:00:02+00:00"),
        updated_at="2026-06-18T10:00:02+00:00",
    )
    pinned = _job(
        "pinned",
        label="用户选择的确认",
        events=_decision_events("d-pinned", at="2026-06-18T10:00:01+00:00"),
        updated_at="2026-06-18T10:00:01+00:00",
    )
    store.ingest_jobs([top, pinned])
    dialog = TaskFocusDialog(store)
    emitted: list[tuple[str, str, str, str]] = []
    dialog.decision_selected.connect(lambda *args: emitted.append(tuple(args)))
    try:
        dialog._handle_pin_requested("pinned")
        QApplication.processEvents()
        buttons = dialog._panel._decision_buttons_host.findChildren(QPushButton)
        allow_button = next(button for button in buttons if button.text() == "允许")
        allow_button.click()

        assert emitted == [("pinned", "d-pinned", "allow", "", "")]
        assert dialog._switcher._chips["pinned"].isChecked()
    finally:
        dialog._switcher.shutdown()
        dialog._panel.shutdown()


def test_task_focus_dialog_confirm_delete_emits_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _qapp()
    store = TaskObservationStore()
    job = _job("paused", status=DesktopJobState.PAUSED)
    store.ingest_jobs([job])
    dialog = TaskFocusDialog(store)
    emitted: list[str] = []
    dialog.task_delete_requested.connect(emitted.append)
    monkeypatch.setattr(task_focus_components, "show_message_box", lambda *args, **kwargs: "delete")
    try:
        dialog._panel.set_pinned_job("paused")
        dialog._switcher.set_selected_job_id("paused")

        dialog._confirm_delete_task("paused")

        assert emitted == ["paused"]
        assert dialog._panel.pinned_job_id == ""
        assert not dialog._switcher._chips["paused"].isChecked()
    finally:
        dialog._switcher.shutdown()
        dialog._panel.shutdown()


def test_task_focus_dialog_reopen_refreshes() -> None:
    """After close shuts the panel down, reopen rebinds and refreshes (B3)."""
    _qapp()
    store = TaskObservationStore()
    dialog = TaskFocusDialog(store)
    dialog.show()
    dialog.close()  # triggers closeEvent → panel.shutdown (_store=None)

    # Reopen rebinds the store; showing the dialog + a job makes panel visible.
    dialog.reopen(store)
    dialog.show()
    store.ingest_jobs([_job("j1", status=DesktopJobState.RUNNING)])
    QApplication.processEvents()

    assert dialog._panel.isVisible()
    dialog._panel.shutdown()
