"""Tests for per-project run logging."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from novel_forge.core.constants import TaskType
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest
from novel_forge.obs.logger import get_logger
from novel_forge.obs.project_logger import ProjectRunLogger, get_project_logger
from novel_forge.persistence.models import ProjectLayout


def test_project_run_logger_persists_events_and_model_calls(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()

    logger = ProjectRunLogger(
        layout=layout,
        project_id="demo_project",
        command="init-long",
        metadata={"mock": True, "total_chapters": 10},
    )
    logger.log_step("spec", {"title": "测试标题"})
    logger.record_router_event(
        "api_call_start",
        {
            "call_id": "call-1",
            "provider": "mock",
            "model": "gpt-4o-mini",
            "task": "init_story_bible",
            "max_tokens": 2048,
            "temperature": 0.7,
            "request": {
                "task_type": "init_story_bible",
                "messages": [{"role": "user", "content": "hello"}],
            },
        },
    )
    logger.record_router_event(
        "api_call_done",
        {
            "call_id": "call-1",
            "provider": "mock",
            "model": "gpt-4o-mini",
            "task": "init_story_bible",
            "latency_ms": 120.5,
            "total_tokens": 321,
            "response": {
                "content": '{"ok":true}',
                "total_tokens": 321,
                "structured_output_mode": "json_schema",
                "structured_output_downgraded_from": "json_schema_strict",
                "structured_output_reason": "profile:mock/model",
            },
        },
    )
    logger.finalize(status="success", result={"ok": True})

    assert logger.run_dir.exists()
    summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "success"
    assert summary["metadata"]["total_chapters"] == 10
    assert summary["schema_version"] == 1
    assert summary["request_id"]

    events = logger.events_path.read_text(encoding="utf-8")
    assert "run_started" in events
    assert '"event": "step"' in events
    assert "api_call_done" in events
    assert "structured_output_mode" in events
    event_records = [json.loads(line) for line in events.splitlines()]
    assert [record["sequence"] for record in event_records] == list(
        range(1, len(event_records) + 1)
    )
    assert all(record["run_id"] == logger.run_id for record in event_records)
    assert all(record["request_id"] == summary["request_id"] for record in event_records)

    model_call_files = sorted((logger.run_dir / "model_calls").iterdir())
    assert len(model_call_files) == 1
    model_call = json.loads(model_call_files[0].read_text(encoding="utf-8"))
    assert model_call["request"]["messages"][0]["content"] == "hello"
    assert model_call["response"]["content"] == '{"ok":true}'
    api_summary = json.loads(logger.api_summary_path.read_text(encoding="utf-8").splitlines()[1])
    assert api_summary["data"]["structured_output_mode"] == "json_schema"
    assert api_summary["data"]["structured_output_downgraded_from"] == "json_schema_strict"


def test_project_run_logger_persists_stream_model_calls(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()

    logger = ProjectRunLogger(
        layout=layout,
        project_id="demo_project",
        command="run-chapter",
    )
    logger.record_router_event(
        "api_stream_start",
        {
            "call_id": "stream-1",
            "provider": "mock",
            "model": "gpt-4o-mini",
            "task": "draft_chapter",
            "max_tokens": 2048,
            "temperature": 0.7,
            "request": {
                "task_type": "draft_chapter",
                "messages": [{"role": "user", "content": "draft"}],
            },
        },
    )
    logger.record_router_event(
        "api_stream_done",
        {
            "call_id": "stream-1",
            "provider": "mock",
            "model": "gpt-4o-mini",
            "task": "draft_chapter",
            "latency_ms": 88.0,
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "response": {"content": "streamed draft", "total_tokens": 30},
        },
    )
    logger.finalize(status="success")

    model_call_files = sorted((logger.run_dir / "model_calls").iterdir())
    assert len(model_call_files) == 1
    model_call = json.loads(model_call_files[0].read_text(encoding="utf-8"))
    assert model_call["event"] == "api_stream_done"
    assert model_call["request"]["messages"][0]["content"] == "draft"
    assert model_call["response"]["content"] == "streamed draft"


def test_project_run_logger_keeps_retry_chain_until_final_success(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()
    logger = ProjectRunLogger(layout=layout, project_id="demo_project", command="run-chapter")
    start = {
        "call_id": "call-retry",
        "provider": "opencode",
        "model": "deepseek-v4-flash",
        "task": "plan_chapter",
        "max_tokens": 19360,
        "request": {"messages": [{"role": "user", "content": "plan"}]},
    }
    logger.record_router_event("api_call_start", start)
    logger.record_router_event(
        "api_call_empty_response",
        {
            "call_id": "call-retry",
            "latency_ms": 91000,
            "finish_reason": "stop",
            "attempt": 1,
            "max_attempts": 3,
            "will_retry": True,
        },
    )
    logger.record_router_event(
        "api_call_length_truncated",
        {
            "call_id": "call-retry",
            "latency_ms": 330000,
            "completion_tokens": 19358,
            "total_tokens": 65000,
            "finish_reason": "length",
            "max_tokens": 19360,
            "next_max_tokens": 38720,
            "attempt": 2,
            "max_attempts": 9,
            "will_retry": True,
        },
    )
    logger.record_router_event(
        "api_call_done",
        {
            "call_id": "call-retry",
            "latency_ms": 570000,
            "prompt_tokens": 45829,
            "completion_tokens": 24000,
            "total_tokens": 69829,
            "max_tokens": 38720,
            "attempt": 3,
            "max_attempts": 9,
            "response": {"content": '{"scenes": []}'},
        },
    )

    files = sorted((logger.run_dir / "model_calls").iterdir())
    assert [path.name for path in files] == [
        "001_plan_chapter_empty_response.json",
        "002_plan_chapter_length_truncated.json",
        "003_plan_chapter.json",
    ]
    assert logger._pending_calls == {}
    final = json.loads(files[-1].read_text(encoding="utf-8"))
    assert final["event"] == "api_call_done"
    assert final["max_tokens"] == 38720
    assert final["response"]["content"] == '{"scenes": []}'
    api_events = [
        json.loads(line)["event"]
        for line in logger.api_summary_path.read_text(encoding="utf-8").splitlines()
    ]
    assert api_events == [
        "api_call_start",
        "api_call_empty_response",
        "api_call_length_truncated",
        "api_call_done",
    ]


def test_project_run_logger_aggregates_stream_deltas_without_text(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()
    logger = ProjectRunLogger(layout=layout, project_id="demo_project", command="init-long")

    for index in range(100):
        logger.log_step(
            "llm_stream_delta",
            {
                "stream_id": "stream-1",
                "task": "ground_outline_research",
                "segments": [{"kind": "reasoning", "text": "敏感推理文本"}],
                "text_length": index,
                "reasoning_length": index * 2,
            },
        )
    logger.log_step("llm_stream_end", {"stream_id": "stream-1"})
    logger.finalize(status="success")

    events = [json.loads(line) for line in logger.events_path.read_text().splitlines()]
    stream_steps = [event["data"] for event in events if event.get("event") == "step"]
    summaries = [item for item in stream_steps if item.get("step") == "llm_stream_delta_summary"]
    assert len(summaries) == 1
    assert summaries[0]["data"]["delta_count"] == 100
    assert "敏感推理文本" not in logger.events_path.read_text(encoding="utf-8")


def test_project_run_logger_persists_format_errors_separately(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()

    logger = ProjectRunLogger(
        layout=layout,
        project_id="demo_project",
        command="init-long",
    )
    payload = {
        "task": "plan_chapter_contracts",
        "attempt": 1,
        "max_attempts": 2,
        "error_type": "JSONDecodeError",
        "error": "Expecting ',' delimiter",
        "raw_content": '{"chapter_contracts":["a" "b"]}',
        "raw_excerpt": '{"chapter_contracts":["a" "b"]}',
    }

    logger.log_step("format_retry", payload)
    logger.finalize(status="error")

    assert "log_file" in payload
    log_file = Path(payload["log_file"])
    assert log_file.exists()
    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert data["task"] == "plan_chapter_contracts"
    assert data["raw_content"] == '{"chapter_contracts":["a" "b"]}'

    summary_lines = logger.format_errors_path.read_text(encoding="utf-8").splitlines()
    assert len(summary_lines) == 1
    assert json.loads(summary_lines[0])["log_file"] == str(log_file)

    errors_text = logger.errors_path.read_text(encoding="utf-8")
    assert "format_retry" in errors_text


def test_project_run_logger_keeps_successful_format_repairs_out_of_error_stream(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()

    logger = ProjectRunLogger(
        layout=layout,
        project_id="demo_project",
        command="init-long",
    )
    payload = {
        "task": "plan_outline",
        "attempt": 1,
        "max_attempts": 2,
        "error_type": "ValueError",
        "error": "JSONDecodeError: Expecting ',' delimiter",
        "repaired": True,
        "repair_action": "local_parse_repair",
        "raw_content": '{"outline":["a" "b"]}',
        "raw_excerpt": '{"outline":["a" "b"]}',
    }

    logger.log_step("format_repaired", payload)
    logger.finalize(status="success")

    assert "log_file" in payload
    assert Path(payload["log_file"]).exists()
    assert logger.format_errors_path.exists()
    assert not logger.errors_path.exists()


def test_project_run_logger_correlates_and_redacts_application_logs(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()
    run_logger = ProjectRunLogger(layout=layout, project_id="demo_project", command="run-chapter")
    application_logger = get_logger("tests.project_run_logger")

    with run_logger.activate(chapter=7, task="draft_chapter"):
        assert get_project_logger() is run_logger
        application_logger.info("begin draft | token=must-not-be-persisted")
        try:
            raise RuntimeError("Authorization: Bearer also-must-not-be-persisted")
        except RuntimeError:
            application_logger.exception("draft failed")

    assert get_project_logger() is None
    run_logger.finalize(status="error")

    records = [
        json.loads(line)
        for line in run_logger.application_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["level"] for record in records] == ["INFO", "ERROR"]
    assert all(record["run_id"] == run_logger.run_id for record in records)
    assert all(record["project_id"] == "demo_project" for record in records)
    assert all(record["chapter"] == 7 for record in records)
    assert all(record["task"] == "draft_chapter" for record in records)
    persisted = run_logger.application_log_path.read_text(encoding="utf-8")
    assert "must-not-be-persisted" not in persisted
    assert "[REDACTED]" in persisted
    python_log = run_logger.python_log_path.read_text(encoding="utf-8")
    assert "draft failed" in python_log
    assert "must-not-be-persisted" not in python_log


def test_project_run_logger_does_not_mix_concurrent_run_application_logs(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()
    first = ProjectRunLogger(layout=layout, project_id="demo_project", command="first")
    second = ProjectRunLogger(layout=layout, project_id="demo_project", command="second")
    application_logger = get_logger("tests.concurrent_project_run_logger")

    with first.activate():
        application_logger.warning("first-run-only")
    with second.activate():
        application_logger.warning("second-run-only")
    first.finalize(status="success")
    second.finalize(status="success")

    first_text = first.application_log_path.read_text(encoding="utf-8")
    second_text = second.application_log_path.read_text(encoding="utf-8")
    assert "first-run-only" in first_text
    assert "second-run-only" not in first_text
    assert "second-run-only" in second_text
    assert "first-run-only" not in second_text


async def test_project_run_logger_context_is_isolated_between_async_runs(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo_project")
    layout.ensure_dirs()
    first = ProjectRunLogger(layout=layout, project_id="demo_project", command="first")
    second = ProjectRunLogger(layout=layout, project_id="demo_project", command="second")

    async def _active_logger(run_logger: ProjectRunLogger) -> ProjectRunLogger | None:
        with run_logger.activate():
            await asyncio.sleep(0)
            return get_project_logger()

    first_active, second_active = await asyncio.gather(
        _active_logger(first),
        _active_logger(second),
    )
    first.finalize(status="success")
    second.finalize(status="success")

    assert first_active is first
    assert second_active is second
    assert get_project_logger() is None


async def test_model_router_emits_observer_events() -> None:
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
    )
    events: list[tuple[str, dict[str, object]]] = []
    router.add_observer(lambda event, payload: events.append((event, payload)))

    response = await router.route(
        ModelRequest(
            task_type=TaskType.SPEC_ENRICH,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=64,
            temperature=0.3,
        )
    )

    assert response.content
    assert [event for event, _payload in events] == ["api_call_start", "api_call_done"]
    start_payload = events[0][1]
    done_payload = events[1][1]
    assert start_payload["task"] == TaskType.SPEC_ENRICH.value
    assert done_payload["task"] == TaskType.SPEC_ENRICH.value
    assert start_payload["call_id"] == done_payload["call_id"]


async def test_model_router_observe_unregisters_observer_after_context() -> None:
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
    )
    events: list[tuple[str, dict[str, object]]] = []

    with router.observe(lambda event, payload: events.append((event, payload))):
        await router.route(
            ModelRequest(
                task_type=TaskType.SPEC_ENRICH,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=64,
                temperature=0.3,
            )
        )

    assert len(events) == 2

    events.clear()
    await router.route(
        ModelRequest(
            task_type=TaskType.SPEC_ENRICH,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=64,
            temperature=0.3,
        )
    )
    assert events == []
