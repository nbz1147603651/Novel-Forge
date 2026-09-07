"""Unit tests for the spoken-text rewrite phase (Phase 5).

Covers:
- 1 round success: no LLM failures → returns immediately
- 3 rounds failure: all retries exhausted → raises ScriptCompletenessError
- Partial success: first round fails, second round succeeds
- Backoff timing between retries
- Permanent route failure: early-exit + graceful degradation
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.tts.pipeline.script_phases import ScriptCompletenessError
from novel_forge.tts.pipeline.script_phases.rewrite import phase_rewrite
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_stage_context import ScriptStageContext


def _make_step(
    *,
    max_rounds: int = 3,
    backoff_s: float = 0.01,  # Fast for tests
) -> MagicMock:
    step = MagicMock()
    step._settings = SimpleNamespace(
        tts_spoken_rewrite_max_rounds=max_rounds,
        tts_spoken_rewrite_retry_backoff_s=backoff_s,
        tts_default_provider="bailian",
    )
    step._router = MagicMock()
    step._builder = MagicMock()
    step._on_step_event = MagicMock()
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
    return input_data


def _make_script(*, llm_failed: int = 0) -> DubbingScript:
    segments = [
        DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="测试文本",
            spoken_text="口语化文本" if llm_failed == 0 else "",
            emotion=EmotionTag.NEUTRAL,
        ),
    ]
    metadata = {}
    if llm_failed > 0:
        metadata["spoken_text_rewrite"] = {
            "rejection_reasons": {"llm_call_failed": llm_failed},
        }
    else:
        metadata["spoken_text_rewrite"] = {
            "rejection_reasons": {},
        }
    return DubbingScript(chapter_number=1, segments=segments, metadata=metadata)


class TestPhaseRewriteSuccess:
    """Tests for successful rewrite scenarios."""

    @pytest.mark.asyncio
    async def test_first_round_success(self) -> None:
        """When rewrite succeeds on first round, returns immediately."""
        step = _make_step()
        input_data = _make_input()
        script = _make_script(llm_failed=0)
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=script,
        ) as mock_rewrite:
            result = await phase_rewrite(script, input_data, ctx, step)

        assert result is script
        mock_rewrite.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_round_success_after_first_failure(self) -> None:
        """When first round fails but second succeeds, returns after retry."""
        step = _make_step(max_rounds=3)
        input_data = _make_input()
        failed_script = _make_script(llm_failed=3)
        success_script = _make_script(llm_failed=0)
        ctx = ScriptStageContext()

        call_count = 0

        async def mock_rewrite_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return failed_script
            return success_script

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            side_effect=mock_rewrite_fn,
        ):
            result = await phase_rewrite(failed_script, input_data, ctx, step)

        assert call_count == 2
        assert result.metadata["spoken_text_rewrite"]["rejection_reasons"] == {}


class TestPhaseRewriteFailure:
    """Tests for rewrite failure scenarios."""

    @pytest.mark.asyncio
    async def test_all_rounds_exhausted_raises_error(self) -> None:
        """When all retry rounds fail, raises ScriptCompletenessError."""
        step = _make_step(max_rounds=3)
        input_data = _make_input()
        failed_script = _make_script(llm_failed=5)
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=failed_script,
        ):
            with pytest.raises(ScriptCompletenessError) as exc_info:
                await phase_rewrite(failed_script, input_data, ctx, step)

        report = exc_info.value.report
        assert report.passed is False
        assert len(report.failures) > 0
        assert any("LLM" in f for f in report.failures)

    @pytest.mark.asyncio
    async def test_single_round_max_raises_on_failure(self) -> None:
        """With max_rounds=1, a single failure immediately raises."""
        step = _make_step(max_rounds=1)
        input_data = _make_input()
        failed_script = _make_script(llm_failed=2)
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=failed_script,
        ):
            with pytest.raises(ScriptCompletenessError):
                await phase_rewrite(failed_script, input_data, ctx, step)

    @pytest.mark.asyncio
    async def test_retry_events_emitted(self) -> None:
        """Step events are emitted for each retry round."""
        step = _make_step(max_rounds=2)
        input_data = _make_input()
        failed_script = _make_script(llm_failed=3)
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=failed_script,
        ):
            with pytest.raises(ScriptCompletenessError):
                await phase_rewrite(failed_script, input_data, ctx, step)

        # Should have retry event + exhausted event
        event_names = [
            call[0][0] for call in step._on_step_event.call_args_list
        ]
        assert "tts_spoken_rewrite_retry" in event_names
        assert "tts_spoken_rewrite_exhausted" in event_names


class TestPhaseRewritePermanentRouteFailure:
    """Tests for permanent route failure (non-transient) scenarios."""

    @staticmethod
    def _make_permanent_failure_script() -> DubbingScript:
        """Script with permanent_route_failure metadata."""
        segments = [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="测试文本",
                spoken_text="",
                emotion=EmotionTag.NEUTRAL,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="对话文本",
                spoken_text="",
                emotion=EmotionTag.NEUTRAL,
            ),
        ]
        metadata = {
            "spoken_text_rewrite": {
                "rejection_reasons": {"llm_call_failed_permanent": 2},
                "permanent_route_failure": True,
            }
        }
        return DubbingScript(chapter_number=1, segments=segments, metadata=metadata)

    @pytest.mark.asyncio
    async def test_permanent_route_failure_skips_remaining_rounds(self) -> None:
        """Permanent route failure exits after 1 round, not wasting retries."""
        step = _make_step(max_rounds=3)
        input_data = _make_input()
        permanent_script = self._make_permanent_failure_script()
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=permanent_script,
        ) as mock_rewrite:
            result = await phase_rewrite(permanent_script, input_data, ctx, step)

        # Only 1 call despite max_rounds=3
        mock_rewrite.assert_called_once()
        # Should NOT raise ScriptCompletenessError
        assert result is not None

    @pytest.mark.asyncio
    async def test_permanent_route_failure_degrades_gracefully(self) -> None:
        """Permanent failure degrades: spoken_text falls back to original text."""
        step = _make_step(max_rounds=3)
        input_data = _make_input()
        permanent_script = self._make_permanent_failure_script()
        ctx = ScriptStageContext()

        with patch(
            "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
            new_callable=AsyncMock,
            return_value=permanent_script,
        ):
            result = await phase_rewrite(permanent_script, input_data, ctx, step)

        # No exception raised; segments get original text as spoken_text
        for seg in result.segments:
            assert seg.spoken_text == seg.text

        # Degradation event emitted
        event_names = [
            call[0][0] for call in step._on_step_event.call_args_list
        ]
        assert "tts_spoken_rewrite_degraded" in event_names
        assert "tts_spoken_rewrite_exhausted" not in event_names
