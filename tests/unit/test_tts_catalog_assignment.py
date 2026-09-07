from __future__ import annotations

import json

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import VoiceCastEntry, VoiceCloneStatus, VoiceTeamContract
from novel_forge.workspace.tts_ops.execution import execute_assign_catalog_voice


async def test_assign_catalog_voice_persists_manual_identity_and_invalidates_preview(
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "voice-catalog-project")
    layout.ensure_dirs()
    original = VoiceCastEntry(
        character_id="lin-zhu",
        character_name="林逐",
        voice_id="mock-female-1",
        clone_status=VoiceCloneStatus.READY,
        preview_audio_path="tts/previews/old.mp3",
        preview_text="旧试听",
        match_reasons=["旧匹配依据"],
    )
    team = VoiceTeamContract(entries=[original], confirmed=True)
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    result = await execute_assign_catalog_voice(
        project_id="voice-catalog-project",
        character_id="lin-zhu",
        voice_id="mock-male-1",
        provider="mock",
        settings=settings,
        layout=layout,
    )

    assert result.result["voice_id"] == "mock-male-1"
    stored = VoiceTeamContract.model_validate(
        json.loads(layout.tts_voice_team_path.read_text(encoding="utf-8"))
    )
    entry = stored.get_entry("lin-zhu")
    assert entry is not None
    assert entry.voice_id == "mock-male-1"
    assert entry.voice_source == "manual"
    assert entry.match_reasons == ["使用作者明确指定的系统音色"]
    assert entry.preview_audio_path == ""
    assert entry.preview_text == ""
    assert stored.confirmed is False


async def test_assign_catalog_voice_rejects_a_voice_absent_from_current_provider_catalog(
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "voice-catalog-project")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[VoiceCastEntry(character_id="lin-zhu", character_name="林逐")]
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    result = await execute_assign_catalog_voice(
        project_id="voice-catalog-project",
        character_id="lin-zhu",
        voice_id="not-in-catalog",
        provider="mock",
        settings=settings,
        layout=layout,
    )

    assert result.result["error"] == "所选音色不在当前平台目录中，请刷新音色目录后重试"
