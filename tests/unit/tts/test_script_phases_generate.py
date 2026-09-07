"""Unit tests for the generate phase (Phase 1).

Covers:
- LLM generation success path
- LLM failure → rule-based fallback
- Source reconciliation failure → fallback
- Character map building
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.tts.pipeline.script_phases.generate import phase_generate
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)


def _make_step() -> MagicMock:
    step = MagicMock()
    step._settings = SimpleNamespace(tts_default_provider="bailian")
    step._on_step_event = MagicMock()
    step._build_character_map = MagicMock(return_value={"角色A": "char_a"})
    step._build_speaker_adjudication_cards = MagicMock(return_value=[])
    return step


def _make_input() -> MagicMock:
    input_data = MagicMock()
    input_data.voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="char_a",
                character_name="角色A",
                voice_id="voice_001",
            ),
        ],
        narrator_voice_id="narrator_001",
    )
    input_data.chapter_number = 1
    input_data.chapter_text = "测试章节文本"
    input_data.character_voices = []
    return input_data


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.character_names = {}
    ctx.speaker_adjudication_cards = []
    return ctx


def _make_script() -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="测试文本",
                emotion=EmotionTag.NEUTRAL,
            ),
        ],
    )


class TestPhaseGenerateLLMSuccess:
    """Tests for LLM generation success path."""

    @pytest.mark.asyncio
    async def test_llm_success_returns_script(self) -> None:
        """When LLM generation succeeds, returns the reconciled script."""
        step = _make_step()
        input_data = _make_input()
        ctx = _make_ctx()
        script = _make_script()

        step._generate_script_llm = AsyncMock(return_value=script)
        step._reconcile_script_with_source = MagicMock(return_value=(script, []))

        result_script, candidates = await phase_generate(input_data, ctx, step)

        assert result_script is script
        assert candidates == []
        step._generate_script_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_character_map_built_from_voice_team(self) -> None:
        """Character map is built from voice team and character voices."""
        step = _make_step()
        input_data = _make_input()
        ctx = _make_ctx()
        script = _make_script()

        step._generate_script_llm = AsyncMock(return_value=script)
        step._reconcile_script_with_source = MagicMock(return_value=(script, []))

        await phase_generate(input_data, ctx, step)

        step._build_character_map.assert_called_once_with(
            input_data.voice_team, input_data.character_voices
        )


class TestPhaseGenerateFallback:
    """Tests for fallback scenarios."""

    @pytest.mark.asyncio
    async def test_llm_failure_triggers_rule_fallback(self) -> None:
        """When LLM returns None, falls back to rule-based generation."""
        step = _make_step()
        input_data = _make_input()
        ctx = _make_ctx()
        fallback_script = _make_script()

        step._generate_script_llm = AsyncMock(return_value=None)
        step._generate_script_rule_based = AsyncMock(return_value=fallback_script)
        step._reconcile_script_with_source = MagicMock(return_value=(fallback_script, []))

        result_script, candidates = await phase_generate(input_data, ctx, step)

        assert result_script is fallback_script
        step._generate_script_rule_based.assert_called_once()
        # Verify fallback event was emitted
        event_names = [call[0][0] for call in step._on_step_event.call_args_list]
        assert "tts_script_rule_fallback" in event_names

    @pytest.mark.asyncio
    async def test_reconciliation_failure_triggers_fallback(self) -> None:
        """When source reconciliation fails, falls back to rule-based."""
        step = _make_step()
        input_data = _make_input()
        ctx = _make_ctx()
        llm_script = _make_script()
        fallback_script = _make_script()

        step._generate_script_llm = AsyncMock(return_value=llm_script)
        step._reconcile_script_with_source = MagicMock(
            side_effect=[
                ValueError("reconciliation failed"),  # First call fails
                (fallback_script, []),  # Second call (after fallback) succeeds
            ]
        )
        step._generate_script_rule_based = AsyncMock(return_value=fallback_script)

        result_script, candidates = await phase_generate(input_data, ctx, step)

        assert result_script is fallback_script
        step._generate_script_rule_based.assert_called_once()

    @pytest.mark.asyncio
    async def test_speaker_candidates_returned(self) -> None:
        """Speaker candidates from reconciliation are returned for Phase 2."""
        step = _make_step()
        input_data = _make_input()
        ctx = _make_ctx()
        script = _make_script()
        mock_candidates = [MagicMock(), MagicMock()]

        step._generate_script_llm = AsyncMock(return_value=script)
        step._reconcile_script_with_source = MagicMock(
            return_value=(script, mock_candidates)
        )

        result_script, candidates = await phase_generate(input_data, ctx, step)

        assert candidates is mock_candidates
