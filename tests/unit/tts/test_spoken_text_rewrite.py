"""Unit tests for the spoken-text rewrite module.

Covers:
- validate_spoken_rewrite semantic validation rules
- rewrite_spoken_text integration with mock LLM
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.schemas import (
    ChapterTTSMetadata,
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    NarratorVoiceProfile,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_stage_context import ScriptStageContext
from novel_forge.tts.spoken_text_rewrite import (
    _parse_rewrite_batch_response,
    rewrite_spoken_text,
    validate_spoken_rewrite,
)

# ─── validate_spoken_rewrite ─────────────────────────────────────────────────


class TestValidateSpokenRewrite:
    """Tests for the semantic validation function."""

    def test_empty_rewrite_always_passes(self) -> None:
        """Empty spoken_text means 'no rewrite needed' — always valid."""
        passed, reason = validate_spoken_rewrite(
            "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来。",
            "",
        )
        assert passed is True
        assert reason == ""

    def test_whitespace_only_rewrite_passes(self) -> None:
        passed, reason = validate_spoken_rewrite("一些原文内容", "   ")
        assert passed is True

    def test_passes_good_rewrite(self) -> None:
        """A reasonable oral rewrite should pass validation."""
        original = "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来。"
        rewritten = "窗外的风吹进来，带着码头的海腥味，还有旧书页的霉味。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is True, f"Expected pass but got: {reason}"

    def test_rejects_too_short(self) -> None:
        """Rewrite that is less than 50% of original core length is rejected."""
        original = "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来，咨询椅空着。"
        rewritten = "风吹进来。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is False
        assert "too_short" in reason

    def test_rejects_too_long(self) -> None:
        """Rewrite that exceeds 130% of original core length is rejected."""
        original = "他站起来。"
        rewritten = "他慢慢地从椅子上站了起来，然后走到窗边，看着外面的风景，心里想着很多事情。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is False
        assert "too_long" in reason

    def test_rejects_html_tags(self) -> None:
        """Rewrite containing HTML tags is rejected."""
        original = "他看着窗外的风景。"
        rewritten = "他看着<b>窗外</b>的风景。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is False
        assert "html" in reason or "markdown" in reason

    def test_rejects_low_sequence_ratio(self) -> None:
        """Rewrite with very low character overlap is rejected."""
        original = "沈岸拿起钢笔，在空白处划了一道无意义的横线。"
        rewritten = "今天天气真好，我们去公园散步吧。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is False
        assert "sequence_ratio" in reason

    def test_keyword_preservation_passes(self) -> None:
        """Rewrite that preserves proper nouns passes."""
        original = "沈岸转身，目光扫过墙角那只半人高的旧玻璃罐。"
        rewritten = "沈岸转过身，看了看墙角那个旧玻璃罐。"
        passed, reason = validate_spoken_rewrite(original, rewritten)
        assert passed is True, f"Expected pass but got: {reason}"

    def test_required_character_name_must_be_preserved(self) -> None:
        original = "沈岸转身，林小满站在门口，苏晚留下的痕迹还在。"
        rewritten = "他转身，一个人站在门口，留下的痕迹还在。"
        passed, reason = validate_spoken_rewrite(
            original,
            rewritten,
            required_terms=("沈岸", "林小满", "苏晚"),
        )

        assert passed is False
        assert reason == "missing_required_terms=林小满,沈岸,苏晚"


# ─── rewrite_spoken_text integration ─────────────────────────────────────────


def _make_segment(
    index: int,
    text: str,
    seg_type: SegmentType = SegmentType.NARRATION,
    character_name: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text=text,
        character_name=character_name,
        emotion=EmotionTag.NEUTRAL,
    )


def _make_script(segments: list[DubbingSegment]) -> DubbingScript:
    return DubbingScript(chapter_number=1, segments=segments)


def _make_voice_team() -> VoiceTeamContract:
    return VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="char_1",
                character_name="林小满",
                voice_id="voice-1",
                character_role="protagonist",
            ),
        ]
    )


def _make_settings() -> SimpleNamespace:
    """Create a settings-like object with all spoken-rewrite config fields."""
    return SimpleNamespace(
        tts_spoken_rewrite_temperature=0.45,
        tts_spoken_rewrite_batch_size=30,
        tts_spoken_rewrite_min_length=8,
        tts_spoken_rewrite_min_length_ratio=0.5,
        tts_spoken_rewrite_max_length_ratio=1.3,
        tts_spoken_rewrite_min_sequence_ratio=0.35,
        tts_spoken_rewrite_min_confidence=0.6,
        tts_spoken_rewrite_context_window=1,
        tts_spoken_rewrite_narration_sentence_chars=30,
        tts_spoken_rewrite_dialogue_sentence_chars=20,
        tts_spoken_rewrite_inner_thought_sentence_chars=25,
        tts_spoken_rewrite_min_output_tokens=1024,
        tts_spoken_rewrite_output_tokens_per_segment=160,
        tts_script_context_max_items=8,
        tts_script_context_max_text_chars=240,
    )


class TestRewriteSpokenTextIntegration:
    """Integration tests with mocked LLM service."""

    @pytest.mark.asyncio
    async def test_no_candidates_returns_unchanged(self) -> None:
        """Segments shorter than threshold are not rewritten."""
        segments = [_make_segment(0, "短文本")]
        script = _make_script(segments)
        voice_team = _make_voice_team()

        result = await rewrite_spoken_text(
            script,
            voice_team,
            router=MagicMock(),
            builder=MagicMock(),
            settings=_make_settings(),
        )
        # No LLM call should be made; script unchanged.
        assert result.segments[0].spoken_text == ""

    @pytest.mark.asyncio
    async def test_successful_rewrite_applied(self) -> None:
        """Valid LLM rewrite is written to segment.spoken_text."""
        original_text = "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来。"
        rewritten_text = "窗外的风吹进来，带着码头的海腥味，还有旧书页的霉味。"
        segments = [_make_segment(0, original_text)]
        script = _make_script(segments)
        voice_team = _make_voice_team()

        mock_response = {
            "rewrites": [{"segment_index": 0, "spoken_text": rewritten_text, "confidence": 0.9}]
        }

        mock_service_instance = AsyncMock()
        mock_service_instance.call_with_retry = AsyncMock(return_value=mock_response)
        mock_service_cls = MagicMock(return_value=mock_service_instance)

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            mock_service_cls,
        ):
            result = await rewrite_spoken_text(
                script,
                voice_team,
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        assert result.segments[0].spoken_text == rewritten_text
        assert result.metadata.get("spoken_text_rewrite", {}).get("rewritten") == 1

    @pytest.mark.asyncio
    async def test_prompt_receives_only_relevant_bounded_stage_context(self) -> None:
        focus = _make_segment(
            1,
            "林小满看着码头的灯，终于把压在心里的那句话说了出来。",
            SegmentType.DIALOGUE,
            "林小满",
        ).model_copy(
            update={
                "character_id": "char_1",
                "scene_context": "码头｜夜晚",
                "tone_hint": "压低声音",
            }
        )
        script = _make_script(
            [
                _make_segment(0, "门响了。"),
                focus,
                _make_segment(2, "海风起了。"),
            ]
        )
        context = ScriptStageContext(
            character_voices=(
                {
                    "character_name": "林小满",
                    "sentence_profile": "短句，先克制后落重音",
                    "sample_lines": ["我知道。"],
                },
                {
                    "character_name": "未出场角色",
                    "sentence_profile": "不应进入当前批次",
                },
            ),
            style_profile={"emotional_tone": "克制", "unused_blob": "x" * 500},
            story_context={"genre": "悬疑", "premise": "不应投影的长梗概"},
            narrator_profile=NarratorVoiceProfile(
                narration_distance="close",
                style_keywords=["冷静", "贴近人物"],
            ),
            tts_metadata=ChapterTTSMetadata(),
            scene_intents=(
                {"scene_id": "scene_1", "location": "码头", "emotional_beat": "坦白"},
                {"scene_id": "scene_2", "location": "山顶", "emotional_beat": "追逐"},
            ),
        )
        service = AsyncMock()
        service.call_with_retry = AsyncMock(
            return_value={"rewrites": [{"segment_index": 1, "spoken_text": "", "confidence": 1.0}]}
        )

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            MagicMock(return_value=service),
        ):
            await rewrite_spoken_text(
                script,
                _make_voice_team(),
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
                context=context,
            )

        call_context = service.call_with_retry.await_args.args[1]
        cards = call_context["stage_cards"]
        assert cards["style_profile"] == {"emotional_tone": "克制"}
        assert cards["story_context"] == {"genre": "悬疑"}
        assert [item["character_name"] for item in cards["character_voices"]] == ["林小满"]
        assert cards["scene_intents"] == [
            {"scene_id": "scene_1", "location": "码头", "emotional_beat": "坦白"}
        ]
        assert cards["narrator_profile"]["narration_distance"] == "close"
        segment_card = cards["segments"][0]
        assert [item["text"] for item in segment_card["continuity_context"]] == [
            "门响了。",
            "海风起了。",
        ]
        assert cards["rewrite_policy"]["dialogue_sentence_chars"] == 20
        assert service.call_with_retry.await_args.kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_numbers_are_preserved_by_runtime_validation(self) -> None:
        original = "林小满等了12分钟才开门，然后慢慢走进空荡的房间。"
        changed_number = "林小满等了一会儿才开门，然后慢慢走进空荡的房间。"
        service = AsyncMock()
        service.call_with_retry = AsyncMock(
            return_value={
                "rewrites": [
                    {
                        "segment_index": 0,
                        "spoken_text": changed_number,
                        "confidence": 0.9,
                    }
                ]
            }
        )

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            MagicMock(return_value=service),
        ):
            result = await rewrite_spoken_text(
                _make_script([_make_segment(0, original)]),
                _make_voice_team(),
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        assert result.segments[0].spoken_text == ""
        assert result.metadata["spoken_text_rewrite"]["rejected"] == 1

    @pytest.mark.asyncio
    async def test_low_confidence_rewrite_is_not_applied(self) -> None:
        original = "窗外的风卷着码头的海腥味和旧书页的霉味涌进来。"
        rewritten = "窗外的风吹进来，带着码头的海腥味，还有旧书页的霉味。"
        service = AsyncMock()
        service.call_with_retry = AsyncMock(
            return_value={
                "rewrites": [
                    {
                        "segment_index": 0,
                        "spoken_text": rewritten,
                        "confidence": 0.59,
                    }
                ]
            }
        )

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            MagicMock(return_value=service),
        ):
            result = await rewrite_spoken_text(
                _make_script([_make_segment(0, original)]),
                _make_voice_team(),
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        assert result.segments[0].spoken_text == ""
        assert result.metadata["spoken_text_rewrite"]["rejection_reasons"] == {
            "confidence_below_threshold": 1
        }


class TestRewriteResponseFormat:
    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            ({"rewrites": []}, "rewrite_count_mismatch"),
            (
                {
                    "rewrites": [
                        {"segment_index": 7, "spoken_text": "保留", "confidence": 0.9},
                        {"segment_index": 7, "spoken_text": "重复", "confidence": 0.8},
                    ]
                },
                "duplicate_segment_index",
            ),
            (
                {"rewrites": [{"segment_index": 9, "spoken_text": "越界", "confidence": 0.9}]},
                "unexpected_segment_index",
            ),
            (
                {
                    "rewrites": [
                        {
                            "segment_index": 7,
                            "spoken_text": "多余字段",
                            "confidence": 0.9,
                            "reason": "not allowed",
                        }
                    ]
                },
                "invalid_rewrite_record_shape",
            ),
            (
                {"rewrites": [{"segment_index": 7, "spoken_text": "越界", "confidence": 1.2}]},
                "confidence_out_of_range",
            ),
        ],
    )
    def test_rejects_malformed_or_incomplete_batch(
        self,
        response: dict[str, object],
        reason: str,
    ) -> None:
        expected = (7, 8) if reason == "duplicate_segment_index" else (7,)
        parsed, actual_reason = _parse_rewrite_batch_response(response, expected)
        assert parsed is None
        assert actual_reason == reason

    def test_accepts_exact_one_record_per_expected_segment(self) -> None:
        response = {
            "rewrites": [
                {"segment_index": 7, "spoken_text": "第一段", "confidence": 0.9},
                {"segment_index": 8, "spoken_text": "", "confidence": 1.0},
            ]
        }
        parsed, reason = _parse_rewrite_batch_response(response, (7, 8))
        assert reason == ""
        assert parsed is not None
        assert set(parsed) == {7, 8}

    def test_prompt_and_native_schema_share_the_rewrite_contract(self) -> None:
        request = PromptBuilder().build(
            TaskType.TTS_REWRITE_SPOKEN_TEXT,
            {
                "output_language": "zh",
                "stage_cards": {
                    "segments": [
                        {
                            "segment_index": 7,
                            "segment_type": "narration",
                            "text": "需要改写的原文。",
                        }
                    ],
                    "rewrite_policy": {
                        "min_rewrite_length": 9,
                        "min_length_ratio": 0.65,
                        "max_length_ratio": 1.2,
                        "min_confidence": 0.72,
                        "narration_sentence_chars": 28,
                        "dialogue_sentence_chars": 18,
                        "inner_thought_sentence_chars": 22,
                    },
                },
            },
        )
        prompt = request.messages[-1]["content"]
        assert "65%-\n   120%" in prompt
        assert "至少 0.72 的信心" in prompt
        assert "不得遗漏、重复或返回批次外索引" in prompt

        schema = request.response_json_schema
        assert schema is not None
        assert schema["required"] == ["rewrites"]
        assert schema["additionalProperties"] is False
        item_schema = schema["properties"]["rewrites"]["items"]
        assert set(item_schema["required"]) == {"segment_index", "spoken_text", "confidence"}
        assert item_schema["additionalProperties"] is False
        assert item_schema["properties"]["confidence"] == {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }

    @pytest.mark.asyncio
    async def test_non_contiguous_segment_index_updates_the_correct_position(self) -> None:
        original_text = "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来。"
        rewritten_text = "窗外的风吹进来，带着码头的海腥味，还有旧书页的霉味。"
        segments = [
            _make_segment(17, original_text),
            _make_segment(42, "这一段保持原样，不应被前一段的外部索引覆盖。"),
        ]
        script = _make_script(segments)
        voice_team = _make_voice_team()
        mock_response = {
            "rewrites": [
                {"segment_index": 17, "spoken_text": rewritten_text, "confidence": 0.9},
                {"segment_index": 42, "spoken_text": "", "confidence": 1.0},
            ]
        }
        mock_service_instance = AsyncMock()
        mock_service_instance.call_with_retry = AsyncMock(return_value=mock_response)

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            MagicMock(return_value=mock_service_instance),
        ):
            result = await rewrite_spoken_text(
                script,
                voice_team,
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        assert result.segments[0].segment_index == 17
        assert result.segments[0].spoken_text == rewritten_text
        assert result.segments[1].segment_index == 42
        # Output contract: empty LLM decision fills spoken_text with original text
        assert result.segments[1].spoken_text == "这一段保持原样，不应被前一段的外部索引覆盖。"

    @pytest.mark.asyncio
    async def test_invalid_rewrite_rejected(self) -> None:
        """Rewrite that fails validation is not applied."""
        original_text = "窗外的风卷着远处码头的海腥味和近处旧书页的霉味涌进来。"
        # Too short - will fail validation
        bad_rewrite = "风。"
        segments = [_make_segment(0, original_text)]
        script = _make_script(segments)
        voice_team = _make_voice_team()

        mock_response = {
            "rewrites": [{"segment_index": 0, "spoken_text": bad_rewrite, "confidence": 0.5}]
        }

        mock_service_instance = AsyncMock()
        mock_service_instance.call_with_retry = AsyncMock(return_value=mock_response)
        mock_service_cls = MagicMock(return_value=mock_service_instance)

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            mock_service_cls,
        ):
            result = await rewrite_spoken_text(
                script,
                voice_team,
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        # spoken_text should remain empty because rewrite was rejected
        assert result.segments[0].spoken_text == ""
        meta = result.metadata.get("spoken_text_rewrite", {})
        assert meta.get("rejected", 0) >= 1

    @pytest.mark.asyncio
    async def test_empty_spoken_text_not_counted_as_rewritten(self) -> None:
        """LLM returning empty spoken_text means 'no rewrite needed'."""
        original_text = "她开口，声音清脆，带着点南方口音的柔软。"
        segments = [_make_segment(0, original_text)]
        script = _make_script(segments)
        voice_team = _make_voice_team()

        mock_response = {"rewrites": [{"segment_index": 0, "spoken_text": "", "confidence": 1.0}]}

        mock_service_instance = AsyncMock()
        mock_service_instance.call_with_retry = AsyncMock(return_value=mock_response)
        mock_service_cls = MagicMock(return_value=mock_service_instance)

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            mock_service_cls,
        ):
            result = await rewrite_spoken_text(
                script,
                voice_team,
                router=MagicMock(),
                builder=MagicMock(),
                settings=_make_settings(),
            )

        # Empty spoken_text from LLM = no rewrite; output contract fills with original text
        assert result.segments[0].spoken_text == original_text
        # Not counted as rewritten or rejected
        meta = result.metadata.get("spoken_text_rewrite", {})
        assert meta.get("rewritten") == 0
        assert meta.get("rejected") == 0

    @pytest.mark.asyncio
    async def test_llm_failure_graceful_fallback(self) -> None:
        """When LLM call fails, segments keep empty spoken_text."""
        original_text = "行李箱滚轮碾过老木地板的声响从楼梯口传来，由远及近。沈岸抬起头。"
        segments = [_make_segment(0, original_text)]
        script = _make_script(segments)
        voice_team = _make_voice_team()

        mock_settings = _make_settings()

        mock_service_instance = AsyncMock()
        mock_service_instance.call_with_retry = AsyncMock(side_effect=Exception("LLM unavailable"))
        mock_service_cls = MagicMock(return_value=mock_service_instance)

        with patch(
            "novel_forge.model_runtime.StructuredModelService",
            mock_service_cls,
        ):
            result = await rewrite_spoken_text(
                script,
                voice_team,
                router=MagicMock(),
                builder=MagicMock(),
                settings=mock_settings,
            )

        # spoken_text should remain empty (fallback to reading original)
        assert result.segments[0].spoken_text == ""
        # Metadata should record the failure
        meta = result.metadata.get("spoken_text_rewrite", {})
        assert meta.get("rejected", 0) >= 1
