"""Unit tests for deterministic pause-marker injection and validation.

Covers:
- build_rule_pause_markers punctuation/structure rules
- inject_pause_markers script-level behavior (idempotency, platform filter)
- sanitize_for_speech pause-marker protection and ellipsis preservation
- auto_fix_pause_markers / validate_pause_markers format repair
"""

from __future__ import annotations

from novel_forge.tts.pipeline.pause_marker_injection import (
    build_rule_pause_markers,
    inject_pause_markers,
    retarget_native_synthesis_annotations,
    strip_pause_markers,
    uses_native_pause_timing,
)
from novel_forge.tts.pipeline.timeline_builder import build_timeline
from novel_forge.tts.pipeline.validate_pause_markers import (
    auto_fix_pause_markers,
    find_invalid_pause_markers,
    validate_pause_markers,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
)
from novel_forge.tts.spoken_text_rewrite import sanitize_for_speech


def _make_segment(
    index: int,
    text: str,
    seg_type: SegmentType = SegmentType.NARRATION,
    character_name: str = "",
    *,
    spoken_text: str | None = None,
    source_paragraph: int = 0,
    scene_context: str = "",
) -> DubbingSegment:
    return DubbingSegment(
        segment_index=index,
        segment_type=seg_type,
        text=text,
        character_name=character_name,
        emotion=EmotionTag.NEUTRAL,
        spoken_text=text if spoken_text is None else spoken_text,
        source_paragraph=source_paragraph,
        scene_context=scene_context,
    )


def _make_script(segments: list[DubbingSegment]) -> DubbingScript:
    return DubbingScript(chapter_number=1, segments=segments)


# ─── build_rule_pause_markers: sentence-level rules ─────────────────────────


class TestSentenceRules:
    def test_full_stop_gets_sentence_pause(self) -> None:
        seg = _make_segment(0, "他推开门走了进去。")
        result = build_rule_pause_markers(seg)
        assert "<#0.6#>" in result
        assert result.endswith("。<#0.6#>")

    def test_exclamation_gets_short_pause(self) -> None:
        seg = _make_segment(0, "快跑！")
        result = build_rule_pause_markers(seg)
        assert result.endswith("！<#0.4#>")

    def test_question_gets_short_pause(self) -> None:
        seg = _make_segment(0, "你是谁？")
        result = build_rule_pause_markers(seg)
        assert result.endswith("？<#0.4#>")

    def test_combined_marks_emit_single_pause(self) -> None:
        seg = _make_segment(0, "怎么会这样？！")
        result = build_rule_pause_markers(seg)
        assert result.count("<#0.4#>") == 1
        assert result.endswith("？！<#0.4#>")

    def test_ellipsis_narration_gets_dramatic_pause(self) -> None:
        seg = _make_segment(0, "夜色渐渐沉了下去……")
        result = build_rule_pause_markers(seg)
        assert "<#1.2#>" in result
        assert "…" not in result

    def test_ellipsis_dialogue_gets_hesitation_pause(self) -> None:
        seg = _make_segment(0, "我……我不知道……", SegmentType.DIALOGUE)
        result = build_rule_pause_markers(seg)
        assert "<#0.8#>" in result
        assert "<#1.2#>" not in result

    def test_english_dots_are_ellipsis(self) -> None:
        seg = _make_segment(0, "Wait... what?")
        result = build_rule_pause_markers(seg)
        assert "<#1.2#>" in result

    def test_single_dot_is_preserved(self) -> None:
        seg = _make_segment(0, "价格为 3.5 元。")
        result = build_rule_pause_markers(seg)
        assert "3.5" in result

    def test_long_sentence_commas_get_light_pause(self) -> None:
        text = "他沿着长街走了很久很久，经过许多亮着暖黄灯光的窗口，才在一扇漆成深蓝色的旧门前停下。"
        assert len(text) >= 40
        seg = _make_segment(0, text)
        result = build_rule_pause_markers(seg)
        assert result.count("<#0.25#>") == 2

    def test_short_sentence_commas_not_marked(self) -> None:
        seg = _make_segment(0, "他走，她停。")
        result = build_rule_pause_markers(seg)
        assert "<#0.25#>" not in result

    def test_empty_spoken_text_unchanged(self) -> None:
        seg = _make_segment(0, "---", spoken_text="")
        result = build_rule_pause_markers(seg)
        assert result == ""


# ─── build_rule_pause_markers: inter-segment leading rules ──────────────────


class TestInterSegmentRules:
    def test_scene_change_gets_two_second_pause(self) -> None:
        prev = _make_segment(0, "第一天在城里。", scene_context="city")
        cur = _make_segment(1, "第二天，他回到了山村。", scene_context="village")
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#2#>")

    def test_paragraph_gap_gets_two_second_pause(self) -> None:
        prev = _make_segment(0, "第一段。", source_paragraph=1)
        cur = _make_segment(1, "第三段的内容。", source_paragraph=3)
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#2#>")

    def test_paragraph_end_gets_one_and_half_second_pause(self) -> None:
        prev = _make_segment(0, "第一段。", source_paragraph=1)
        cur = _make_segment(1, "第二段的内容。", source_paragraph=2)
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#1.5#>")

    def test_dialogue_to_narration_transition(self) -> None:
        prev = _make_segment(0, "我明天就走。", SegmentType.DIALOGUE)
        cur = _make_segment(1, "他说完，转身离开了房间。")
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#0.8#>")

    def test_speaker_alternation(self) -> None:
        prev = _make_segment(
            0, "你最近还好吗？", SegmentType.DIALOGUE, character_name="阿明"
        )
        cur = _make_segment(
            1, "还好，就是有点忙。", SegmentType.DIALOGUE, character_name="小满"
        )
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#0.6#>")

    def test_same_speaker_no_alternation_pause(self) -> None:
        prev = _make_segment(
            0, "你最近还好吗？", SegmentType.DIALOGUE, character_name="阿明"
        )
        cur = _make_segment(
            1, "还好。", SegmentType.DIALOGUE, character_name="阿明"
        )
        result = build_rule_pause_markers(cur, prev=prev)
        assert not result.startswith("<#0.6#>")

    def test_attribution_phrase_pause(self) -> None:
        prev = _make_segment(0, "老者低声说道")
        cur = _make_segment(1, "这地方不干净。", SegmentType.DIALOGUE)
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#0.3#>")

    def test_first_segment_no_leading_pause(self) -> None:
        seg = _make_segment(0, "故事开始了。")
        result = build_rule_pause_markers(seg, prev=None)
        assert not result.startswith("<#")

    def test_scene_change_beats_paragraph_end(self) -> None:
        prev = _make_segment(0, "第一段。", source_paragraph=1, scene_context="city")
        cur = _make_segment(
            1, "第二段的内容。", source_paragraph=2, scene_context="village"
        )
        result = build_rule_pause_markers(cur, prev=prev)
        assert result.startswith("<#2#>")
        assert not result.startswith("<#1.5#>")


# ─── inject_pause_markers: script level ─────────────────────────────────────


class TestInjectPauseMarkers:
    def test_injects_and_attaches_metadata(self) -> None:
        segments = [
            _make_segment(0, "他推开门走了进去。"),
            _make_segment(1, "你终于来了。", SegmentType.DIALOGUE, character_name="小满"),
        ]
        script = inject_pause_markers(_make_script(segments))
        assert "<#0.6#>" in script.segments[0].spoken_text
        assert "<#0.6#>" in script.segments[1].spoken_text
        meta = script.metadata["pause_marker_injection"]
        assert meta["injected_segments"] == 2
        assert meta["platform"] == "minimax"

    def test_skips_unsupported_platform(self) -> None:
        segments = [_make_segment(0, "他推开门走了进去。")]
        script = inject_pause_markers(_make_script(segments), platform="bailian")
        assert script.metadata.get("pause_marker_injection") is None
        assert "<#0.6#>" not in script.segments[0].spoken_text

    def test_normalizes_platform_identifier_before_capability_lookup(self) -> None:
        script = inject_pause_markers(
            _make_script([_make_segment(0, "他推开门走了进去。")]),
            platform="MiniMax",
            platforms=["MINIMAX"],
        )

        assert "<#0.6#>" in script.segments[0].spoken_text
        assert script.metadata["pause_marker_injection"]["platform"] == "minimax"

    def test_idempotent_double_injection(self) -> None:
        segments = [
            _make_segment(0, "他推开门走了进去。"),
            _make_segment(1, "快跑！", SegmentType.DIALOGUE, character_name="阿明"),
        ]
        first = inject_pause_markers(_make_script(segments))
        second = inject_pause_markers(first)
        assert second.segments[0].spoken_text == first.segments[0].spoken_text
        assert second.segments[1].spoken_text == first.segments[1].spoken_text
        # Re-injection must not double markers.
        assert second.segments[0].spoken_text.count("<#0.6#>") == 1

    def test_original_text_field_unchanged(self) -> None:
        segments = [_make_segment(0, "他推开门走了进去。")]
        script = inject_pause_markers(_make_script(segments))
        assert script.segments[0].text == "他推开门走了进去。"

    def test_strip_pause_markers(self) -> None:
        assert strip_pause_markers("你好。<#0.6#>再见<#1.5#>") == "你好。再见"

    def test_native_pause_timing_has_no_second_timeline_gap(self) -> None:
        script = inject_pause_markers(
            _make_script(
                [
                    _make_segment(0, "第一段。", source_paragraph=1),
                    _make_segment(1, "第二段。", source_paragraph=2),
                ]
            )
        )
        assert uses_native_pause_timing(script) is True
        assert script.segments[1].synthesis_text.startswith("<#1.5#>")

        timeline = build_timeline(
            script,
            [
                SynthesisResult(
                    segment_index=0,
                    status=SynthesisStatus.COMPLETED,
                    duration_ms=1000,
                ),
                SynthesisResult(
                    segment_index=1,
                    status=SynthesisStatus.COMPLETED,
                    # Includes MiniMax's native 1.5s leading marker.
                    duration_ms=2500,
                ),
            ],
        )
        assert timeline.entries[1].start_ms == timeline.entries[0].end_ms
        assert timeline.total_duration_ms == 3500

    def test_retarget_removes_speech_28_controls_for_unsupported_target(self) -> None:
        segment = _make_segment(0, "她轻轻叹了口气。")
        segment = segment.model_copy(
            update={"emotion": EmotionTag.SAD, "emotion_intensity": 0.9}
        )
        original = inject_pause_markers(_make_script([segment]), model_id="speech-2.8-hd")

        retargeted = retarget_native_synthesis_annotations(
            original,
            previous_platform="minimax",
            previous_model_id="speech-2.8-hd",
            platform="dashscope",
        )

        assert "<#" not in retargeted.segments[0].spoken_text
        assert "(sighs)" not in retargeted.segments[0].spoken_text
        assert "pause_marker_injection" not in retargeted.metadata

    def test_retarget_drops_speech_28_tag_but_keeps_minimax_pause(self) -> None:
        segment = _make_segment(0, "她轻轻叹了口气。")
        segment = segment.model_copy(
            update={"emotion": EmotionTag.SAD, "emotion_intensity": 0.9}
        )
        original = inject_pause_markers(_make_script([segment]), model_id="speech-2.8-hd")

        retargeted = retarget_native_synthesis_annotations(
            original,
            previous_platform="minimax",
            previous_model_id="speech-2.8-hd",
            platform="minimax",
            model_id="speech-2.6-hd",
        )

        assert "<#0.6#>" in retargeted.segments[0].spoken_text
        assert "(sighs)" not in retargeted.segments[0].spoken_text
        assert retargeted.metadata["pause_marker_injection"]["model_id"] == "speech-2.6-hd"


# ─── sanitize_for_speech compatibility ──────────────────────────────────────


class TestSanitizeCompatibility:
    def test_pause_markers_survive_sanitization(self) -> None:
        text = "他停了一下。<#0.6#>然后继续走。<#1.5#>"
        result = sanitize_for_speech(text)
        assert "<#0.6#>" in result
        assert "<#1.5#>" in result

    def test_ellipsis_preserved_instead_of_comma(self) -> None:
        result = sanitize_for_speech("他想了想……还是算了。")
        assert "…" in result
        assert result.count("，") == 0

    def test_plain_angle_brackets_still_stripped(self) -> None:
        result = sanitize_for_speech("他（低声）说道。<not-a-marker>")
        assert "<not-a-marker>" not in result

    def test_sanitized_injected_roundtrip(self) -> None:
        """Injected markers survive the sanitize fallback path intact."""
        seg = _make_segment(0, "他推开门走了进去。")
        injected = build_rule_pause_markers(seg)
        result = sanitize_for_speech(injected)
        assert "<#0.6#>" in result


# ─── validate_pause_markers: format auto-fix ────────────────────────────────


class TestValidatePauseMarkers:
    def test_fix_range_to_average(self) -> None:
        fixed, count = auto_fix_pause_markers("停一下<#0.3-0.5#>再说。")
        assert count == 1
        assert "<#0.4#>" in fixed

    def test_fix_range_with_tilde(self) -> None:
        fixed, count = auto_fix_pause_markers("<#0.5~0.8#>")
        assert count == 1
        assert "<#0.65#>" in fixed

    def test_fix_spurious_s_suffix(self) -> None:
        fixed, count = auto_fix_pause_markers("<#0.6s#>")
        assert count == 1
        assert "<#0.6#>" in fixed

    def test_fix_missing_closing_hash(self) -> None:
        fixed, count = auto_fix_pause_markers("<#0.6>")
        assert count == 1
        assert "<#0.6#>" in fixed

    def test_fix_whitespace_around_number(self) -> None:
        fixed, count = auto_fix_pause_markers("<# 0.6 #>")
        assert count == 1
        assert "<#0.6#>" in fixed

    def test_combined_fixes_in_order(self) -> None:
        fixed, count = auto_fix_pause_markers("<#0.3-0.5#> <#0.6s#> <#0.6> <# 0.6 #>")
        assert count == 4
        assert fixed == "<#0.4#> <#0.6#> <#0.6#> <#0.6#>"

    def test_no_changes_when_valid(self) -> None:
        text = "你好。<#0.6#>再见。<#1.5#>"
        fixed, count = auto_fix_pause_markers(text)
        assert count == 0
        assert fixed is text

    def test_find_invalid_markers(self) -> None:
        invalid = find_invalid_pause_markers("正常<#0.6#>坏标记<#abc#>")
        assert invalid == ["<#abc#>"]

    def test_validate_passes_for_fixed_text(self) -> None:
        valid, result = validate_pause_markers("你好<#0.6s#>")
        assert valid is True
        assert "<#0.6#>" in result

    def test_validate_reports_unfixable(self) -> None:
        valid, reason = validate_pause_markers("坏标记<#abc#>")
        assert valid is False
        assert "unfixable" in reason
