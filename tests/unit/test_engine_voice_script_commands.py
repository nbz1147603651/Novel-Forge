from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novel_forge.api.routes import engine
from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentTakeVersion,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TakeReviewStatus,
)
from novel_forge.tts.services.studio_service import VoiceStudioProjectService
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.tts_ops.execution import execute_save_dubbing_script


async def test_save_dubbing_script_persists_edits_and_invalidates_audio(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    script = DubbingScript(
        chapter_number=4,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                character_name="旁白",
                text="旧旁白。",
            )
        ],
    )
    atomic_write_json(
        layout.tts_dubbing_script_path(4),
        script.model_dump(mode="json"),
    )
    audio_dir = layout.tts_audio_dir(4)
    audio_dir.mkdir(parents=True)
    (audio_dir / "seg_0000.mp3").write_bytes(b"stale-audio")

    result = await execute_save_dubbing_script(
        project_id="demo",
        chapter_number=4,
        edits=[
            {
                "segment_index": 0,
                "content": "新旁白。",
                "speaker_id": "",
                "speaker_label": "旁白",
                "segment_type": "inner_thought",
                "emotion_label": "温柔",
                "emotion_intensity": 0.72,
                "tone_hint": "轻声",
                "speed_override": 0.9,
                "stress_words": ["新"],
                "language_code": "zh",
            }
        ],
        layout=layout,
    )

    saved = json.loads(layout.tts_dubbing_script_path(4).read_text(encoding="utf-8"))
    segment = saved["segments"][0]
    assert result.result["changed_segment_indices"] == [0]
    assert segment["text"] == "新旁白。"
    assert segment["segment_type"] == SegmentType.INNER_THOUGHT.value
    assert segment["emotion"] == EmotionTag.TENDER.value
    assert segment["emotion_intensity"] == 0.72
    assert segment["speed_override"] == 0.9
    assert saved["script_hash"]
    assert not audio_dir.exists()


async def test_save_voice_guidance_preserves_source_and_retires_candidate(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    source = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        character_id="shen-qing",
        character_name="沈青",
        text="别回头。",
    )
    script = DubbingScript(chapter_number=4, segments=[source])
    atomic_write_json(layout.tts_dubbing_script_path(4), script.model_dump(mode="json"))
    VoiceStudioProjectService(layout).register_candidate_take(
        SegmentTakeVersion(
            take_id="candidate-1",
            chapter_number=4,
            segment_index=0,
            segment=source,
            segment_result=SynthesisResult(segment_index=0, status=SynthesisStatus.COMPLETED),
            source_script_hash="script-v1",
        )
    )
    body = engine._SaveVoiceGuidanceBody.model_validate(
        {
            "kind": "save_voice_guidance",
            "projectId": "demo",
            "chapterNumber": 4,
            "edits": [
                {
                    "segmentIndex": 0,
                    "segmentOverride": {
                        "segment_index": 0,
                        "segment_type": "narration",
                        "character_id": "untrusted",
                        "character_name": "篡改者",
                        "text": "不应写入正文。",
                        "emotion": "tender",
                        "emotion_intensity": 0.7,
                        "tone_hint": "压低声音",
                        "vol_override": 0.9,
                    },
                }
            ],
        }
    )

    response = await engine.engine_save_voice_guidance(body, FileSystemStorage(tmp_path))

    assert response.status == "accepted"
    manifest = VoiceStudioProjectService(layout).load_take_manifest(4)
    assert manifest.takes[0].status == TakeReviewStatus.REJECTED
    saved = manifest.drafts["0"]
    assert saved.text == "别回头。"
    assert saved.character_id == "shen-qing"
    assert saved.segment_type == SegmentType.DIALOGUE
    assert saved.tone_hint == "压低声音"
    assert saved.vol_override == 0.9


async def test_analyze_reference_style_command_uses_transient_workspace_boundary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def fake_analyze(**kwargs):
        captured.update(kwargs)
        return ExecutionResult(
            project_id="demo",
            result={
                "message": "风格画像已建立",
                "profile": {"profile_id": "dsp_test", "confidence": 0.8},
            },
        )

    monkeypatch.setattr(engine, "execute_analyze_dubbing_style_reference", fake_analyze)
    runtime = SimpleNamespace(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        storage=FileSystemStorage(tmp_path),
    )
    body = engine._AnalyzeVoiceScriptStyleBody.model_validate(
        {
            "kind": "analyze_voice_script_style",
            "projectId": "demo",
            "sourceName": "sample.vtt",
            "referenceScriptText": "参考配音脚本" * 40,
        }
    )

    response = await engine.engine_analyze_voice_script_style(body, runtime)

    assert response.status == "accepted"
    assert response.data is not None
    assert response.data["profile"]["profile_id"] == "dsp_test"
    assert captured["reference_script_name"] == "sample.vtt"


async def test_analyze_reference_style_command_maps_validation_error_to_422(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_analyze(**_kwargs):
        raise ValueError("参考配音脚本有效内容不足")

    monkeypatch.setattr(engine, "execute_analyze_dubbing_style_reference", fake_analyze)
    runtime = SimpleNamespace(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        storage=FileSystemStorage(tmp_path),
    )
    body = engine._AnalyzeVoiceScriptStyleBody(
        project_id="demo",
        source_name="short.txt",
        reference_script_text="太短",
    )

    with pytest.raises(HTTPException) as exc_info:
        await engine.engine_analyze_voice_script_style(body, runtime)

    assert exc_info.value.status_code == 422
