"""Unit tests for the ScriptOrchestrator.

Covers:
- End-to-end orchestration with mocked phases
- Phase sequencing (all 7 phases called in order)
- Completeness gate enforcement (blocks on failure)
- Event emission for phase transitions
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.tts.pipeline.script_orchestrator import ScriptOrchestrator
from novel_forge.tts.pipeline.script_phases import ScriptCompletenessError, ScriptCompletenessReport
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import compute_segment_uid, compute_source_text_hash
from novel_forge.tts.script_stage_context import ScriptStageContext


def _make_step() -> MagicMock:
    step = MagicMock()
    step._settings = SimpleNamespace(
        tts_script_gate_enabled=True,
        tts_script_gate_spoken_text_coverage=0.6,
        tts_script_gate_emotion_differentiation=0.15,
        tts_default_provider="bailian",
        tts_default_model="speech-2.8-hd",
    )
    step._on_step_event = MagicMock()
    step._build_character_map = MagicMock(return_value={"角色A": "char_a"})
    step._build_speaker_adjudication_cards = MagicMock(return_value=[])
    step._generate_script_llm = AsyncMock(return_value=None)
    step._generate_script_rule_based = AsyncMock()
    step._reconcile_script_with_source = MagicMock()
    step._adjudicate_ambiguous_quote_roles = AsyncMock()
    step._merge_narration_continuations = MagicMock()
    step._fold_single_char_dialogue = MagicMock()
    step._validate_script_source_fidelity = MagicMock()
    step._review_dubbing_script_professionally = AsyncMock()
    step._ensure_soundscape_design = MagicMock()
    step._enrich_voice_direction = MagicMock()
    step._compute_hash = MagicMock(return_value="test_hash")
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
    input_data.chapter_text = "测试文本对话文本"
    input_data.character_voices = []
    input_data.scene_intents = []
    input_data.library_assets = []
    input_data.audio_creative_bible = None
    input_data.story_context = {}
    return input_data


def _make_good_script() -> DubbingScript:
    """Create a script that passes the completeness gate."""
    segments = [
        DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="测试文本",
            spoken_text="口语化文本",
            emotion=EmotionTag.HAPPY,
            emotion_intensity=0.8,
        ),
        DubbingSegment(
            segment_index=1,
            segment_type=SegmentType.DIALOGUE,
            text="对话文本",
            spoken_text="口语对话",
            emotion=EmotionTag.ANGRY,
            emotion_intensity=0.9,
            character_id="char_a",
        ),
    ]
    return DubbingScript(
        chapter_number=1,
        segments=segments,
        source_text_hash=compute_source_text_hash("测试文本对话文本"),
    )


class TestScriptOrchestratorPhases:
    """Tests for phase sequencing and delegation."""

    @pytest.mark.asyncio
    async def test_all_phases_called_in_order(self) -> None:
        """Orchestrator calls all 7 phases in the correct order."""
        step = _make_step()
        input_data = _make_input()
        ctx = MagicMock()
        ctx.character_names = {}
        ctx.speaker_adjudication_cards = []
        ctx.story_context = {}
        script_stage_context = ScriptStageContext()
        good_script = _make_good_script()

        call_order: list[str] = []

        async def mock_generate(*args, **kwargs):
            call_order.append("generate")
            return good_script, []

        async def mock_adjudicate(*args, **kwargs):
            call_order.append("adjudicate")
            return good_script

        def mock_normalize(*args, **kwargs):
            call_order.append("normalize")
            return good_script

        def mock_review_rules(*args, **kwargs):
            call_order.append("review_rules")
            return good_script

        async def mock_review_llm(*args, **kwargs):
            call_order.append("review_llm")
            return good_script

        async def mock_rewrite(*args, **kwargs):
            call_order.append("rewrite")
            return good_script

        async def mock_sound_design(*args, **kwargs):
            call_order.append("sound_design")
            return good_script

        def mock_finalize(*args, **kwargs):
            call_order.append("finalize")
            return good_script

        with (
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_generate", mock_generate),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_adjudicate", mock_adjudicate),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_normalize", mock_normalize),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_rules", mock_review_rules),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_llm", mock_review_llm),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_rewrite", mock_rewrite),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_sound_design", mock_sound_design),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_finalize", mock_finalize),
        ):
            orchestrator = ScriptOrchestrator(step)
            await orchestrator.run(input_data, ctx, script_stage_context)

        assert "generate" in call_order
        assert "adjudicate" in call_order
        assert "normalize" in call_order
        assert "review_rules" in call_order
        assert "sound_design" in call_order
        assert "finalize" in call_order
        # Generate must come first
        assert call_order[0] == "generate"


class TestParallelReviewRewriteMerge:
    """Tests for identity-safe merging of the two concurrent branches."""

    def test_merges_by_segment_id_and_preserves_specialist_text_repair(self) -> None:
        base = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=10,
                    segment_type=SegmentType.NARRATION,
                    text="第一段",
                    spoken_text="第一段",
                ),
                DubbingSegment(
                    segment_index=30,
                    segment_type=SegmentType.DIALOGUE,
                    text="第二段",
                    spoken_text="第二段",
                    character_id="char_a",
                ),
            ],
        )
        reviewed = base.model_copy(
            update={
                "segments": [
                    base.segments[1].model_copy(update={"spoken_text": "审校修复第二段"}),
                    base.segments[0],
                ],
                "metadata": {"professional_script_review": {"status": "passed"}},
            }
        )
        rewritten = base.model_copy(
            update={
                "segments": [
                    base.segments[0].model_copy(update={"spoken_text": "口语改写第一段"}),
                    base.segments[1].model_copy(update={"spoken_text": "通用改写第二段"}),
                ],
                "metadata": {"spoken_text_rewrite": {"rewritten": 2}},
            }
        )

        merged = ScriptOrchestrator._merge_review_and_rewrite(base, reviewed, rewritten)

        by_id = {segment.segment_index: segment for segment in merged.segments}
        assert [segment.segment_index for segment in merged.segments] == [30, 10]
        assert by_id[10].spoken_text == "口语改写第一段"
        assert by_id[30].spoken_text == "审校修复第二段"
        assert by_id[10].segment_uid == compute_segment_uid(by_id[10])
        assert by_id[30].segment_uid == compute_segment_uid(by_id[30])
        assert merged.metadata["spoken_text_rewrite"] == {"rewritten": 2}

    def test_identity_mismatch_fails_safe_to_reviewed_branch(self) -> None:
        base = _make_good_script()
        reviewed = base.model_copy(
            update={"segments": [base.segments[0].model_copy(update={"spoken_text": "审校版"})]}
        )
        rewritten = base

        merged = ScriptOrchestrator._merge_review_and_rewrite(base, reviewed, rewritten)

        assert merged is reviewed


class TestScriptOrchestratorGate:
    """Tests for completeness gate enforcement."""

    @pytest.mark.asyncio
    async def test_gate_failure_raises_error(self) -> None:
        """Orchestrator raises ScriptCompletenessError when gate fails."""
        step = _make_step()
        input_data = _make_input()
        ctx = MagicMock()
        ctx.character_names = {}
        ctx.speaker_adjudication_cards = []
        ctx.story_context = {}
        script_stage_context = ScriptStageContext()

        failing_report = ScriptCompletenessReport(
            passed=False,
            spoken_text_coverage=0.1,
            failures=["spoken_text 覆盖率不足"],
        )

        with (
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_generate",
                  new_callable=AsyncMock, return_value=(_make_good_script(), [])),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_adjudicate",
                  new_callable=AsyncMock, return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_normalize",
                  return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_rules",
                  return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_llm",
                  new_callable=AsyncMock, return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_rewrite",
                  new_callable=AsyncMock, return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_sound_design",
                  new_callable=AsyncMock, return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_finalize",
                  return_value=_make_good_script()),
            patch("novel_forge.tts.pipeline.script_orchestrator.validate_script_completeness",
                  return_value=failing_report),
        ):
            orchestrator = ScriptOrchestrator(step)
            with pytest.raises(ScriptCompletenessError) as exc_info:
                await orchestrator.run(input_data, ctx, script_stage_context)

        assert exc_info.value.report.passed is False

    @pytest.mark.asyncio
    async def test_gate_pass_attaches_report_to_metadata(self) -> None:
        """When gate passes, completeness report is attached to script metadata."""
        step = _make_step()
        input_data = _make_input()
        ctx = MagicMock()
        ctx.character_names = {}
        ctx.speaker_adjudication_cards = []
        ctx.story_context = {}
        script_stage_context = ScriptStageContext()
        good_script = _make_good_script()

        passing_report = ScriptCompletenessReport(
            passed=True,
            spoken_text_coverage=0.9,
            emotion_differentiation=0.5,
            voice_assignment_coverage=1.0,
        )

        with (
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_generate",
                  new_callable=AsyncMock, return_value=(good_script, [])),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_adjudicate",
                  new_callable=AsyncMock, return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_normalize",
                  return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_rules",
                  return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_review_llm",
                  new_callable=AsyncMock, return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_rewrite",
                  new_callable=AsyncMock, return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_sound_design",
                  new_callable=AsyncMock, return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.phase_finalize",
                  return_value=good_script),
            patch("novel_forge.tts.pipeline.script_orchestrator.validate_script_completeness",
                  return_value=passing_report),
        ):
            orchestrator = ScriptOrchestrator(step)
            result = await orchestrator.run(input_data, ctx, script_stage_context)

        assert "completeness_report" in result.metadata
        assert result.metadata["completeness_report"]["passed"] is True
        assert result.metadata["repair_verification"]["passed"] is True

    @pytest.mark.asyncio
    async def test_source_text_mutation_fails_before_script_can_be_persisted(self) -> None:
        step = _make_step()
        input_data = _make_input()
        ctx = MagicMock()
        ctx.character_names = {}
        ctx.speaker_adjudication_cards = []
        ctx.story_context = {}
        script_stage_context = ScriptStageContext()
        baseline = _make_good_script()
        mutated = baseline.model_copy(
            update={
                "segments": [
                    baseline.segments[0].model_copy(update={"text": "被改写的正文"}),
                    baseline.segments[1],
                ]
            }
        )
        passing_report = ScriptCompletenessReport(
            passed=True,
            spoken_text_coverage=1.0,
            emotion_differentiation=1.0,
            voice_assignment_coverage=1.0,
        )

        with (
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_generate",
                new_callable=AsyncMock,
                return_value=(baseline, []),
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_adjudicate",
                new_callable=AsyncMock,
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_normalize",
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_review_rules",
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_review_llm",
                new_callable=AsyncMock,
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_rewrite",
                new_callable=AsyncMock,
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_sound_design",
                new_callable=AsyncMock,
                return_value=baseline,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.phase_finalize",
                return_value=mutated,
            ),
            patch(
                "novel_forge.tts.pipeline.script_orchestrator.validate_script_completeness",
                return_value=passing_report,
            ),
        ):
            with pytest.raises(ScriptCompletenessError) as exc_info:
                await ScriptOrchestrator(step).run(input_data, ctx, script_stage_context)

        assert "改写正文源文本" in str(exc_info.value)
        failed_script = exc_info.value.script
        assert isinstance(failed_script, DubbingScript)
        evidence = failed_script.metadata["repair_verification"]
        assert evidence["passed"] is False
        source_change = next(
            item for item in evidence["changed_targets"] if item["field_path"].endswith(".text")
        )
        assert source_change["locator"]["segment_uid"] == baseline.segments[0].segment_uid
