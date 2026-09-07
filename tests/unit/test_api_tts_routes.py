"""API parity tests for TTS provider capabilities and character voice actions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from novel_forge.api.routes import tts
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord
from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript, TTSProgressState
from novel_forge.workspace.execution_result import ExecutionResult


def _runtime(tmp_path):
    return SimpleNamespace(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        storage=FileSystemStorage(tmp_path),
    )


async def test_provider_catalog_exposes_capabilities(monkeypatch, tmp_path) -> None:
    async def fake_list(**kwargs):
        assert kwargs["provider"] == "mock"
        return ExecutionResult(
            project_id="api",
            result={
                "provider": "mock",
                "capabilities": {"synthesis": True, "voice_design": True},
                "voices": [{"voice_id": "mock-male-1"}],
            },
        )

    monkeypatch.setattr(tts, "execute_list_tts_voices", fake_list)

    payload = await tts.get_provider_catalog("mock", _runtime(tmp_path))

    assert payload["capabilities"]["voice_design"] is True
    assert payload["voices"][0]["voice_id"] == "mock-male-1"


async def test_design_route_uses_workspace_boundary(monkeypatch, tmp_path) -> None:
    captured = {}

    async def fake_design(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(project_id="demo", result={"entries": []})

    async def fake_run_logger(runtime, **kwargs):
        return await kwargs["execute"](None)

    monkeypatch.setattr(tts, "execute_design_character_voice", fake_design)
    monkeypatch.setattr(tts, "execute_api_with_run_logger", fake_run_logger)

    payload = await tts.design_character_voice(
        tts.CharacterVoiceDesignRequest(
            project_id="demo",
            character_id="c1",
            editorial_note="尾音更克制",
            provider="mock",
        ),
        _runtime(tmp_path),
    )

    assert payload == {"entries": []}
    assert captured["character_id"] == "c1"
    assert captured["description"] == "尾音更克制"
    assert captured["layout"].root.name == "demo"


def test_clone_request_rejects_server_filesystem_paths() -> None:
    with pytest.raises(ValidationError):
        tts.CharacterVoiceCloneRequest(
            project_id="demo",
            character_id="c1",
            reference_file_id="/private/tmp/reference.wav",
            provider="minimax",
        )


def test_full_pipeline_request_supports_narrator_only_chapter() -> None:
    request = tts.FullTTSPipelineRequest(
        project_id="demo",
        chapter_number=3,
        chapter_text="整章只有旁白。",
    )

    assert request.characters == []
    assert request.automation_mode is None


def test_generate_script_request_accepts_reference_style_controls() -> None:
    request = tts.GenerateScriptRequest(
        project_id="demo",
        chapter_number=3,
        chapter_text="当前作品正文。",
        provider="minimax",
        reference_script_text="参考脚本" * 40,
        reference_script_name="reference.srt",
        reference_style_strength=0.75,
    )

    assert request.provider == "minimax"
    assert request.reference_script_name == "reference.srt"
    assert request.reference_style_strength == 0.75


def test_tts_requests_validate_the_shared_automation_contract() -> None:
    request = tts.FullTTSPipelineRequest(
        project_id="demo",
        chapter_number=3,
        chapter_text="正文",
        automation_mode="autonomous",
    )

    assert request.automation_mode is not None
    assert request.automation_mode.value == "autonomous"
    with pytest.raises(ValidationError):
        tts.SynthesizeRequest(
            project_id="demo",
            chapter_number=3,
            automation_mode="unknown",
        )


def test_execution_errors_become_http_422() -> None:
    execution = ExecutionResult(project_id="demo", result={"error": "voice unavailable"})

    with pytest.raises(HTTPException) as exc_info:
        tts._successful_result(execution)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "voice unavailable"


async def test_progress_route_reads_chapter_scoped_checkpoint(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("demo"))
    layout.ensure_dirs()
    checkpoint = TTSProgressState(
        chapter_number=2,
        voice_team_done=True,
        script_done=True,
        completed_segments=[0, 1],
        failed_segments=[2],
    )
    path = layout.tts_progress_path_for_chapter(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checkpoint.model_dump_json(), encoding="utf-8")

    payload = await tts.get_tts_progress("demo", 2, runtime)

    assert payload["chapter_number"] == 2
    assert payload["completed_segments"] == [0, 1]
    assert payload["failed_segments"] == [2]


class _CapturingJobService:
    def __init__(self) -> None:
        self.command: JobCommand | None = None

    def submit(self, command: JobCommand) -> JobRecord:
        self.command = command
        return JobRecord(
            job_id="tts-job",
            kind=command.kind,
            label="tts",
            project_id=command.project_id,
        )


async def test_synthesis_job_route_submits_durable_job_kind() -> None:
    service = _CapturingJobService()

    record = await tts.submit_synthesis_job(
        tts.SynthesizeRequest(project_id="demo", chapter_number=3, provider="mock"),
        service,  # type: ignore[arg-type]
    )

    assert record.job_id == "tts-job"
    assert service.command is not None
    assert service.command.kind == JobKind.TTS_SYNTHESIZE
    assert service.command.payload["chapter_number"] == 3


async def test_full_pipeline_job_route_preserves_narrator_only_request() -> None:
    service = _CapturingJobService()

    await tts.submit_full_pipeline_job(
        tts.FullTTSPipelineRequest(
            project_id="demo",
            chapter_number=4,
            chapter_text="纯旁白正文",
        ),
        service,  # type: ignore[arg-type]
    )

    assert service.command is not None
    assert service.command.kind == JobKind.TTS_FULL_PIPELINE
    assert service.command.payload["characters"] == []
    assert service.command.payload["automation_mode"] is None


async def test_audio_api_blocks_external_consumption_until_delivery_ready(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("demo"))
    layout.ensure_dirs()
    result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        is_complete=True,
        delivery_ready=False,
        delivery_blocking_reasons=["unresolved_sound_cues"],
    )
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result.model_dump_json(), encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        await tts.get_audio_result("demo", 1, runtime)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["blocking_reasons"] == [
        "unresolved_sound_cues",
        "missing_assembled_audio",
    ]
    diagnostic = await tts.get_audio_result("demo", 1, runtime, include_incomplete=True)
    assert diagnostic["delivery_ready"] is False
