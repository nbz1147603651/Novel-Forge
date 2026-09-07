"""Tests for the voice-approval gate: designed voices must be auditioned
before entering formal synthesis.

Root cause this guards against: MiniMax voice_design is a black box that may
return a wrong-gender voice despite a "性别：男" prompt. Previously the designed
voice entered the voice map with clone_status=READY and zero human confirmation,
so a male character could synthesize with a female voice the author never heard.
"""

from __future__ import annotations

from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.pipeline.build_voice_team_step import BuildVoiceTeamStep
from novel_forge.tts.schemas import (
    TTSProvider,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.workspace.tts_ops.execution import execute_approve_character_voice


def _designed_entry(*, approval: str = "pending") -> VoiceCastEntry:
    return VoiceCastEntry(
        character_id="c1",
        character_name="陈半仙",
        voice_id="ttv-designed-001",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="designed",
        approval_status=approval,
    )


def _pending_team_layout(tmp_path: Path) -> tuple[ProjectLayout, Settings]:
    """Build a layout with a single pending designed entry on disk."""
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[_designed_entry(approval="pending")],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")
    return layout, settings


async def test_execute_approve_flips_pending_to_approved(tmp_path: Path) -> None:
    """execute_approve_character_voice persists approval_status=approved on disk."""
    layout, settings = _pending_team_layout(tmp_path)
    result = await execute_approve_character_voice(
        project_id="demo",
        character_id="c1",
        settings=settings,
        layout=layout,
    )
    assert "error" not in result.result
    entries = result.result.get("entries") or []
    assert entries and entries[0]["approval_status"] == "approved"


async def test_execute_approve_is_idempotent_for_already_approved(tmp_path: Path) -> None:
    """Approving an already-approved entry is a no-op (no error, no invalidation)."""
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[_designed_entry(approval="approved")],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")
    result = await execute_approve_character_voice(
        project_id="demo",
        character_id="c1",
        settings=settings,
        layout=layout,
    )
    assert "error" not in result.result


async def test_execute_approve_returns_error_for_missing_character(tmp_path: Path) -> None:
    """Approving a character not in the team returns an error result."""
    layout, settings = _pending_team_layout(tmp_path)
    result = await execute_approve_character_voice(
        project_id="demo",
        character_id="nonexistent",
        settings=settings,
        layout=layout,
    )
    assert "error" in result.result


def test_designed_entry_defaults_to_pending_approval() -> None:
    """A freshly designed voice must require audition before formal use."""
    # approval_status is set to "pending" by the build step's design path.
    entry = _designed_entry(approval="pending")
    assert entry.approval_status == "pending"
    # is_ready must be False for pending entries: clone_status is READY but the
    # voice has not been auditioned/confirmed, so it must NOT enter synthesis.
    assert entry.clone_status == VoiceCloneStatus.READY
    assert entry.is_ready is False


def test_approved_entry_is_ready_for_synthesis() -> None:
    """Once approved, the designed voice enters synthesis normally."""
    entry = _designed_entry(approval="approved")
    assert entry.is_ready is True


def test_legacy_entries_without_approval_field_default_to_approved() -> None:
    """Existing voice_team.json payloads (no approval_status key) stay usable.

    Backward compat: the field defaults to "approved" so historical teams are
    not blocked by the new gate.
    """
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="legacy-voice",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
    )
    assert entry.approval_status == "approved"
    assert entry.is_ready is True


def test_pending_designed_voice_is_not_reused_automatically() -> None:
    """A pending (un-auditioned) designed entry must not be silently reused."""
    step = BuildVoiceTeamStep(object(), settings=Settings())
    entry = _designed_entry(approval="pending")
    entry.voice_design_prompt = step._build_voice_design_prompt(  # noqa: SLF001
        {"name": "陈半仙", "gender": "男", "age": "老年", "role": "supporting"}
    )
    character = {"name": "陈半仙", "gender": "男", "age": "老年", "role": "supporting"}
    # Even though the design prompt matches, a pending entry must NOT be reused
    # without an explicit approval step.
    assert step._can_reuse_existing_entry(entry, character, TTSProvider.MOCK) is False  # noqa: SLF001


def test_approved_designed_voice_is_reused() -> None:
    """An approved designed entry is reused normally when the prompt matches."""
    step = BuildVoiceTeamStep(object(), settings=Settings())
    character = {"name": "陈半仙", "gender": "男", "age": "老年", "role": "supporting"}
    entry = _designed_entry(approval="approved")
    entry.voice_design_prompt = step._build_voice_design_prompt(character)  # noqa: SLF001
    assert step._can_reuse_existing_entry(entry, character, TTSProvider.MOCK) is True  # noqa: SLF001

