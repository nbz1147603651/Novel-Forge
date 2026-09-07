"""Persistent TTS JobService command integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.app_service.contracts import JobCommand, JobKind
from novel_forge.app_service.performance_metrics import RunPerformanceMetrics
from novel_forge.app_service.workspace_commands import (
    WorkspaceCommandExecutor,
    _run_tts_export_audio_command,
    _run_tts_synthesize_command,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError
from novel_forge.workspace.execution_result import ExecutionResult


def test_tts_job_kinds_build_stable_persistent_intents() -> None:
    executor = WorkspaceCommandExecutor()

    synth = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_SYNTHESIZE,
            payload={"project_id": "demo", "chapter_number": 2, "provider": "mock"},
        )
    )
    full = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_FULL_PIPELINE,
            payload={
                "project_id": "demo",
                "chapter_number": 2,
                "chapter_text": "正文",
                "characters": [],
            },
        )
    )
    script = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_GENERATE_SCRIPT,
            payload={
                "project_id": "demo",
                "chapter_number": 2,
                "provider": "dashscope",
                "reference_style_strength": 0.8,
            },
        )
    )
    export = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIO,
            payload={
                "project_id": "demo",
                "scope": "chapter",
                "chapter_number": 2,
                "format": "flac",
            },
        )
    )
    audiobook = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIOBOOK,
            payload={"project_id": "demo", "chapter_numbers": [1, 2]},
        )
    )

    assert synth.project_id == "demo"
    assert synth.command_name == "app-service-tts-synthesize"
    assert full.command_name == "app-service-tts-full-pipeline"
    assert script.request.provider == "dashscope"
    assert script.request.reference_style_strength == 0.8
    assert export.command_name == "app-service-tts-export-audio"
    assert export.request.format == "flac"
    assert audiobook.command_name == "app-service-tts-export-audiobook"


async def test_synthesis_job_emits_segment_progress_and_requires_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master = tmp_path / "master.mp3"
    master.write_bytes(b"audio")
    audio_result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path=str(master),
        is_complete=True,
        delivery_ready=True,
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_synthesize(**kwargs):
        kwargs["on_segment_progress"](0, "completed")
        return ExecutionResult(project_id="demo", result=audio_result.model_dump(mode="json"))

    async def fake_logged(*args, execute, **_kwargs):
        return await execute(args[2]), "run-log"

    import novel_forge.app_service.workspace_commands as commands

    monkeypatch.setattr(commands, "execute_synthesize_chapter", fake_synthesize)
    monkeypatch.setattr(commands, "_execute_logged_prepared", fake_logged)
    prepared = WorkspaceCommandExecutor().prepare(
        JobCommand(
            kind=JobKind.TTS_SYNTHESIZE,
            payload={"project_id": "demo", "chapter_number": 1, "provider": "mock"},
        )
    )
    runtime = SimpleNamespace(storage=FileSystemStorage(tmp_path), settings=SimpleNamespace())

    payload, run_log_dir = await _run_tts_synthesize_command(
        prepared,
        runtime,
        lambda step, data: events.append((step, data)),
        RunPerformanceMetrics(),
    )

    assert payload["delivery_ready"] is True
    assert run_log_dir == "run-log"
    assert ("tts_segment", {"chapter": 1, "segment_index": 0, "status": "completed"}) in events


async def test_autonomous_synthesis_job_fails_when_delivery_gate_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        is_complete=False,
        delivery_ready=False,
        delivery_blocking_reasons=["incomplete_synthesis"],
    )

    async def fake_synthesize(**_kwargs):
        return ExecutionResult(project_id="demo", result=blocked.model_dump(mode="json"))

    async def fake_logged(*args, execute, **_kwargs):
        return await execute(args[2]), "run-log"

    import novel_forge.app_service.workspace_commands as commands

    monkeypatch.setattr(commands, "execute_synthesize_chapter", fake_synthesize)
    monkeypatch.setattr(commands, "_execute_logged_prepared", fake_logged)
    prepared = WorkspaceCommandExecutor().prepare(
        JobCommand(
            kind=JobKind.TTS_SYNTHESIZE,
            payload={
                "project_id": "demo",
                "chapter_number": 1,
                "automation_mode": "autonomous",
            },
        )
    )
    runtime = SimpleNamespace(storage=FileSystemStorage(tmp_path), settings=SimpleNamespace())

    with pytest.raises(TTSDeliveryNotReadyError):
        await _run_tts_synthesize_command(
            prepared,
            runtime,
            lambda _step, _data: None,
            RunPerformanceMetrics(),
        )


async def test_audio_export_job_records_only_a_bounded_delivery_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_export(**kwargs):
        assert kwargs["scope"] == "chapter"
        assert kwargs["chapter_number"] == 2
        assert kwargs["format"] == "mp3"
        return ExecutionResult(
            project_id="demo",
            result={
                "export_filename": "chapter_002.mp3",
                "message": "导出已完成：chapter_002.mp3",
            },
        )

    async def fake_logged(*args, execute, **_kwargs):
        return await execute(args[2]), "run-log"

    import novel_forge.app_service.workspace_commands as commands

    monkeypatch.setattr(commands, "execute_export_audio_delivery", fake_export)
    monkeypatch.setattr(commands, "_execute_logged_prepared", fake_logged)
    prepared = WorkspaceCommandExecutor().prepare(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIO,
            payload={
                "project_id": "demo",
                "scope": "chapter",
                "chapter_number": 2,
                "format": "mp3",
            },
        )
    )
    runtime = SimpleNamespace(storage=FileSystemStorage(tmp_path), settings=SimpleNamespace())

    payload, run_log_dir = await _run_tts_export_audio_command(
        prepared,
        runtime,
        lambda step, data: events.append((step, data)),
        RunPerformanceMetrics(),
    )

    assert payload["export_filename"] == "chapter_002.mp3"
    assert run_log_dir == "run-log"
    assert events == [
        (
            "tts_export_start",
            {"scope": "chapter", "chapter_number": 2, "format": "mp3"},
        ),
        ("tts_export_complete", {"filename": "chapter_002.mp3"}),
    ]
