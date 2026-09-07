from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.services.automation import (
    AudioAutomationMode,
    resolve_audio_automation_mode,
    settings_for_audio_automation_mode,
)
from novel_forge.workspace.tts_ops.execution import execute_full_tts_pipeline


@pytest.mark.parametrize(
    ("mode", "enabled", "auto_generate", "auto_approve"),
    [
        (AudioAutomationMode.MANUAL, False, False, False),
        (AudioAutomationMode.ASSISTED, True, True, False),
        (AudioAutomationMode.AUTONOMOUS, True, True, True),
    ],
)
def test_automation_mode_is_authoritative_over_legacy_sound_switches(
    mode: AudioAutomationMode,
    enabled: bool,
    auto_generate: bool,
    auto_approve: bool,
) -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=not enabled,
        sound_generation_auto_generate=not auto_generate,
        sound_generation_auto_approve=not auto_approve,
    )

    resolved, effective = settings_for_audio_automation_mode(settings, mode)

    assert resolved == mode
    assert effective.tts_automation_mode == mode.value
    assert effective.sound_generation_enabled is enabled
    assert effective.sound_generation_auto_generate is auto_generate
    assert effective.sound_generation_auto_approve is auto_approve
    assert settings.sound_generation_enabled is (not enabled)


def test_platform_default_and_legacy_aliases_resolve_consistently() -> None:
    settings = Settings(_env_file=None, tts_automation_mode="autonomous")

    assert resolve_audio_automation_mode(None, settings=settings) == (
        AudioAutomationMode.AUTONOMOUS
    )
    assert resolve_audio_automation_mode("ai_assisted", settings=settings) == (
        AudioAutomationMode.ASSISTED
    )
    assert resolve_audio_automation_mode("unknown", settings=settings) == (
        AudioAutomationMode.ASSISTED
    )


async def test_manual_full_pipeline_reports_missing_prerequisites_without_creating_them(
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "manual-work")
    layout.ensure_dirs()

    result = await execute_full_tts_pipeline(
        project_id="manual-work",
        chapter_number=1,
        chapter_text="第一章正文",
        characters=[],
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        layout=layout,
        automation_mode=AudioAutomationMode.MANUAL,
    )

    assert result.result["error_code"] == "tts_manual_prerequisites_required"
    assert result.result["automation_mode"] == "manual"
    assert "配音团队" in result.result["missing_prerequisites"]
    assert "当前章节配音脚本" in result.result["missing_prerequisites"]
    assert not layout.tts_voice_team_path.exists()
    assert not layout.tts_dubbing_script_path(1).exists()
