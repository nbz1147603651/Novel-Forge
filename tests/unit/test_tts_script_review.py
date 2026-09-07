from __future__ import annotations

from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    ParalinguisticTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import unresolved_speaker_indices
from novel_forge.tts.script_review import (
    apply_dubbing_review_decisions,
    apply_tts_platform_contract,
    build_dubbing_review_stage_cards,
    review_and_repair_dubbing_script,
)


def test_professional_review_recovers_precise_glass_sfx_and_removes_auto_speed() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他扬手一挥，桌边的玻璃杯摔碎在地上。",
                speed_override=0.78,
                vol_override=0.9,
                pitch_override=-2,
            )
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    segment = reviewed.segments[0]
    assert segment.speed_override is None
    assert segment.vol_override is None
    assert segment.pitch_override is None
    assert len(reviewed.sfx_cues) == 1
    cue = reviewed.sfx_cues[0]
    assert cue.effect_name == "玻璃碎裂"
    assert cue.trigger_segment_index == 0
    assert cue.offset_ms > 0
    assert cue.allow_dialogue_overlap is True
    assert cue.narrative_priority >= 95
    review = reviewed.metadata["professional_script_review"]
    assert review["removed_generated_numeric_overrides"] == 3
    assert review["humanize_prescreen"]["engine"] == "HumanizeScanStep"


def test_professional_review_proactively_recovers_short_foley_events() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="她把牛皮纸文件袋放下，纸张擦过桌沿。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="楼道里的脚步声慢慢靠近，又突然停下。",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="林小满把钥匙插入锁芯，轻轻转动。",
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.NARRATION,
                text="她拆开糖纸，细碎的包装纸发出轻响。",
            ),
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    names = {cue.effect_name for cue in reviewed.sfx_cues}
    assert {"纸张文件声", "脚步声", "钥匙门锁声", "包装纸轻响"} <= names
    assert all(cue.duration_ms <= 2_200 for cue in reviewed.sfx_cues)
    audit = reviewed.metadata["professional_script_review"]["sound_effect_audit"]
    assert audit["mode"] == "high_confidence_foley_recovery"
    assert audit["recovered_count"] >= 4


def test_professional_review_rolls_back_ai_like_spoken_adaptation_and_noise_tags() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="不用。",
                spoken_text="首先不用，其次真的不用。",
                paralinguistic_tags=[
                    ParalinguisticTag(tag_type="laugh", position=0.1),
                    ParalinguisticTag(tag_type="pause", position=0.5, duration_ms=250),
                ],
            )
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    segment = reviewed.segments[0]
    assert segment.spoken_text == ""
    assert [tag.tag_type for tag in segment.paralinguistic_tags] == ["pause"]
    review = reviewed.metadata["professional_script_review"]
    assert review["spoken_text_rollback_segment_indices"] == [0]
    assert review["removed_unsupported_performance_tags"] == 1


def test_platform_contract_removes_unrenderable_minimax_legacy_sound_tags() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="先别说话。",
                paralinguistic_tags=[
                    ParalinguisticTag(tag_type="breath", position=0.1),
                    ParalinguisticTag(tag_type="pause", position=0.5, duration_ms=300),
                    ParalinguisticTag(tag_type="laugh", position=0.8),
                ],
            )
        ],
    )

    legacy = apply_tts_platform_contract(
        script,
        provider_id="minimax",
        model_id="speech-2.6-hd",
    )
    speech_28 = apply_tts_platform_contract(
        script,
        provider_id="minimax",
        model_id="speech-2.8-hd",
    )

    assert [tag.tag_type for tag in legacy.segments[0].paralinguistic_tags] == ["pause"]
    assert legacy.metadata["tts_platform_contract"]["removed_unsupported_interjections"] == 2
    assert len(speech_28.segments[0].paralinguistic_tags) == 3


def test_speaker_gate_detects_legacy_dialogue_without_a_character() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.DIALOGUE,
                text="这句话不能交给旁白。",
            )
        ],
    )

    assert unresolved_speaker_indices(script) == (3,)


def test_speaker_gate_ignores_audit_ids_removed_from_the_current_script() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=10,
                segment_type=SegmentType.NARRATION,
                text="当前脚本只剩这一段。",
            )
        ],
        metadata={
            "speaker_adjudication": {"unresolved_segment_indices": [99]},
            "professional_script_review": {
                "llm_review": {"manual_review_segment_indices": [99]}
            },
        },
    )

    assert unresolved_speaker_indices(script) == ()


def test_dubbing_review_reuses_humanize_as_scoped_candidates_only() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="当然！此外，我已经查完了。",
            )
        ],
    )
    team = VoiceTeamContract(entries=[VoiceCastEntry(character_id="c1", character_name="沈岸")])

    cards = build_dubbing_review_stage_cards(
        script,
        team,
        tts_platform="minimax",
        tts_model="speech-2.6-hd",
    )

    candidates = cards["segments"][0]["humanize_candidates"]
    applicability = {item["pattern_id"]: item["applicability"] for item in candidates}
    assert applicability["collaborative_artifact"] == "portable_residue"
    assert applicability["ai_vocabulary"] == "novel_context_only"
    assert all("span_start" in item and "span_end" in item for item in candidates)
    assert all(item["actionable"] is False for item in candidates)
    assert cards["review_boundaries"]["humanize_reuse"] == "candidate_signals_only"
    assert cards["tts_platform"] == "minimax"
    assert cards["tts_model"] == "speech-2.6-hd"


def test_professional_review_records_precise_humanize_candidate_segments() -> None:
    script = DubbingScript(
        chapter_number=8,
        segments=[
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="当然！此外，这不是借口，而是事实。",
            )
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)
    prescreen = reviewed.metadata["professional_script_review"]["humanize_prescreen"]

    assert prescreen["candidate_count"] >= 2
    assert prescreen["candidate_segment_indices"] == [3]
    assert prescreen["portable_residue_count"] >= 1
    assert prescreen["counts_by_pattern"]["collaborative_artifact"] == 1
    candidate = prescreen["segment_candidates"][0]["candidates"][0]
    assert candidate["span_start"] is not None
    assert candidate["span_end"] is not None


def test_specialist_review_applies_only_source_anchored_performance_repairs() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="你终于来了。",
                emotion=EmotionTag.SAD,
            )
        ],
    )
    response = {
        "decisions": [
            {
                "segment_index": 0,
                "verdict": "revise_performance",
                "issue_types": ["emotional_intent", "performance_naturalness"],
                "confidence": 0.92,
                "evidence": "你终于来了。",
                "rationale": "等待后的松弛与亲近，不是悲伤。",
                "recommended_segment_type": "unchanged",
                "recommended_character_id": "",
                "recommended_emotion": "tender",
                "recommended_tone_hint": "先松一口气，尾音收住",
                "recommended_paralinguistic_tags": [],
                "spoken_text": "这是不得采纳的改写",
                "speed_override": 0.7,
            }
        ],
        "reviewed_segment_count": 1,
        "overall_verdict": "passed",
        "summary": "已修复一处情绪误判。",
    }

    reviewed = apply_dubbing_review_decisions(script, response, allowed_character_ids={"c1"})

    segment = reviewed.segments[0]
    assert segment.text == "你终于来了。"
    assert segment.spoken_text == ""
    assert segment.speed_override is None
    assert segment.emotion is EmotionTag.TENDER
    assert segment.tone_hint == "先松一口气，尾音收住"
    llm_review = reviewed.metadata["professional_script_review"]["llm_review"]
    assert llm_review["status"] == "passed"
    assert llm_review["applied_performance_repairs"][0]["fields"] == [
        "emotion",
        "tone_hint",
    ]


def test_specialist_role_concern_never_silently_changes_speaker_and_blocks_synthesis() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=4,
                segment_type=SegmentType.NARRATION,
                text="别碰它。",
            )
        ],
    )
    response = {
        "decisions": [
            {
                "segment_index": 4,
                "verdict": "manual_review",
                "issue_types": ["acoustic_role", "speaker_ownership"],
                "confidence": 0.94,
                "evidence": "别碰它。",
                "rationale": "文本表现为当前发声，但当前被标为旁白。",
                "recommended_segment_type": "dialogue",
                "recommended_character_id": "c1",
                "recommended_emotion": "unchanged",
                "recommended_tone_hint": "",
                "recommended_paralinguistic_tags": [],
            }
        ],
        "reviewed_segment_count": 1,
        "overall_verdict": "needs_review",
        "summary": "一处声音角色需人工确认。",
    }

    reviewed = apply_dubbing_review_decisions(script, response, allowed_character_ids={"c1"})

    assert reviewed.segments[0].segment_type is SegmentType.NARRATION
    assert reviewed.segments[0].character_id == ""
    assert unresolved_speaker_indices(reviewed) == (4,)
    llm_review = reviewed.metadata["professional_script_review"]["llm_review"]
    assert llm_review["manual_review_segment_indices"] == [4]


def test_specialist_speaker_review_accepts_evidence_from_the_same_source_paragraph() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="林小满把文件袋推到苏晚面前。",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="屋外的雨越来越密。",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="苏晚低头看着那只袋子。",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.DIALOGUE,
                character_id="su",
                character_name="苏晚",
                text="不是报酬，就是顺手买的。",
                source_paragraph=3,
            ),
        ],
    )
    response = {
        "decisions": [
            {
                "segment_index": 3,
                "verdict": "manual_review",
                "issue_types": ["speaker_ownership"],
                "confidence": 0.94,
                "evidence": "林小满把文件袋推到苏晚面前。",
                "rationale": "源段落的行动主体与当前标注角色冲突。",
                "recommended_segment_type": "dialogue",
                "recommended_character_id": "lin",
            }
        ],
        "reviewed_segment_count": 4,
        "overall_verdict": "needs_review",
        "summary": "说话人需要人工确认。",
    }

    reviewed = apply_dubbing_review_decisions(
        script,
        response,
        allowed_character_ids={"lin", "su"},
    )

    assert reviewed.segments[3].character_id == "su", "review never silently changes ownership"
    llm_review = reviewed.metadata["professional_script_review"]["llm_review"]
    assert llm_review["manual_review_segment_indices"] == [3]
    assert llm_review["manual_review_items"][0]["recommended_character_id"] == "lin"


# ── Pure-punctuation narration segment fixes ──────────────────────────────────


def test_merge_narration_continuations_folds_pure_dash_segment() -> None:
    """A narration segment containing only '——' must be merged into the previous."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="指尖无意掠过柜顶那道焦黑的痕迹",
                source_paragraph=2,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="——",
                source_paragraph=2,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="烟头烫的，苏晚留下的。",
                source_paragraph=2,
            ),
        ],
    )

    repaired, index_map, merge_count = GenerateDubbingScriptStep._merge_narration_continuations(
        script
    )

    assert merge_count >= 1
    # The pure-dash segment (index 1) must not survive as a standalone segment.
    for seg in repaired.segments:
        assert seg.text.strip() != "——", "pure-dash narration must be merged"
    # The dash should be absorbed into the previous segment's text.
    assert any("——" in seg.text and len(seg.text) > 2 for seg in repaired.segments)


def test_merge_narration_continuations_folds_ellipsis_segment() -> None:
    """A narration segment containing only '……' must be merged into adjacent."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="她沉默了很久",
                source_paragraph=5,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="……",
                source_paragraph=5,
            ),
        ],
    )

    repaired, _index_map, merge_count = GenerateDubbingScriptStep._merge_narration_continuations(
        script
    )

    assert merge_count == 1
    assert len(repaired.segments) == 1
    assert repaired.segments[0].text == "她沉默了很久……"


def test_review_merges_pure_punct_narration_as_defense_in_depth() -> None:
    """review_and_repair_dubbing_script must merge surviving pure-punct narration."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="窗外的风卷着远处码头的海腥味",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="——",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="和近处旧书页的霉味涌进来。",
                source_paragraph=3,
            ),
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    # The pure-dash segment must not survive.
    for seg in reviewed.segments:
        assert seg.text.strip() != "——", "pure-dash narration must be merged by review"
    # The review metadata should record the merge.
    review_meta = reviewed.metadata["professional_script_review"]
    assert review_meta["pure_punct_narration_merged"] >= 1


def test_review_merges_pure_punct_narration_forward_when_first_in_paragraph() -> None:
    """When pure-punct narration is first in paragraph, merge into next segment."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="——",
                source_paragraph=7,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="行李箱滚轮碾过老木地板的声响从楼梯口传来。",
                source_paragraph=7,
            ),
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    assert len(reviewed.segments) == 1
    assert "——" in reviewed.segments[0].text
    assert "行李箱" in reviewed.segments[0].text
    review_meta = reviewed.metadata["professional_script_review"]
    assert review_meta["pure_punct_narration_merged"] == 1


def test_review_does_not_merge_punct_across_paragraphs() -> None:
    """Pure-punct narration must NOT merge across paragraph boundaries."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="她转身离开了。",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="——",
                source_paragraph=4,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="门在身后合上。",
                source_paragraph=4,
            ),
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    # The dash in paragraph 4 should merge into segment 2 (same paragraph),
    # NOT into segment 0 (different paragraph).
    assert reviewed.segments[0].text == "她转身离开了。"
    # Segment 1 (dash) should be merged into segment 2 (same paragraph).
    merged_texts = [seg.text for seg in reviewed.segments]
    assert "她转身离开了。" in merged_texts
    # The dash must be absorbed into the paragraph-4 segment.
    assert any("——" in t and "门在身后" in t for t in merged_texts)


def test_merge_folds_four_em_dash_variant() -> None:
    """Four em-dashes '————' (not in old frozenset) must also be merged."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他停下了脚步",
                source_paragraph=2,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="————",
                source_paragraph=2,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="然后继续前行。",
                source_paragraph=2,
            ),
        ],
    )

    repaired, _index_map, merge_count = GenerateDubbingScriptStep._merge_narration_continuations(
        script
    )

    assert merge_count >= 1
    for seg in repaired.segments:
        assert seg.text.strip() != "————", "four-em-dash narration must be merged"
    assert any("————" in seg.text and len(seg.text) > 4 for seg in repaired.segments)


def test_review_converts_cross_paragraph_isolated_punct_to_silence() -> None:
    """Cross-paragraph isolated pure-punct narration must be converted to SILENCE."""
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="她转身离开了。",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="——————",
                source_paragraph=4,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="门在身后合上。",
                source_paragraph=5,
            ),
        ],
    )

    reviewed = review_and_repair_dubbing_script(script)

    # The isolated pure-punct segment (paragraph 4, no same-paragraph neighbor)
    # must be converted to SILENCE so the synthesis layer skips it.
    punct_seg = reviewed.segments[1]
    assert punct_seg.segment_type == SegmentType.SILENCE
    review_meta = reviewed.metadata["professional_script_review"]
    assert review_meta["pure_punct_narration_merged"] >= 1
