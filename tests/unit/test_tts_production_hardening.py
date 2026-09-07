"""Regression tests for the production TTS dataflow and recovery edges."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.assets.voice_library import VoiceLibrary
from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.pipeline.assemble_audio_step import AssembleAudioStep
from novel_forge.tts.pipeline.build_narrator_profile_step import (
    BuildNarratorProfileInput,
    BuildNarratorProfileStep,
)
from novel_forge.tts.pipeline.build_voice_team_step import BuildVoiceTeamStep, match_system_voice
from novel_forge.tts.pipeline.generate_script_step import (
    GenerateDubbingScriptInput,
    GenerateDubbingScriptStep,
)
from novel_forge.tts.pipeline.synthesize_audio_step import SynthesizeAudioStep
from novel_forge.tts.pipeline.timeline_builder import build_timeline
from novel_forge.tts.runtime.performance_policy import derive_voice_performance_profile
from novel_forge.tts.schemas import (
    AudioCreativeBible,
    BGMTiming,
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    NarratorVoiceProfile,
    SceneTransition,
    SegmentType,
    SFXCue,
    SoundscapeCue,
    SynthesisResult,
    SynthesisStatus,
    TTSProgressState,
    TTSProvider,
    TTSResponse,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    DubbingScriptFreshness,
    assess_dubbing_script_freshness,
    compute_dubbing_script_hash,
    compute_segment_uid,
    compute_source_text_hash,
    unresolved_speaker_indices,
)
from novel_forge.tts.script_stage_context import ScriptStageContext
from novel_forge.tts.services.automation import AudioAutomationMode
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.tts_ops.execution import (
    _activate_minimax_team_voices,
    _clear_activated_voice_deadlines,
    _load_tts_upstream_context,
    _lock_used_voice_identities,
    _merge_character_inputs,
    _repair_unresolved_speakers,
    _source_text_hash,
    _tts_project_lock,
    _voice_team_content_hash,
    execute_accept_segment_take,
    execute_build_narrator_profile,
    execute_confirm_voice_team,
    execute_design_character_voice,
    execute_full_tts_pipeline,
    execute_generate_dubbing_script,
    execute_prepare_voice_team_previews,
    execute_preview_character_voice,
    execute_reassemble_chapter_audio,
    execute_resolve_dubbing_speakers,
    execute_synthesize_chapter,
    execute_synthesize_segment,
    tts_artifact_source_mismatch,
    tts_audio_result_script_hash,
    tts_audio_result_script_mismatch,
    tts_audio_result_source_hash,
)


def _script_step() -> GenerateDubbingScriptStep:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    step._character_names = {"c1": "林远"}
    step._tts_metadata = {
        "scene_emotion_map": [
            {"scene_id": "s1", "dominant_emotion": "anxious"},
        ]
    }
    step._scene_intents = [
        {
            "scene_id": "s1",
            "location": "旧书房",
            "time_marker": "深夜",
            "emotional_beat": "紧张试探",
            "summary": "林远等待消息",
        }
    ]
    step._narrator_distance = "medium"
    return step


def test_llm_character_id_is_preserved_for_synthesis_lookup() -> None:
    step = _script_step()

    segment = step._parse_segment(
        {
            "segment_type": "dialogue",
            "character_id": "c1",
            "character_name": "林远",
            "text": "你来了。",
        },
        {"c1": "c1", "林远": "c1"},
        fallback_index=0,
    )

    assert segment.character_id == "c1"
    assert segment.character_name == "林远"


def test_script_parser_allows_only_bounded_short_spoken_text() -> None:
    step = _script_step()

    short = step._parse_segment(
        {
            "segment_type": "dialogue",
            "character_id": "c1",
            "character_name": "林远",
            "text": "坐。",
            "spoken_text": "坐下说吧。",
        },
        {"c1": "c1", "林远": "c1"},
    )
    long_line = step._parse_segment(
        {
            "segment_type": "dialogue",
            "character_id": "c1",
            "character_name": "林远",
            "text": "你先坐下慢慢说。",
            "spoken_text": "改掉原文。",
        },
        {"c1": "c1", "林远": "c1"},
    )
    short_phrase = step._parse_segment(
        {
            "segment_type": "dialogue",
            "character_id": "c1",
            "character_name": "林远",
            "text": "沈……沈先生？",
            "spoken_text": "沈……沈先生，是您吗？",
        },
        {"c1": "c1", "林远": "c1"},
    )

    assert short.text == "坐。"
    # Phase 4.1: spoken_text generation unified into independent rewrite step.
    # _validated_spoken_text now always returns ""; spoken_text is populated
    # later by rewrite_spoken_text.
    assert short.spoken_text == ""
    assert short_phrase.spoken_text == ""
    assert long_line.spoken_text == ""


def test_missing_soundscape_design_is_automatically_backfilled() -> None:
    step = _script_step()
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="旧书房里只剩钟声。",
                scene_context="旧书房｜深夜｜紧张试探",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="你来了。",
                scene_context="旧书房｜深夜｜紧张试探",
            ),
        ],
    )

    designed = step._ensure_soundscape_design(script)

    assert len(designed.soundscapes) == 1
    cue = designed.soundscapes[0]
    assert cue.start_segment_index == 0
    assert cue.end_segment_index == 1
    assert cue.name == "旧书房环境底床"
    assert cue.volume == 0.14
    assert designed.metadata["soundscape_design"] == {
        "mode": "automatic_backfill",
        "reason": "script_output_missing_soundscapes",
        "cue_count": 1,
    }


def test_rule_fallback_uses_only_high_confidence_direct_speech_structure() -> None:
    step = _script_step()
    step._character_names = {"c1": "沈岸"}
    character_map = {"c1": "c1", "沈岸": "c1"}
    visual_sentence = "沈岸掏出怀表，表盘上多了一个数字——一个淡红色的“1”，在表盘边缘微微闪烁。"

    assert step._extract_dialogues(visual_sentence, character_map) == []
    reply = step._extract_dialogues(
        "“坐。”",
        character_map,
        source_before="沈岸没有立刻回答。他把怀表揣入胸口内袋。",
    )
    assert [(item["text"], item["character_id"]) for item in reply] == [("坐。", "")]
    ambiguous_numeric_reply = step._extract_dialogues(
        "“1”",
        character_map,
        source_before="有人问沈岸还剩几次机会。",
    )
    assert ambiguous_numeric_reply == []


def test_source_fidelity_rejects_visual_number_misclassified_as_dialogue() -> None:
    step = _script_step()
    step._character_names = {"c1": "沈岸"}
    source = "沈岸掏出怀表，表盘上多了一个数字——一个淡红色的“1”，在表盘边缘微微闪烁。"
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="沈岸掏出怀表，表盘上多了一个数字——一个淡红色的",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="沈岸",
                text="1",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="，在表盘边缘微微闪烁。",
            ),
        ],
    )

    with pytest.raises(ValueError, match="narrative quotation"):
        step._validate_script_source_fidelity(script, source, {"c1": "c1", "沈岸": "c1"})


async def test_semantic_adjudication_folds_narrative_quotes_back_into_narration() -> None:
    step = _script_step()
    step._character_names = {"c1": "沈岸"}
    character_map = {"c1": "c1", "沈岸": "c1"}
    source = (
        "“好了。”沈岸把玻璃罐放回架子上，刻上“许”字，“你的噩梦今天就会结束。”\n"
        "沈岸掏出怀表，表盘上多了一个数字——淡红色的“1”，正在闪烁。"
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="好了。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="沈岸把玻璃罐放回架子上，刻上",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="许",
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.NARRATION,
                text="字，",
            ),
            DubbingSegment(
                segment_index=4,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="你的噩梦今天就会结束。",
            ),
            DubbingSegment(
                segment_index=5,
                segment_type=SegmentType.NARRATION,
                text="沈岸掏出怀表，表盘上多了一个数字——淡红色的",
            ),
            DubbingSegment(
                segment_index=6,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="1",
            ),
            DubbingSegment(
                segment_index=7,
                segment_type=SegmentType.NARRATION,
                text="，正在闪烁。",
            ),
        ],
    )

    reconciled, candidates = step._reconcile_script_with_source(
        script,
        source,
        character_map,
    )
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": "p0_q0",
                    "segment_role": "dialogue",
                    "verdict": "resolved",
                    "character_id": "c1",
                    "confidence": 0.96,
                    "evidence": "“好了。”沈岸把玻璃罐放回架子上",
                    "rationale": "当前叙事时刻的发声。",
                },
                {
                    "candidate_id": "p0_q1",
                    "segment_role": "narration",
                    "verdict": "unknown",
                    "character_id": "",
                    "confidence": 0.98,
                    "evidence": "刻上“许”字",
                    "rationale": "引号内文本未被发声。",
                },
                {
                    "candidate_id": "p0_q2",
                    "segment_role": "dialogue",
                    "verdict": "resolved",
                    "character_id": "c1",
                    "confidence": 0.95,
                    "evidence": "“你的噩梦今天就会结束。”",
                    "rationale": "当前叙事时刻的发声。",
                },
                {
                    "candidate_id": "p1_q0",
                    "segment_role": "narration",
                    "verdict": "unknown",
                    "character_id": "",
                    "confidence": 0.99,
                    "evidence": "淡红色的“1”",
                    "rationale": "引号内文本未被发声。",
                },
            ],
            "summary": "两条对白，两条叙述引用。",
        }
    )
    adjudicated = await step._adjudicate_ambiguous_quote_roles(
        reconciled,
        candidates,
        VoiceTeamContract(
            entries=[VoiceCastEntry(character_id="c1", character_name="沈岸", voice_id="voice")]
        ),
    )

    assert [segment.text for segment in adjudicated.dialogue_segments] == [
        "好了。",
        "你的噩梦今天就会结束。",
    ]
    assert [segment.text for segment in adjudicated.narration_segments] == [
        "沈岸把玻璃罐放回架子上，刻上许字，",
        "沈岸掏出怀表，表盘上多了一个数字——淡红色的1，正在闪烁。",
    ]
    assert adjudicated.metadata["source_reconciliation"]["semantic_quotes_reclassified"] == 2
    step._validate_script_source_fidelity(adjudicated, source, character_map)


async def test_semantic_adjudication_keeps_nominalized_memory_name_in_narration() -> None:
    step = _script_step()
    source = "他记得这个罐子，三年前装过一位退休教师的“童年夏夜蝉鸣”，报酬是一段中性记忆。"
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他记得这个罐子，三年前装过一位退休教师的",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text="童年夏夜蝉鸣",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="，报酬是一段中性记忆。",
            ),
        ],
    )

    reconciled, candidates = step._reconcile_script_with_source(
        script,
        source,
        {"c1": "c1", "林远": "c1"},
    )

    assert [candidate.text for candidate in candidates] == ["童年夏夜蝉鸣"]
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": "p0_q0",
                    "segment_role": "narration",
                    "verdict": "unknown",
                    "character_id": "",
                    "confidence": 0.98,
                    "evidence": "三年前装过一位退休教师的“童年夏夜蝉鸣”",
                    "rationale": "引号片段在当前叙事时刻没有被发声。",
                }
            ],
            "summary": "该片段是叙述引用。",
        }
    )
    adjudicated = await step._adjudicate_ambiguous_quote_roles(
        reconciled,
        candidates,
        VoiceTeamContract(
            entries=[VoiceCastEntry(character_id="c1", character_name="林远", voice_id="voice")]
        ),
    )

    assert adjudicated.dialogue_segments == []
    assert [segment.text for segment in adjudicated.narration_segments] == [
        "他记得这个罐子，三年前装过一位退休教师的童年夏夜蝉鸣，报酬是一段中性记忆。"
    ]
    step._validate_script_source_fidelity(
        adjudicated,
        source,
        {"c1": "c1", "林远": "c1"},
    )


async def test_reconciliation_extracts_dialogue_swallowed_inside_a_narrator_segment() -> None:
    """A whole-paragraph narrator result must not hide a character's quoted line."""
    step = _script_step()
    step._character_names = {"lin": "林小满", "su": "苏晚"}
    character_map = {
        "lin": "lin",
        "林小满": "lin",
        "su": "su",
        "苏晚": "su",
    }
    source = (
        "林小满把文件袋放在桌边，神情有些局促。"
        "“不是报酬，就是顺手买的。您看起来有点累。”"
        "苏晚没有立刻回答。"
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text=source,
                source_paragraph=0,
            )
        ],
    )

    reconciled, candidates = step._reconcile_script_with_source(script, source, character_map)

    assert [segment.segment_type for segment in reconciled.segments] == [
        SegmentType.NARRATION,
        SegmentType.DIALOGUE,
        SegmentType.NARRATION,
    ]
    assert [candidate.text for candidate in candidates] == [
        "不是报酬，就是顺手买的。您看起来有点累。"
    ]
    assert reconciled.metadata["source_reconciliation"]["embedded_quotes_extracted_for_review"] == 1
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": candidates[0].candidate_id,
                    "segment_role": "dialogue",
                    "verdict": "resolved",
                    "character_id": "lin",
                    "confidence": 0.99,
                    "evidence": "林小满把文件袋放在桌边，神情有些局促",
                    "rationale": "根据当前动作主体和后续苏晚的反应判断发话人。",
                }
            ],
            "summary": "已将被旁白吞并的引号台词归还林小满。",
        }
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="lin", character_name="林小满", voice_id="lin-voice"),
            VoiceCastEntry(character_id="su", character_name="苏晚", voice_id="su-voice"),
        ]
    )

    adjudicated = await step._adjudicate_ambiguous_quote_roles(reconciled, candidates, team)

    dialogue = adjudicated.dialogue_segments
    assert len(dialogue) == 1
    assert dialogue[0].character_id == "lin"
    assert dialogue[0].character_name == "林小满"
    assert "您看起来有点累" in dialogue[0].text
    assert "您看起来有点累" not in "".join(
        segment.text for segment in adjudicated.narration_segments
    )
    step._validate_script_source_fidelity(adjudicated, source, character_map)


def test_script_freshness_distinguishes_current_stale_and_legacy_outputs() -> None:
    current_text = "当前终稿正文。"
    current = DubbingScript(
        chapter_number=1,
        segments=[],
        source_text_hash=compute_source_text_hash(current_text),
    )
    stale = current.model_copy(
        update={"source_text_hash": compute_source_text_hash("旧终稿正文。")}
    )
    legacy = current.model_copy(update={"source_text_hash": ""})

    assert assess_dubbing_script_freshness(current, current_text) is DubbingScriptFreshness.CURRENT
    assert assess_dubbing_script_freshness(stale, current_text) is DubbingScriptFreshness.STALE
    assert assess_dubbing_script_freshness(legacy, current_text) is DubbingScriptFreshness.LEGACY
    assert assess_dubbing_script_freshness(current, "") is DubbingScriptFreshness.SOURCE_MISSING


def test_script_freshness_accepts_full_publication_hash() -> None:
    current_text = "终稿与发布账本使用同一正文。"
    script = DubbingScript(
        chapter_number=1,
        segments=[],
        source_text_hash=hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
    )

    assert assess_dubbing_script_freshness(script, current_text) is DubbingScriptFreshness.CURRENT


async def test_speaker_adjudicator_is_bounded_to_candidates_and_source_evidence() -> None:
    step = _script_step()
    step._character_names = {"lin": "林远", "shen": "沈岸"}
    character_map = {
        "lin": "lin",
        "林远": "lin",
        "shen": "shen",
        "沈岸": "shen",
    }
    source = "沈岸看向林远。\n“现在。”"
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="沈岸看向林远。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="现在。",
            ),
        ],
    )
    reconciled, candidates = step._reconcile_script_with_source(
        script,
        source,
        character_map,
    )
    assert [candidate.candidate_id for candidate in candidates] == ["p1_q0"]
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": "p1_q0",
                    "segment_role": "dialogue",
                    "verdict": "resolved",
                    "character_id": "shen",
                    "confidence": 0.93,
                    "evidence": "沈岸看向林远。",
                    "rationale": "前一段的行动主体继续发言。",
                }
            ],
            "summary": "一条对白已裁决。",
        }
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="lin", character_name="林远", voice_id="voice-lin"),
            VoiceCastEntry(character_id="shen", character_name="沈岸", voice_id="voice-shen"),
        ]
    )

    adjudicated = await step._adjudicate_ambiguous_quote_roles(reconciled, candidates, team)

    assert adjudicated.segments[1].character_id == "shen"
    assert "".join(segment.text for segment in adjudicated.segments) == "沈岸看向林远。现在。"
    assert adjudicated.metadata["speaker_adjudication"]["status"] == "passed"
    call = step._call_with_retry.await_args
    assert call.args[0].value == "tts_adjudicate_script_segments"
    assert "chapter_text" not in call.args[1]["stage_cards"]
    assert call.args[1]["stage_cards"]["candidates"][0]["text"] == "现在。"
    assert call.args[1]["stage_cards"]["candidates"][0]["paragraph_before"] == "沈岸看向林远。"
    assert call.args[1]["stage_cards"]["candidates"][0]["paragraph_text"] == "“现在。”"


def test_source_repair_merges_narration_split_after_a_leadin_colon() -> None:
    step = _script_step()
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他在签名旁边补充了一行小字：",
                source_paragraph=4,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="建议另行建档。",
                source_paragraph=4,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林远",
                text="我明白了。",
                source_paragraph=4,
            ),
        ],
        bgm_suggestions=[BGMTiming(start_segment_index=0, end_segment_index=1)],
        sfx_cues=[SFXCue(effect_name="笔尖划纸", trigger_segment_index=1)],
        soundscapes=[SoundscapeCue(name="办公室底噪", start_segment_index=0, end_segment_index=1)],
    )

    repaired, index_map, merge_count = step._merge_narration_continuations(script)

    assert merge_count == 1
    assert index_map == {0: 0, 1: 0, 2: 1}
    assert [segment.segment_index for segment in repaired.segments] == [0, 1]
    assert repaired.segments[0].text == "他在签名旁边补充了一行小字：建议另行建档。"
    assert repaired.bgm_suggestions[0].end_segment_index == 0
    assert repaired.sfx_cues[0].trigger_segment_index == 0
    assert repaired.soundscapes[0].end_segment_index == 0


async def test_speaker_adjudicator_retries_with_source_and_character_evidence() -> None:
    step = _script_step()
    step._character_names = {"shen": "沈岸"}
    step._speaker_adjudication_character_cards = [
        {
            "character_id": "shen",
            "character_name": "沈岸",
            "sentence_profile": "简短克制",
            "emotion_syntax": "少用感叹句",
            "signature_moves": ["沉默后开口"],
            "sample_lines": ["坐。"],
        }
    ]
    character_map = {"shen": "shen", "沈岸": "shen"}
    source = "沈岸没有立刻回答。他的目光落在门边。\n\n“坐。”"
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="沈岸没有立刻回答。他的目光落在门边。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="坐。",
            ),
        ],
    )
    reconciled, candidates = step._reconcile_script_with_source(script, source, character_map)
    step._call_with_retry = AsyncMock(
        side_effect=[
            {
                "decisions": [
                    {
                        "candidate_id": "p1_q0",
                        "segment_role": "ambiguous",
                        "verdict": "unknown",
                        "character_id": "",
                        "confidence": 0.5,
                        "evidence": "沈岸没有立刻回答。",
                    }
                ],
                "summary": "首轮证据不足。",
            },
            {
                "decisions": [
                    {
                        "candidate_id": "p1_q0",
                        "segment_role": "dialogue",
                        "verdict": "resolved",
                        "character_id": "shen",
                        "confidence": 0.93,
                        "evidence": "沈岸没有立刻回答。",
                        "rationale": "相邻段落主体延续且角色证据卡匹配。",
                    }
                ],
                "summary": "源证据修复完成。",
            },
        ]
    )
    team = VoiceTeamContract(
        entries=[VoiceCastEntry(character_id="shen", character_name="沈岸", voice_id="shen-voice")]
    )

    adjudicated = await step._adjudicate_ambiguous_quote_roles(reconciled, candidates, team)

    assert step._call_with_retry.await_count == 2
    assert adjudicated.segments[1].character_id == "shen"
    assert adjudicated.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []
    assert (
        adjudicated.metadata["speaker_adjudication"]["decisions"][-1]["adjudication_pass"]
        == "source_repair"
    )
    retry_cards = step._call_with_retry.await_args_list[1].args[1]["stage_cards"]
    assert retry_cards["adjudication_pass"] == "source_repair"
    assert retry_cards["candidates"][0]["attribution_window"][0]["character_id"] == "shen"
    assert retry_cards["character_evidence"][0]["sample_lines"] == ["坐。"]


async def test_professional_dubbing_review_uses_its_own_routable_task() -> None:
    step = _script_step()
    step._settings = Settings(
        _env_file=None,
        tts_script_llm_review_enabled=True,
        tts_script_llm_review_max_output_tokens=4096,
    )
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [],
            "reviewed_segment_count": 1,
            "overall_verdict": "passed",
            "summary": "配音表演计划通过。",
        }
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="你来了。",
            )
        ],
    )
    team = VoiceTeamContract(entries=[VoiceCastEntry(character_id="c1", character_name="林远")])

    reviewed = await step._review_dubbing_script_professionally(
        script,
        team,
        ScriptStageContext(
            character_voices=(
                {
                    "character_name": "林远",
                    "sentence_profile": "克制短句",
                },
            ),
            style_profile={"emotional_tone": "冷静"},
            narrator_profile=NarratorVoiceProfile(narration_distance="distant"),
        ),
    )

    call = step._call_with_retry.await_args
    assert call.args[0] is TaskType.TTS_REVIEW_DUBBING_SCRIPT
    assert call.args[1]["stage_cards"]["review_boundaries"]["humanize_reuse"] == (
        "candidate_signals_only"
    )
    assert call.args[1]["stage_cards"]["character_voices"] == [
        {"character_name": "林远", "sentence_profile": "克制短句"}
    ]
    assert call.args[1]["stage_cards"]["style_profile"] == {"emotional_tone": "冷静"}
    assert call.args[1]["stage_cards"]["narrator_profile"]["narration_distance"] == ("distant")
    assert reviewed.metadata["professional_script_review"]["llm_review"]["status"] == "passed"


def test_script_content_hash_ignores_timestamps_and_audit_wording() -> None:
    step = _script_step()
    segment = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        character_id="c1",
        text="现在。",
    )
    first = DubbingScript(
        chapter_number=1,
        segments=[segment],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={"speaker_adjudication": {"summary": "first wording"}},
    )
    second = DubbingScript(
        chapter_number=1,
        segments=[segment],
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        metadata={"speaker_adjudication": {"summary": "different wording"}},
    )
    changed_speaker = second.model_copy(
        update={
            "segments": [segment.model_copy(update={"character_id": "c2"})],
        }
    )

    assert step._compute_hash(first) == step._compute_hash(second)
    assert step._compute_hash(first) != step._compute_hash(changed_speaker)


async def test_synthesis_blocks_source_audit_speakers_until_manual_review(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "“现在。”"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    team = VoiceTeamContract(
        entries=[VoiceCastEntry(character_id="c1", character_name="林远", voice_id="voice-c1")]
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text="现在。",
            )
        ],
        source_text_hash=_source_text_hash(chapter_text),
        metadata={
            "source_reconciliation": {"status": "passed"},
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
            },
        },
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    chapter_result = await execute_synthesize_chapter(
        project_id="demo",
        chapter_number=1,
        settings=settings,
        layout=layout,
        # MANUAL mode skips the automated speaker-repair pass so the
        # unresolved-speaker gate itself is exercised deterministically.
        automation_mode=AudioAutomationMode.MANUAL,
    )
    # execute_synthesize_segment performs the unresolved-speaker gate directly
    # (no automated repair pass), so no automation_mode is needed here.
    segment_result = await execute_synthesize_segment(
        project_id="demo",
        chapter_number=1,
        segment_index=0,
        settings=settings,
        layout=layout,
    )

    assert chapter_result.result["error_code"] == "tts_speaker_review_required"
    assert chapter_result.result["segment_indices"] == [0]
    assert segment_result.result["error_code"] == "tts_speaker_review_required"


async def test_legacy_quote_bearing_script_must_regenerate_before_synthesis(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "表盘上显示“1”。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_voice_team_path.write_text(
        VoiceTeamContract(entries=[]).model_dump_json(),
        encoding="utf-8",
    )
    legacy = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="表盘上显示",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="1",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="。",
            ),
        ],
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(legacy.model_dump_json(), encoding="utf-8")

    result = await execute_synthesize_chapter(
        project_id="demo",
        chapter_number=1,
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
    )

    assert result.result["error_code"] == "tts_script_source_audit_required"


async def test_voice_room_candidate_is_isolated_until_explicit_acceptance(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "坐。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                speed_offset=0.05,
                pitch_offset=-1,
                performance_profile=derive_voice_performance_profile(
                    {
                        "tts_voice_hints": {
                            "preferred_speed": "fast",
                            "preferred_pitch": "low",
                        }
                    }
                ),
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text=chapter_text,
            )
        ],
        script_hash="script-v1",
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    formal_segment = layout.tts_segment_audio_path(1, 0)
    formal_segment.parent.mkdir(parents=True, exist_ok=True)
    formal_segment.write_bytes(b"formal-segment")
    master = layout.tts_assembled_audio_path(1)
    master.write_bytes(b"formal-master")
    subtitle = layout.tts_subtitle_path(1)
    subtitle.write_text("formal subtitle", encoding="utf-8")
    formal_result = ChapterAudioResult(
        chapter_number=1,
        script=script,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(formal_segment),
                provider=TTSProvider.MOCK,
            )
        ],
        assembled_audio_path=str(master),
        subtitle_path=str(subtitle),
        is_complete=True,
    )
    result_path = layout.tts_audio_result_path(1)
    result_path.write_text(formal_result.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    audition = await execute_synthesize_segment(
        project_id="demo",
        chapter_number=1,
        segment_index=0,
        settings=settings,
        layout=layout,
        provider="mock",
        segment_override=script.segments[0]
        .model_copy(update={"spoken_text": "坐下说吧。", "tone_hint": "克制"})
        .model_dump(mode="json"),
    )

    assert audition.result["requires_acceptance"] is True
    candidate_path = Path(audition.result["segment_result"]["audio_path"])
    assert candidate_path.is_file()
    assert "candidates" in audition.result["segment_result"]["audio_path"]
    assert formal_segment.read_bytes() == b"formal-segment"
    assert master.read_bytes() == b"formal-master"
    assert ChapterAudioResult.model_validate_json(
        result_path.read_text(encoding="utf-8")
    ).is_complete

    blocked = await execute_reassemble_chapter_audio(
        project_id="demo",
        chapter_number=1,
        settings=settings,
        layout=layout,
    )
    assert "待审试听版本" in blocked.result["error"]

    accepted = await execute_accept_segment_take(
        project_id="demo",
        chapter_number=1,
        take_id=str(audition.result["take_id"]),
        settings=settings,
        layout=layout,
    )
    promoted = ChapterAudioResult.model_validate(accepted.result["audio_result"])

    assert promoted.metadata["assembly_stale"] is True
    assert promoted.is_complete is False
    assert promoted.assembled_audio_path == str(master)
    assert Path(promoted.segment_results[0].audio_path).parent.name == "approved"
    assert not candidate_path.exists()
    assert (
        DubbingScript.model_validate_json(
            layout.tts_dubbing_script_path(1).read_text(encoding="utf-8")
        )
        .segments[0]
        .spoken_text
        == "坐下说吧。"
    )
    assert master.read_bytes() == b"formal-master"


async def test_fast_reassemble_refuses_without_a_persisted_timeline(tmp_path: Path) -> None:
    """The fast reassemble path reuses the persisted speech timeline.

    When no timeline has been persisted yet (e.g. a project that never ran the
    full pipeline), ``fast=True`` must fail fast with a clear, machine-readable
    error rather than silently falling back to the slow ASR alignment path.
    """
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "坐。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text=chapter_text,
            )
        ],
        script_hash="script-v1",
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    formal_segment = layout.tts_segment_audio_path(1, 0)
    formal_segment.parent.mkdir(parents=True, exist_ok=True)
    formal_segment.write_bytes(b"formal-segment")
    master = layout.tts_assembled_audio_path(1)
    master.write_bytes(b"formal-master")
    subtitle = layout.tts_subtitle_path(1)
    subtitle.write_text("formal subtitle", encoding="utf-8")
    formal_result = ChapterAudioResult(
        chapter_number=1,
        script=script,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(formal_segment),
                provider=TTSProvider.MOCK,
            )
        ],
        assembled_audio_path=str(master),
        subtitle_path=str(subtitle),
        is_complete=True,
    )
    layout.tts_audio_result_path(1).write_text(formal_result.model_dump_json(), encoding="utf-8")
    # An audio execution plan is required even on the fast path.
    from novel_forge.tts.platform.config import build_audio_execution_plan

    plan = build_audio_execution_plan(
        Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
    )
    layout.tts_execution_plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    # No speech timeline persisted -> fast path must refuse with a clear code.
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    result = await execute_reassemble_chapter_audio(
        project_id="demo",
        chapter_number=1,
        settings=settings,
        layout=layout,
        fast=True,
    )

    assert result.result.get("error_code") == "fast_reassemble_timeline_unavailable"


async def test_fast_reassemble_reuses_persisted_timeline_without_rerunning_asr(
    tmp_path: Path,
) -> None:
    """With ``fast=True`` and a persisted timeline, ASR alignment is skipped.

    The fast path must load the existing ``SpeechTimeline`` from disk instead of
    invoking ``AlignSpeechTimelineStep`` (which probes offline ASR sidecars).
    We assert this by patching the step's ``run`` to fail: if the fast path
    honours its contract it never calls it, so the reassemble proceeds.
    """
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "坐。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text=chapter_text,
            )
        ],
        script_hash="script-v1",
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    formal_segment = layout.tts_segment_audio_path(1, 0)
    formal_segment.parent.mkdir(parents=True, exist_ok=True)
    formal_segment.write_bytes(b"formal-segment")
    master = layout.tts_assembled_audio_path(1)
    master.write_bytes(b"formal-master")
    subtitle = layout.tts_subtitle_path(1)
    subtitle.write_text("formal subtitle", encoding="utf-8")
    formal_result = ChapterAudioResult(
        chapter_number=1,
        script=script,
        segment_results=[
            SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(formal_segment),
                provider=TTSProvider.MOCK,
            )
        ],
        assembled_audio_path=str(master),
        subtitle_path=str(subtitle),
        is_complete=True,
    )
    layout.tts_audio_result_path(1).write_text(formal_result.model_dump_json(), encoding="utf-8")
    from novel_forge.tts.platform.config import build_audio_execution_plan

    plan = build_audio_execution_plan(
        Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
    )
    layout.tts_execution_plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    # Persist a minimal speech timeline so the fast path has something to reuse.
    from novel_forge.tts.platform.schemas import SpeechTimeline, SpeechTimelineEntry

    timeline = SpeechTimeline(
        chapter_number=1,
        entries=[
            SpeechTimelineEntry(
                segment_index=0,
                text=chapter_text,
                start_ms=0,
                end_ms=400,
                alignment_status="segment_fallback",
            )
        ],
        total_duration_ms=400,
    )
    timeline_path = layout.tts_speech_timeline_path(1)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(timeline.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock")

    import novel_forge.workspace.tts_ops.execution_export as exec_tts

    asr_calls = {"count": 0}

    class _ExplodingAlignStep:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run(self, *args: object, **kwargs: object) -> object:
            asr_calls["count"] += 1
            raise AssertionError("fast reassemble must not re-run ASR alignment")

    original_step = exec_tts.AlignSpeechTimelineStep
    exec_tts.AlignSpeechTimelineStep = _ExplodingAlignStep  # type: ignore[assignment]
    try:
        result = await execute_reassemble_chapter_audio(
            project_id="demo",
            chapter_number=1,
            settings=settings,
            layout=layout,
            fast=True,
        )
    finally:
        exec_tts.AlignSpeechTimelineStep = original_step  # type: ignore[assignment]

    assert asr_calls["count"] == 0
    assert "error" not in result.result


def test_sound_cues_resolve_against_measured_segment_timeline() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="第一段",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="第二段",
                source_paragraph=0,
            ),
        ],
        bgm_suggestions=[BGMTiming(track_name="底乐", start_segment_index=0)],
        sfx_cues=[SFXCue(effect_name="敲门", trigger_segment_index=1, offset_ms=125)],
        soundscapes=[SoundscapeCue(name="雨声", start_segment_index=0)],
    )
    results = [
        SynthesisResult(
            segment_index=0,
            status=SynthesisStatus.COMPLETED,
            duration_ms=1_000,
        ),
        SynthesisResult(
            segment_index=1,
            status=SynthesisStatus.COMPLETED,
            duration_ms=2_000,
        ),
    ]
    step = AssembleAudioStep(settings=Settings())

    step._resolve_cue_timing(script, results)

    assert script.bgm_suggestions[0].start_ms == 0
    assert script.bgm_suggestions[0].end_ms == 3_400
    assert script.sfx_cues[0].trigger_ms == 1_525
    assert script.soundscapes[0].start_ms == 0
    assert script.soundscapes[0].end_ms == 3_400
    assert script.segments[0].start_ms == 0
    assert script.segments[0].end_ms == 1_000
    assert script.segments[1].start_ms == 1_400
    assert script.segments[1].end_ms == 3_400


def test_generated_ambience_is_extended_with_a_soft_crossfade() -> None:
    from pydub import AudioSegment

    source = AudioSegment.silent(duration=1_000)
    looped = AssembleAudioStep._fit_loop_with_crossfade(source, 3_250, crossfade_ms=80)

    assert len(looped) == 3_250


async def test_mp3_assembly_decodes_without_a_system_ffprobe(tmp_path: Path) -> None:
    """Bundled FFmpeg has no standalone ffprobe executable on desktop builds."""
    segment_path = tmp_path / "seg_0000.mp3"
    segment_path.write_bytes(MockTTSAdapter(latency_ms=0)._generate_mock_audio(24))  # noqa: SLF001
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="这是一段用于验证 MP3 装配的旁白。",
            )
        ],
    )
    synthesis = SynthesisResult(
        segment_index=0,
        audio_path=str(segment_path),
        status=SynthesisStatus.COMPLETED,
    )
    output_path = tmp_path / "chapter_full.mp3"
    step = AssembleAudioStep(settings=Settings(_env_file=None))

    duration_ms = await step._concatenate_pydub(  # noqa: SLF001
        [synthesis], output_path, script, {}
    )

    assert output_path.is_file()
    assert duration_ms > 0
    assert synthesis.duration_ms > 0


async def test_narrator_profile_uses_unified_llm_service_contract() -> None:
    step = BuildNarratorProfileStep(object(), object(), settings=Settings())
    step._call_with_retry = AsyncMock(
        return_value={
            "voice_type": "克制的中性旁白",
            "base_speed": 0.95,
            "emotional_range": "moderate",
            "narration_distance": "medium",
            "style_keywords": ["清晰", "克制"],
        }
    )

    profile = await step._build_profile_llm(BuildNarratorProfileInput(genre="悬疑", tone="冷峻"))

    assert profile is not None
    assert profile.voice_type == "克制的中性旁白"
    call = step._call_with_retry.await_args
    assert call.args[0].value == "tts_build_narrator_profile"
    assert call.args[1]["stage_cards"]["genre"] == "悬疑"
    assert call.kwargs["required_keys"] == (
        "voice_type",
        "base_speed",
        "emotional_range",
        "narration_distance",
        "style_keywords",
    )


async def test_character_voice_preview_is_reused_until_synthesis_inputs_change(
    tmp_path: Path, monkeypatch
) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    adapter = MockTTSAdapter(latency_ms=0)
    adapter.synthesize = AsyncMock(wraps=adapter.synthesize)

    class Registry:
        def get_adapter(self, provider):
            assert provider == TTSProvider.MOCK
            return adapter

    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: Registry()),
    )
    settings = Settings(tts_default_provider="mock")
    sample_text = "这是用于核对试听与正式配音参数完全一致的角色台词。"

    first = await execute_preview_character_voice(
        project_id="demo",
        character_id="c1",
        sample_text=sample_text,
        settings=settings,
        layout=layout,
        provider="mock",
    )
    second = await execute_preview_character_voice(
        project_id="demo",
        character_id="c1",
        sample_text=sample_text,
        settings=settings,
        layout=layout,
        provider="mock",
    )

    assert first.result["cached"] is False
    assert Path(first.result["audio_path"]).exists()
    assert second.result["cached"] is True
    assert second.result["audio_path"] == first.result["audio_path"]
    assert adapter.synthesize.await_count == 1
    preview_request = adapter.synthesize.await_args_list[0].args[0]
    formal_request = SynthesizeAudioStep(object(), settings=settings)._build_tts_request(
        DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            text=sample_text,
        ),
        {"c1": team.entries[0]},
        TTSProvider.MOCK,
    )
    assert (
        preview_request.speed,
        preview_request.pitch,
        preview_request.volume,
    ) == (
        formal_request.speed,
        formal_request.pitch,
        formal_request.volume,
    )
    assert first.result["effective_parameters"] == {
        "speed": formal_request.speed,
        "pitch": formal_request.pitch,
        "volume": formal_request.volume,
    }

    team.entries[0] = team.entries[0].model_copy(update={"voice_id": "mock-male-2"})
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    changed = await execute_preview_character_voice(
        project_id="demo",
        character_id="c1",
        sample_text=sample_text,
        settings=settings,
        layout=layout,
        provider="mock",
    )

    assert changed.result["cached"] is False
    assert changed.result["audio_path"] != first.result["audio_path"]
    assert adapter.synthesize.await_count == 2


async def test_character_preview_stops_waiting_for_an_active_auto_tts_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An interactive audition must report a busy project instead of hanging."""
    layout = ProjectLayout(tmp_path / "preview-lock")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    @asynccontextmanager
    async def _blocked_tts_lock(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.lock_manager.tts_project_lock", _blocked_tts_lock
    )

    with pytest.raises(TimeoutError, match="自动配音仍在处理"):
        await execute_preview_character_voice(
            project_id="preview-lock",
            character_id="c1",
            sample_text="这是一段不会开始合成的试听。",
            settings=Settings(
                _env_file=None,
                tts_default_provider="mock",
                tts_preview_lock_wait_timeout_s=0.1,
            ),
            layout=layout,
            provider="mock",
        )


async def test_character_preview_has_a_provider_response_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A provider request that never returns cannot leave the worker spinning."""
    layout = ProjectLayout(tmp_path / "preview-provider-timeout")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    async def _never_returns(_request):
        await asyncio.Event().wait()

    adapter = SimpleNamespace(synthesize=_never_returns)
    registry = SimpleNamespace(get_adapter=lambda _provider: adapter)
    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda _cls, _settings: registry),
    )

    with pytest.raises(TimeoutError, match="试听合成在 1 秒内"):
        await execute_preview_character_voice(
            project_id="preview-provider-timeout",
            character_id="c1",
            sample_text="这是一段等待平台超时的试听。",
            settings=Settings(
                _env_file=None,
                tts_default_provider="mock",
                tts_preview_synthesis_timeout_s=1.0,
            ),
            layout=layout,
            provider="mock",
        )


async def test_voice_team_build_prepares_auditions_for_each_character(
    tmp_path: Path, monkeypatch
) -> None:
    layout = ProjectLayout(tmp_path / "preview_team")
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="苏晚",
                voice_id="mock-female-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            ),
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    adapter = MockTTSAdapter(latency_ms=0)

    class Registry:
        def get_adapter(self, provider):
            assert provider == TTSProvider.MOCK
            return adapter

    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: Registry()),
    )
    events: list[str] = []
    result = await execute_prepare_voice_team_previews(
        project_id="preview_team",
        characters=[
            {"character_id": "c1", "name": "林远", "voice_sample": "线索还在这里。"},
            {"character_id": "c2", "name": "苏晚", "voice_sample": "别急，先听我说。"},
        ],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        on_step_progress=lambda event, data: events.append(event),
    )

    updated = VoiceTeamContract.model_validate(result.result)
    assert all(Path(entry.preview_audio_path).is_file() for entry in updated.entries)
    assert [entry.preview_text for entry in updated.entries] == [
        "线索还在这里。",
        "别急，先听我说。",
    ]
    assert events.count("voice_preview_ready") == 2
    assert events[-1] == "voice_preview_batch_done"


async def test_dubbing_script_uses_unified_llm_service_contract() -> None:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    step._character_names = {"c1": "林远"}
    step._call_with_retry = AsyncMock(
        return_value={
            "segments": [
                {
                    "segment_index": 0,
                    "segment_type": "narration",
                    "text": "林远说：",
                    "emotion": "neutral",
                },
                {
                    "segment_index": 1,
                    "segment_type": "dialogue",
                    "character_id": "c1",
                    "character_name": "林远",
                    "text": "线索在这里。",
                    "emotion": "determined",
                },
            ]
        }
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                clone_status=VoiceCloneStatus.READY,
            )
        ]
    )
    input_data = GenerateDubbingScriptInput(
        chapter_number=1,
        chapter_text="林远说：「线索在这里。」",
        voice_team=team,
    )

    script = await step._generate_script_llm(input_data, step._build_character_map(team))

    assert script is not None
    assert script.segments[1].character_id == "c1"
    call = step._call_with_retry.await_args
    assert call.args[0].value == "tts_generate_dubbing_script"
    assert call.args[1]["stage_cards"]["chapter_text"] == input_data.chapter_text
    assert call.kwargs["required_keys"] == ("segments",)


async def test_dubbing_script_generation_partitions_large_chapters_and_remaps_cues() -> None:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    chapter_text = f"{'甲' * 700}。\n{'乙' * 700}。\n{'丙' * 300}。"
    batches = step._partition_chapter_text(chapter_text)

    async def _generate(_task, context, **_kwargs):
        batch_text = context["stage_cards"]["chapter_text"]
        return {
            "segments": [{"segment_type": "narration", "text": batch_text}],
            "bgm_suggestions": [
                {"track_name": "低密度底乐", "start_segment_index": 0, "end_segment_index": 0}
            ],
            "sfx_cues": [{"effect_name": "页响", "trigger_segment_index": 0}],
            "soundscapes": [{"name": "室内底噪", "start_segment_index": 0, "end_segment_index": 0}],
            "scene_transitions": [],
        }

    step._call_with_retry = AsyncMock(side_effect=_generate)
    team = VoiceTeamContract(entries=[])
    script = await step._generate_script_llm(
        GenerateDubbingScriptInput(
            chapter_number=1,
            chapter_text=chapter_text,
            voice_team=team,
        ),
        {},
    )

    assert script is not None
    assert len(batches) == 2
    assert step._call_with_retry.await_count == 2
    step._validate_script_text_fidelity(script, chapter_text)
    assert [item.segment_index for item in script.segments] == [0, 1]
    assert [item.start_segment_index for item in script.bgm_suggestions] == [0, 1]
    assert [item.trigger_segment_index for item in script.sfx_cues] == [0, 1]
    assert [item.start_segment_index for item in script.soundscapes] == [0, 1]
    assert all(call.kwargs["max_tokens"] < 27852 for call in step._call_with_retry.await_args_list)


async def test_dubbing_script_batches_use_bounded_internal_concurrency() -> None:
    step = GenerateDubbingScriptStep(
        object(),
        object(),
        settings=Settings(_env_file=None, tts_script_max_concurrent_batches=2),
    )
    chapter_text = "\n".join(f"{character * 900}。" for character in "甲乙丙丁")
    active = 0
    peak = 0
    release = asyncio.Event()

    async def _generate(_task, context, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if peak == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "segments": [
                {
                    "segment_type": "narration",
                    "text": context["stage_cards"]["chapter_text"],
                }
            ]
        }

    step._call_with_retry = AsyncMock(side_effect=_generate)
    script = await step._generate_script_llm(
        GenerateDubbingScriptInput(
            chapter_number=1,
            chapter_text=chapter_text,
            voice_team=VoiceTeamContract(entries=[]),
        ),
        {},
    )

    assert script is not None
    assert step._call_with_retry.await_count == 4
    assert peak == 2
    step._validate_script_text_fidelity(script, chapter_text)


async def test_dubbing_script_generation_falls_back_per_batch_without_losing_chapter() -> None:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    chapter_text = f"{'甲' * 700}。\n{'乙' * 700}。\n{'丙' * 300}。"
    call_count = 0

    async def _generate(_task, context, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("simulated output truncation")
        return {
            "segments": [
                {
                    "segment_type": "narration",
                    "text": context["stage_cards"]["chapter_text"],
                }
            ],
            "bgm_suggestions": [],
            "sfx_cues": [],
            "soundscapes": [],
            "scene_transitions": [],
        }

    step._call_with_retry = AsyncMock(side_effect=_generate)
    script = await step._generate_script_llm(
        GenerateDubbingScriptInput(
            chapter_number=1,
            chapter_text=chapter_text,
            voice_team=VoiceTeamContract(entries=[]),
        ),
        {},
    )

    assert script is not None
    step._validate_script_text_fidelity(script, chapter_text)
    assert script.metadata["generation_batches"]["fallback_batch_indices"] == [1]
    assert step._call_with_retry.await_count == 2


def test_dubbing_script_prompt_projects_project_wide_context_to_current_chapter() -> None:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    signatures = {
        "醒梦事务所": ["晨间", "百叶窗"],
        **{f"无关地点{i}": ["全书未来场景", "不应进入本章"] for i in range(20)},
    }
    input_data = GenerateDubbingScriptInput(
        chapter_number=6,
        chapter_text="沈岸站在醒梦事务所窗前。",
        voice_team=VoiceTeamContract(
            entries=[
                VoiceCastEntry(
                    character_id="shen_an",
                    character_name="沈岸",
                    voice_id="voice-opaque-provider-id",
                ),
                VoiceCastEntry(
                    character_id="future_character",
                    character_name="未来角色",
                    voice_id="future-voice-id",
                ),
            ]
        ),
        scene_intents=[{"scene_id": "scene_01", "location": "醒梦事务所"}],
        audio_creative_bible=AudioCreativeBible(
            project_id="demo",
            location_sound_signatures=signatures,
        ),
    )

    cards = step._build_stage_cards(input_data, step._build_character_map(input_data.voice_team))

    assert cards["voice_team"]["entries"] == [
        {
            "character_id": "shen_an",
            "character_name": "沈岸",
            "gender": "",
            "age": "",
            "role": "",
            "voice_id": "voice-opaque-provider-id",
        },
        {
            "character_id": "future_character",
            "character_name": "未来角色",
            "gender": "",
            "age": "",
            "role": "",
            "voice_id": "future-voice-id",
        },
    ]
    assert cards["audio_creative_bible"]["location_sound_signatures"] == {
        "醒梦事务所": ["晨间", "百叶窗"]
    }
    rendered = PromptBuilder().render(
        TaskType.TTS_GENERATE_DUBBING_SCRIPT,
        {"stage_cards": cards},
    )
    assert "沈岸" in rendered
    assert "醒梦事务所" in rendered


async def test_tts_project_lock_is_reentrant_and_serializes_tasks(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    active = 0
    max_active = 0

    async def _work() -> None:
        nonlocal active, max_active
        async with _tts_project_lock("demo", layout):
            # Nested workspace calls in execute_full_tts_pipeline must not deadlock.
            async with _tts_project_lock("demo", layout):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

    await asyncio.wait_for(asyncio.gather(_work(), _work()), timeout=1.0)

    assert max_active == 1


async def test_rule_fallback_uses_scene_metadata_and_real_paragraph_boundaries() -> None:
    step = _script_step()

    script = await step._generate_script_rule_based(
        "旧书房里只剩钟声。\n林远说：「你来了。」",
        chapter_number=1,
        character_map={"c1": "c1", "林远": "c1"},
    )

    assert len(script.segments) == 3
    assert script.segments[0].scene_context == "旧书房｜深夜｜紧张试探"
    assert script.segments[0].emotion == EmotionTag.ANXIOUS
    assert script.segments[2].character_id == "c1"


async def test_invalid_llm_script_falls_back_without_losing_prose_or_dialogue(
    monkeypatch,
) -> None:
    """A malformed model split must never become a synthesizeable script."""
    step = _script_step()
    step._call_with_retry = AsyncMock(
        side_effect=[
            {
                "segments": [
                    {"segment_type": "narration", "text": "沈岸抬起头。"},
                    {
                        "segment_type": "dialogue",
                        "character_id": "qinghe",
                        "character_name": "许清禾",
                        "text": "你是……沈老板？",
                    },
                    # Mirrors the production failure: the rest of a direct quote
                    # is rendered as narration and its intervening prose vanished.
                    {"segment_type": "narration", "text": "我听说你能解梦。"},
                    {"segment_type": "narration", "text": "沈岸说：“坐。”"},
                ]
            },
            {
                "decisions": [
                    {
                        "candidate_id": "p1_q0",
                        "segment_role": "dialogue",
                        "verdict": "resolved",
                        "character_id": "qinghe",
                        "confidence": 0.96,
                        "evidence": "“你是……沈老板？”许清禾的声音发颤",
                        "rationale": "段落上下文支持当前发声与说话人。",
                    },
                    {
                        "candidate_id": "p1_q1",
                        "segment_role": "dialogue",
                        "verdict": "resolved",
                        "character_id": "qinghe",
                        "confidence": 0.96,
                        "evidence": "许清禾的声音发颤，“我听说你能解梦。”",
                        "rationale": "同段上下文支持当前发声与说话人。",
                    },
                ],
                "summary": "两处发声均由上下文证据裁决。",
            },
        ]
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="qinghe",
                character_name="许清禾",
                voice_id="mock-female-1",
                clone_status=VoiceCloneStatus.READY,
            ),
            VoiceCastEntry(
                character_id="shen_an",
                character_name="沈岸",
                voice_id="mock-male-1",
                clone_status=VoiceCloneStatus.READY,
            ),
        ]
    )
    source = (
        "沈岸抬起头。\n\n“你是……沈老板？”许清禾的声音发颤，“我听说你能解梦。”\n\n沈岸说：“坐。”"
    )

    async def _rewrite_with_source_text(script, *_args, **_kwargs):
        return script.model_copy(
            update={
                "segments": [
                    segment.model_copy(update={"spoken_text": segment.text})
                    for segment in script.segments
                ],
                "metadata": {
                    **script.metadata,
                    "spoken_text_rewrite": {"rejection_reasons": {}},
                },
            }
        )

    monkeypatch.setattr(
        "novel_forge.tts.spoken_text_rewrite.rewrite_spoken_text",
        _rewrite_with_source_text,
    )

    script = await step._execute(
        GenerateDubbingScriptInput(
            chapter_number=1,
            chapter_text=source,
            voice_team=team,
        )
    )

    assert step._normalized_source_text("".join(item.text for item in script.segments)) == (
        step._normalized_source_text(source)
    )
    assert [
        (item.character_id, item.text)
        for item in script.segments
        if item.segment_type == SegmentType.DIALOGUE
    ] == [
        ("qinghe", "你是……沈老板？"),
        ("qinghe", "我听说你能解梦。"),
        ("shen_an", "坐。"),
    ]
    assert any(item.text == "许清禾的声音发颤，" for item in script.narration_segments)


async def test_rule_fallback_does_not_guess_speaker_from_nearby_prose() -> None:
    step = _script_step()
    step._character_names = {"shen_an": "沈岸", "qinghe": "许清禾"}

    script = await step._generate_script_rule_based(
        "沈岸没有立刻回答。他的目光落在许清禾指缝间的灰上。\n\n“坐。”",
        chapter_number=1,
        character_map={
            "shen_an": "shen_an",
            "沈岸": "shen_an",
            "qinghe": "qinghe",
            "许清禾": "qinghe",
        },
    )

    dialogue = next(item for item in script.segments if item.segment_type == SegmentType.DIALOGUE)
    assert dialogue.character_id == ""
    assert dialogue.character_name == ""


def test_provider_voice_matching_uses_provider_catalog() -> None:
    voice_id = match_system_voice(
        {"gender": "male", "personality": "沉稳", "role": "supporting"},
        available_voices=[
            {"voice_id": "1004", "name": "智华", "gender": "male", "tags": ["沉稳"]},
            {"voice_id": "1003", "name": "智美", "gender": "female", "tags": ["甜美"]},
        ],
    )

    assert voice_id == "1004"


def test_clone_activation_deadline_is_cleared_without_changing_audio_hash() -> None:
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="cloned-c1",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
                voice_source="cloned",
                activation_deadline=datetime.now(timezone.utc) + timedelta(days=7),
            )
        ]
    )
    before = _voice_team_content_hash(team)

    changed = _clear_activated_voice_deadlines(team, {"cloned-c1"})

    assert changed is True
    assert team.entries[0].activation_deadline is None
    assert _voice_team_content_hash(team) == before


async def test_minimax_team_voice_is_activated_and_archived_locally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Adapter:
        async def synthesize(self, request):  # type: ignore[no-untyped-def]
            assert request.voice_id == "temporary-c1"
            assert request.metadata["voice_identity_lock"] is True
            return TTSResponse(
                audio_data=b"activation-audio",
                audio_format="mp3",
                voice_id=request.voice_id,
            )

    class Registry:
        def get_adapter(self, provider):  # type: ignore[no-untyped-def]
            assert provider == TTSProvider.MINIMAX
            return Adapter()

    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: Registry()),
    )
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="temporary-c1",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
                voice_source="designed",
                activation_deadline=datetime.now(timezone.utc) + timedelta(days=7),
            )
        ]
    )
    events: list[str] = []

    activated = await _activate_minimax_team_voices(
        team=team,
        settings=Settings(_env_file=None, storage_root=tmp_path),
        layout=layout,
        on_step_progress=lambda event, data: events.append(event),
    )

    assert activated == {"temporary-c1"}
    assert team.entries[0].activation_deadline is None
    assert "minimax_voice_activated" in events
    assert events[0] == "minimax_voice_activation_batch_start"
    assert "minimax_voice_activation_started" in events
    assert "minimax_voice_activation_progress" in events
    assert events[-1] == "minimax_voice_activation_batch_done"
    activation_files = list(layout.tts_audio_dir(0).glob("activation_temporary-c1.mp3"))
    assert activation_files[0].read_bytes() == b"activation-audio"


async def test_build_narrator_profile_constructs_and_binds_a_real_voice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    adapter = MockTTSAdapter(latency_ms=0)

    class Registry:
        def get_adapter(self, provider):
            assert provider == TTSProvider.MOCK
            return adapter

    async def _fake_profile_run(self, input_data):  # type: ignore[no-untyped-def]
        return NarratorVoiceProfile(
            provider=input_data.provider,
            voice_type="克制的中性旁白",
            base_speed=0.92,
            emotional_range="restrained",
            narration_distance="medium",
            style_keywords=["冷峻", "清晰", "留白"],
        )

    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: Registry()),
    )
    monkeypatch.setattr(BuildNarratorProfileStep, "run", _fake_profile_run)
    events: list[str] = []

    result = await execute_build_narrator_profile(
        project_id="demo",
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        on_step_progress=lambda event, data: events.append(event),
    )

    profile = NarratorVoiceProfile.model_validate(result.result)
    team = VoiceTeamContract.model_validate_json(
        layout.tts_voice_team_path.read_text(encoding="utf-8")
    )
    assert profile.voice_source == "designed"
    assert profile.voice_id.startswith("mock-designed-")
    assert profile.voice_design_prompt
    assert team.narrator_voice_id == profile.voice_id
    assert team.narrator_provider == TTSProvider.MOCK
    assert "narrator_voice_ready" in events


async def test_character_traits_prefer_provider_voice_design() -> None:
    settings = Settings()
    step = BuildVoiceTeamStep(object(), settings=settings)
    adapter = MockTTSAdapter(latency_ms=0)

    entry = await step._assign_voice_for_character(
        {
            "character_id": "c1",
            "name": "林远",
            "gender": "male",
            "age": "青年",
            "role": "protagonist",
            "personality": "沉稳克制",
            "voice": "短句，低沉，遇到危险时语速加快",
        },
        adapter,
        TTSProvider.MOCK,
        [],
        True,
    )

    assert entry.voice_source == "designed"
    assert entry.voice_design_prompt
    assert entry.voice_id.startswith("mock-designed-")


def test_structured_voice_hints_and_editorial_speech_profile_shape_voice() -> None:
    settings = Settings()
    step = BuildVoiceTeamStep(object(), settings=settings)
    speed, pitch, volume = step._compute_voice_offsets(
        {
            "character_id": "c1",
            "name": "林远",
            "tts_voice_hints": {
                "preferred_pitch": "high",
                "voice_texture": "低沉沙哑",
            },
        }
    )

    assert speed == 0.0
    # Voice texture belongs to voice casting; only the explicit structured
    # pitch hint contributes a small provider-neutral performance baseline.
    assert pitch == 1
    assert volume == 0.0

    merged = _merge_character_inputs(
        [{"character_id": "c1", "name": "林远", "voice_description": ""}],
        [{"character_id": "c1", "name": "林远", "personality": "克制"}],
        [
            {
                "character": "林远",
                "sentence_profile": "短句、少解释",
                "explanation_bias": "用事实替代情绪",
                "emotion_syntax": "情绪升高时停顿变长",
                "signature_moves": ["先沉默再回答"],
            }
        ],
    )

    assert "用事实替代情绪" in merged[0]["voice_description"]
    assert "先沉默再回答" in merged[0]["voice_description"]


def test_ready_voice_is_reused_when_only_performance_hints_change() -> None:
    step = BuildVoiceTeamStep(object(), settings=Settings())
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="voice-1",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
    )
    character = {"character_id": "c1", "name": "林远"}

    assert step._can_reuse_existing_entry(entry, character, TTSProvider.MOCK)
    assert not step._can_reuse_existing_entry(entry, character, TTSProvider.MINIMAX)
    changed_character = {
        **character,
        "tts_voice_hints": {"preferred_pitch": "high"},
    }
    assert step._can_reuse_existing_entry(
        entry,
        changed_character,
        TTSProvider.MOCK,
    )
    step._refresh_entry_performance_profile(entry, changed_character)
    assert entry.voice_id == "voice-1"
    assert entry.performance_profile is not None
    assert entry.performance_profile.automatic_baseline.pitch_offset == 1


def test_legacy_bailian_voice_without_model_binding_requires_rebuild() -> None:
    step = BuildVoiceTeamStep(object(), settings=Settings(_env_file=None))
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="longxiaochun",
        provider=TTSProvider.BAILIAN,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
    )
    character = {"character_id": "c1", "name": "林远"}

    assert not step._can_reuse_existing_entry(entry, character, TTSProvider.BAILIAN)

    entry.identity_locked = True
    with pytest.raises(RuntimeError, match="未记录绑定模型"):
        step._can_reuse_existing_entry(entry, character, TTSProvider.BAILIAN)


def test_formally_used_character_voice_is_reused_until_explicit_rebuild() -> None:
    step = BuildVoiceTeamStep(object(), settings=Settings())
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="minimax-character-1",
        provider=TTSProvider.MINIMAX,
        clone_status=VoiceCloneStatus.READY,
        voice_source="designed",
        voice_design_prompt="旧版角色简报",
        identity_locked=True,
    )

    assert step._can_reuse_existing_entry(
        entry,
        {
            "character_id": "c1",
            "name": "林远",
            "age": "中年",
            "personality": "上游画像已发生变化",
            "tts_voice_hints": {"preferred_pitch": "high"},
        },
        TTSProvider.MINIMAX,
    )
    with pytest.raises(RuntimeError, match="必须显式重建"):
        step._can_reuse_existing_entry(entry, {"name": "林远"}, TTSProvider.QWEN3)


def test_successful_formal_voice_usage_locks_cast_identity() -> None:
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="minimax-character-1",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="陈雪",
                voice_id="minimax-character-2",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
            ),
        ]
    )

    changed = _lock_used_voice_identities(team, {"minimax-character-1"})

    assert changed is True
    assert team.entries[0].identity_locked is True
    assert team.entries[0].identity_lock_reason == "first_formal_synthesis"
    assert team.entries[0].identity_locked_at is not None
    assert team.entries[1].identity_locked is False
    assert _lock_used_voice_identities(team, {"minimax-character-1"}) is False


def test_audio_result_freshness_supports_current_and_legacy_hash_locations(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "当前终稿"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    current_hash = _source_text_hash(chapter_text)

    assert (
        tts_audio_result_source_hash({"metadata": {"source_text_hash": current_hash}})
        == current_hash
    )
    assert (
        tts_audio_result_source_hash({"script": {"source_text_hash": current_hash}}) == current_hash
    )
    assert tts_artifact_source_mismatch(layout, 1, current_hash) is None
    mismatch = tts_artifact_source_mismatch(layout, 1, "old-hash")
    assert mismatch is not None
    assert mismatch["error_code"] == "stale_tts_artifact"


def test_audio_result_cannot_mix_with_another_script_for_the_same_final_text(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "同一份定稿正文"
    source_hash = _source_text_hash(chapter_text)
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    old_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="旧版分段方案",
            )
        ],
        source_text_hash=source_hash,
    )
    old_script.script_hash = compute_dubbing_script_hash(old_script)
    current_script = old_script.model_copy(
        update={
            "segments": [
                old_script.segments[0].model_copy(update={"text": "新版分段与说话人方案"})
            ],
            "script_hash": "",
        }
    )
    current_script.script_hash = compute_dubbing_script_hash(current_script)
    layout.tts_dubbing_script_path(1).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_dubbing_script_path(1).write_text(
        current_script.model_dump_json(),
        encoding="utf-8",
    )
    payload = ChapterAudioResult(
        chapter_number=1,
        script=old_script,
        metadata={
            "source_text_hash": source_hash,
            "script_hash": old_script.script_hash,
        },
    ).model_dump(mode="json")

    assert tts_artifact_source_mismatch(layout, 1, source_hash) is None
    assert tts_audio_result_script_hash(payload) == old_script.script_hash
    mismatch = tts_audio_result_script_mismatch(layout, 1, payload)
    assert mismatch is not None
    assert mismatch["error_code"] == "stale_tts_audio_result"
    assert mismatch["expected_script_hash"] == current_script.script_hash


def test_upstream_loader_reads_authoritative_editorial_contract_path(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.characters_path.write_text(
        '{"characters": [{"character_id": "c1", "name": "林远", "tts_voice_hints": {"preferred_speed": "slow"}}]}',
        encoding="utf-8",
    )
    layout.editorial_contract_path.parent.mkdir(parents=True, exist_ok=True)
    layout.editorial_contract_path.write_text(
        '{"character_voices": [{"character": "林远", "tts_voice_hints": {"preferred_pitch": "low"}}]}',
        encoding="utf-8",
    )
    layout.chapter_plan_path(3).write_text(
        '{"scene_intents": [{"scene_id": "s1", "location": "旧书房", "emotional_beat": "焦虑"}]}',
        encoding="utf-8",
    )
    metadata_path = layout.reports_dir / "chapter_003_tts_metadata.json"
    metadata_path.write_text(
        '{"scene_emotion_map": [{"scene_id": "s1", "dominant_emotion": "anxious"}]}',
        encoding="utf-8",
    )

    context = _load_tts_upstream_context(layout, 3)

    assert context["character_voices"][0]["character"] == "林远"
    assert context["scene_intents"][0]["scene_id"] == "s1"
    assert context["tts_metadata"] is not None


def test_upstream_loader_rejects_metadata_from_an_older_chapter_text(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.chapter_path(3).write_text("当前终稿", encoding="utf-8")
    metadata_path = layout.reports_dir / "chapter_003_tts_metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "chapter_number": 3,
                "source_text_hash": _source_text_hash("旧终稿"),
                "scene_emotion_map": [
                    {"scene_id": "stale", "dominant_emotion": "anxious"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context = _load_tts_upstream_context(layout, 3)

    assert context["tts_metadata"] is None


def test_timeline_applies_segment_transition_gap_to_following_segment() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="第一段",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="第二段",
                source_paragraph=1,
                transition=SceneTransition(
                    transition_type="location_change",
                    gap_ms=1200,
                ),
            ),
        ],
    )
    results = [
        SynthesisResult(
            segment_index=0,
            duration_ms=1000,
            status=SynthesisStatus.COMPLETED,
        ),
        SynthesisResult(
            segment_index=1,
            duration_ms=1000,
            status=SynthesisStatus.COMPLETED,
        ),
    ]

    timeline = build_timeline(script, results)

    assert timeline.entries[0].start_ms == 0
    assert timeline.entries[1].start_ms == 2200
    assert timeline.total_duration_ms == 3200


def test_timeline_uses_contextual_gaps_for_character_entries() -> None:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="门后忽然传来脚步声。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="lin",
                character_name="林夏",
                text="别开门。",
                emotion=EmotionTag.ANXIOUS,
                emotion_intensity=0.8,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.DIALOGUE,
                character_id="shen",
                character_name="沈舟",
                text="我知道。",
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.NARRATION,
                text="他没有再往前。",
            ),
        ],
    )
    results = [
        SynthesisResult(
            segment_index=index,
            duration_ms=1000,
            status=SynthesisStatus.COMPLETED,
        )
        for index in range(4)
    ]

    timeline = build_timeline(script, results)

    # 旁白 -> 高强度角色需要一个入场拍，不再硬接300ms。
    assert timeline.entries[1].start_ms == 1475
    # 两个短句角色快速接话，保持对话动量。
    assert timeline.entries[2].start_ms == 2595
    # 角色 -> 旁白留出叙事视角切换的呼吸。
    assert timeline.entries[3].start_ms == 4095


async def test_script_generation_rejects_non_authoritative_chapter_text(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("当前终稿", encoding="utf-8")

    result = await execute_generate_dubbing_script(
        project_id="demo",
        chapter_number=1,
        chapter_text="已经过期的页面文本",
        settings=Settings(),
        layout=layout,
    )

    assert result.result["error_code"] == "stale_chapter_text"


async def test_script_generation_builds_current_router_and_prompt_builder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from novel_forge.gateway.factory import ModelRouterBuilder

    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "当前终稿"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    team = VoiceTeamContract(entries=[])
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    router_sentinel = object()
    monkeypatch.setattr(ModelRouterBuilder, "build", lambda self: router_sentinel)

    async def _fake_run(self, input_data):
        assert self._router is router_sentinel
        assert input_data.audio_creative_bible is not None
        assert input_data.audio_creative_bible.project_id == "demo"
        return DubbingScript(
            chapter_number=input_data.chapter_number,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text=input_data.chapter_text,
                )
            ],
            source_text_hash=_source_text_hash(input_data.chapter_text),
            script_hash="current-script",
        )

    monkeypatch.setattr(GenerateDubbingScriptStep, "run", _fake_run)

    result = await execute_generate_dubbing_script(
        project_id="demo",
        chapter_number=1,
        chapter_text=chapter_text,
        settings=Settings(),
        layout=layout,
    )

    persisted = DubbingScript.model_validate(result.result)
    assert persisted.script_hash != "current-script"
    assert persisted.script_hash == compute_dubbing_script_hash(persisted)
    assert layout.tts_dubbing_script_path(1).exists()
    assert layout.tts_audio_creative_bible_path.exists()
    assert persisted.metadata["audio_creative_bible_path"] == str(
        layout.tts_audio_creative_bible_path
    )


async def test_script_generation_retargets_native_controls_to_frozen_tts_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Final script syntax must follow its frozen formal-TTS target, not settings."""
    from novel_forge.gateway.factory import ModelRouterBuilder
    from novel_forge.tts.pipeline.pause_marker_injection import inject_pause_markers
    from novel_forge.tts.platform.schemas import (
        AudioExecution,
        AudioExecutionPlan,
        AudioExecutionStage,
        AudioQualityPreset,
        AudioRouteTarget,
        AudioStageRoute,
    )
    from novel_forge.workspace.tts_ops import execution_script as execution_tts_module

    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "她终于笑了。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_voice_team_path.write_text(
        VoiceTeamContract(entries=[]).model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(ModelRouterBuilder, "build", lambda self: object())

    async def _fake_run(self, input_data):
        assert input_data.target_provider == "bailian"
        assert input_data.target_model == "qwen-audio-3.0-tts-plus"
        initial = DubbingScript(
            chapter_number=input_data.chapter_number,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text=input_data.chapter_text,
                    emotion=EmotionTag.HAPPY,
                    emotion_intensity=0.9,
                )
            ],
            source_text_hash=_source_text_hash(input_data.chapter_text),
        )
        return inject_pause_markers(initial, model_id="speech-2.8-hd")

    def _dashscope_formal_plan(settings, *_args, **_kwargs):
        assert settings.tts_default_provider == "bailian"
        return AudioExecutionPlan(
            preset=AudioQualityPreset.PRODUCTION,
            routes=[
                AudioStageRoute(
                    stage=AudioExecutionStage.TTS_FORMAL,
                    primary=AudioRouteTarget(
                        plugin_id="dashscope-formal",
                        provider_id="dashscope",
                        model_id="qwen-audio-3.0-tts-plus",
                        execution=AudioExecution.CLOUD_API,
                    ),
                )
            ],
        )

    monkeypatch.setattr(GenerateDubbingScriptStep, "run", _fake_run)
    monkeypatch.setattr(execution_tts_module, "build_audio_execution_plan", _dashscope_formal_plan)

    result = await execute_generate_dubbing_script(
        project_id="demo",
        chapter_number=1,
        chapter_text=chapter_text,
        settings=Settings(),
        layout=layout,
        provider="bailian",
    )

    persisted = DubbingScript.model_validate(result.result)
    assert "<#" not in persisted.segments[0].spoken_text
    assert "(laughs)" not in persisted.segments[0].spoken_text
    assert persisted.metadata["tts_native_synthesis_target"] == {
        "provider": "dashscope",
        "model_id": "qwen-audio-3.0-tts-plus",
        "execution_plan_id": "",
    }


async def test_script_regeneration_write_failure_preserves_the_previous_script(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from novel_forge.gateway.factory import ModelRouterBuilder
    from novel_forge.workspace.tts_ops import execution_script as execution_tts_module

    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "当前终稿"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_voice_team_path.write_text(
        VoiceTeamContract(entries=[]).model_dump_json(),
        encoding="utf-8",
    )
    script_path = layout.tts_dubbing_script_path(1)
    previous = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="可恢复的旧版脚本",
            )
        ],
        source_text_hash=_source_text_hash("旧终稿"),
    )
    script_path.parent.mkdir(parents=True, exist_ok=True)
    previous_bytes = previous.model_dump_json().encode("utf-8")
    script_path.write_bytes(previous_bytes)
    audio_path = layout.tts_audio_dir(1) / "segment_000.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"previous-audio")
    monkeypatch.setattr(ModelRouterBuilder, "build", lambda self: object())

    async def _fake_run(self, input_data):
        return DubbingScript(
            chapter_number=input_data.chapter_number,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text=input_data.chapter_text,
                )
            ],
            source_text_hash=_source_text_hash(input_data.chapter_text),
        )

    monkeypatch.setattr(GenerateDubbingScriptStep, "run", _fake_run)
    original_atomic_write = execution_tts_module.atomic_write_json

    def _fail_only_final_script(path, payload):
        if Path(path) == script_path:
            raise OSError("模拟磁盘写入失败")
        return original_atomic_write(path, payload)

    monkeypatch.setattr(execution_tts_module, "atomic_write_json", _fail_only_final_script)

    with pytest.raises(OSError, match="模拟磁盘写入失败"):
        await execute_generate_dubbing_script(
            project_id="demo",
            chapter_number=1,
            chapter_text=chapter_text,
            settings=Settings(),
            layout=layout,
        )

    assert script_path.read_bytes() == previous_bytes
    assert audio_path.read_bytes() == b"previous-audio"


async def test_speaker_repair_uses_durable_ids_and_clears_resolved_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("前文。\n\n“现在就走。”\n\n后文。", encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=10,
                segment_type=SegmentType.NARRATION,
                text="前文。",
            ),
            DubbingSegment(
                segment_index=30,
                segment_type=SegmentType.DIALOGUE,
                text="现在就走。",
            ),
            DubbingSegment(
                segment_index=50,
                segment_type=SegmentType.NARRATION,
                text="后文。",
            ),
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [30],
            },
            "professional_script_review": {
                "llm_review": {
                    "status": "needs_review",
                    "manual_review_segment_indices": [30],
                }
            },
        },
    )
    voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="voice-1",
                clone_status=VoiceCloneStatus.READY,
            )
        ]
    )
    captured_cards: list[dict[str, Any]] = []

    class _RepairService:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def call_with_retry(self, _task: Any, payload: dict[str, Any], **_kwargs: Any):
            captured_cards.extend(payload["stage_cards"]["candidates"])
            return {
                "decisions": [
                    {
                        "candidate_id": "repair_30",
                        "verdict": "resolved",
                        "character_id": "c1",
                        "segment_role": "dialogue",
                        "confidence": 0.95,
                    }
                ],
                "summary": "已根据上下文确认。",
            }

    monkeypatch.setattr(
        "novel_forge.gateway.factory.ModelRouterBuilder",
        lambda _settings: SimpleNamespace(build=lambda: object()),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.llm_service.LLMService",
        _RepairService,
    )

    repaired = await _repair_unresolved_speakers(
        script=script,
        unresolved_indices=(30,),
        voice_team=voice_team,
        layout=layout,
        chapter_number=1,
        settings=Settings(_env_file=None),
    )

    assert captured_cards[0]["segment_index"] == 30
    assert captured_cards[0]["previous_segments"][-1]["text"] == "前文。"
    assert captured_cards[0]["next_segments"][0]["text"] == "后文。"
    repaired_by_id = {segment.segment_index: segment for segment in repaired.segments}
    assert repaired_by_id[30].character_id == "c1"
    assert repaired_by_id[30].segment_uid == compute_segment_uid(repaired_by_id[30])
    assert unresolved_speaker_indices(repaired) == ()
    assert repaired.metadata["speaker_adjudication"]["unresolved_segment_indices"] == []
    review = repaired.metadata["professional_script_review"]["llm_review"]
    assert review["manual_review_segment_indices"] == []
    assert review["status"] == "passed"
    persisted = DubbingScript.model_validate_json(
        layout.tts_dubbing_script_path(1).read_text(encoding="utf-8")
    )
    assert persisted.script_hash == repaired.script_hash


async def test_speaker_repair_does_not_return_unpersisted_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from novel_forge.workspace.tts_ops import execution_script as execution_tts_module

    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=12,
                segment_type=SegmentType.DIALOGUE,
                text="是谁？",
            )
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [12],
            }
        },
    )
    voice_team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="voice-1",
                clone_status=VoiceCloneStatus.READY,
            )
        ]
    )

    class _RepairService:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def call_with_retry(self, _task: Any, _payload: Any, **_kwargs: Any):
            return {
                "decisions": [
                    {
                        "candidate_id": "repair_12",
                        "verdict": "resolved",
                        "character_id": "c1",
                        "segment_role": "dialogue",
                        "confidence": 0.95,
                    }
                ],
                "summary": "已确认。",
            }

    monkeypatch.setattr(
        "novel_forge.gateway.factory.ModelRouterBuilder",
        lambda _settings: SimpleNamespace(build=lambda: object()),
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.llm_service.LLMService",
        _RepairService,
    )

    def _fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(
        execution_tts_module,
        "atomic_write_json",
        _fail_write,
    )

    result = await _repair_unresolved_speakers(
        script=script,
        unresolved_indices=(12,),
        voice_team=voice_team,
        layout=layout,
        chapter_number=1,
        settings=Settings(_env_file=None),
    )

    assert result is script
    assert unresolved_speaker_indices(result) == (12,)


async def test_human_speaker_resolution_persists_by_id_and_invalidates_old_audio(
    tmp_path: Path,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "“现在就走。”"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_voice_team_path.write_text(
        VoiceTeamContract(
            entries=[
                VoiceCastEntry(
                    character_id="c1",
                    character_name="林远",
                    voice_id="voice-1",
                    clone_status=VoiceCloneStatus.READY,
                )
            ]
        ).model_dump_json(),
        encoding="utf-8",
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=30,
                segment_type=SegmentType.DIALOGUE,
                text="现在就走。",
            )
        ],
        source_text_hash=compute_source_text_hash(chapter_text),
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [30],
            },
            "professional_script_review": {
                "llm_review": {
                    "status": "needs_review",
                    "manual_review_segment_indices": [30],
                }
            },
        },
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")
    audio_path = layout.tts_assembled_audio_path(1)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"old-audio")

    result = await execute_resolve_dubbing_speakers(
        project_id="demo",
        chapter_number=1,
        resolutions=[
            {
                "segment_index": 30,
                "character_id": "c1",
                "segment_type": "dialogue",
            }
        ],
        layout=layout,
    )

    assert "error" not in result.result
    assert result.result["resolved_segment_indices"] == [30]
    persisted = DubbingScript.model_validate_json(
        script_path.read_text(encoding="utf-8")
    )
    assert persisted.segments[0].character_id == "c1"
    assert persisted.segments[0].character_name == "林远"
    assert unresolved_speaker_indices(persisted) == ()
    assert persisted.metadata["speaker_adjudication"]["status"] == "passed"
    assert persisted.metadata["professional_script_review"]["llm_review"]["status"] == "passed"
    assert not audio_path.exists()


async def test_synthesis_rejects_script_from_old_final_text(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("当前终稿", encoding="utf-8")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="voice-1",
                clone_status=VoiceCloneStatus.READY,
            )
        ]
    )
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="旧稿旁白",
            )
        ],
        source_text_hash=_source_text_hash("旧终稿"),
    )
    script_path = layout.tts_dubbing_script_path(1)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script.model_dump_json(), encoding="utf-8")

    result = await execute_synthesize_chapter(
        project_id="demo",
        chapter_number=1,
        settings=Settings(),
        layout=layout,
        provider="mock",
    )

    assert result.result["error_code"] == "stale_dubbing_script"


async def test_synthesis_persists_partial_checkpoint_and_reuses_it_on_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The facade must preserve completed segments when a later one fails."""
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "当前终稿"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    voice_team = VoiceTeamContract(entries=[], default_provider=TTSProvider.MOCK)
    layout.tts_voice_team_path.write_text(voice_team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="已完成旁白",
                spoken_text="已完成旁白",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="需要续跑的旁白",
                spoken_text="需要续跑的旁白",
            ),
        ],
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    received_resume_inputs: list[tuple[list[int], dict[str, str]]] = []

    async def _fake_synthesize(self, input_data):
        received_resume_inputs.append(
            (
                list(input_data.completed_segments or []),
                dict(input_data.completed_segment_hashes or {}),
            )
        )
        return [
            SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(layout.tts_audio_dir(1) / "seg_0000.mp3"),
                request_hash="segment-0-v1",
            ),
            SynthesisResult(
                segment_index=1,
                status=SynthesisStatus.FAILED,
                error_message="provider timeout",
            ),
        ]

    async def _fake_assemble(self, input_data):
        return ChapterAudioResult(
            chapter_number=input_data.chapter_number,
            script=input_data.script,
            segment_results=input_data.segment_results,
            is_complete=False,
        )

    monkeypatch.setattr(SynthesizeAudioStep, "run", _fake_synthesize)
    monkeypatch.setattr(AssembleAudioStep, "run", _fake_assemble)
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        tts_default_provider="mock",
        audio_preflight_blocking=False,
    )

    first = await execute_synthesize_chapter(
        project_id="demo",
        chapter_number=1,
        settings=settings,
        layout=layout,
        provider="mock",
    )
    checkpoint_path = layout.tts_progress_path_for_chapter(1)
    checkpoint = TTSProgressState.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))

    assert first.result["is_complete"] is False
    assert checkpoint_path.exists()
    assert not layout.tts_progress_path.exists()
    assert checkpoint.completed_segments == [0]
    assert checkpoint.completed_segment_hashes == {"0": "segment-0-v1"}
    assert checkpoint.failed_segments == [1]
    assert checkpoint.failed_segment_errors == {"1": "provider timeout"}

    second = await execute_synthesize_chapter(
        project_id="demo",
        chapter_number=1,
        settings=settings,
        layout=layout,
        provider="mock",
    )

    assert second.result["is_complete"] is False
    assert received_resume_inputs == [([], {}), ([0], {"0": "segment-0-v1"})]


async def test_full_tts_retry_reuses_valid_cast_and_script(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The one-click flow must repair partial audio without rerunning its prerequisites."""
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "当前终稿"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    narrator = NarratorVoiceProfile(voice_id="mock-narrator", provider=TTSProvider.MOCK)
    layout.tts_narrator_profile_path.write_text(narrator.model_dump_json(), encoding="utf-8")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text="只续跑缺失片段。",
            )
        ],
        source_text_hash=_source_text_hash(chapter_text),
    )
    layout.tts_dubbing_script_path(1).write_text(script.model_dump_json(), encoding="utf-8")
    unexpected_calls: list[str] = []
    synthesis_calls: list[int] = []
    events: list[str] = []

    async def _unexpected_team_or_script(*_args, **_kwargs):
        unexpected_calls.append("prerequisite")
        raise AssertionError("valid prerequisite was regenerated")

    async def _fake_synthesize(**kwargs):
        synthesis_calls.append(int(kwargs["chapter_number"]))
        return ExecutionResult(
            project_id=kwargs["project_id"],
            result={"is_complete": False, "resumed": True},
        )

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_build_voice_team",
        _unexpected_team_or_script,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_generate_dubbing_script",
        _unexpected_team_or_script,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_synthesize_chapter",
        _fake_synthesize,
    )

    result = await execute_full_tts_pipeline(
        project_id="demo",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
        on_step_progress=lambda step, _data: events.append(step),
    )

    assert result.result == {"is_complete": False, "resumed": True}
    assert unexpected_calls == []
    assert synthesis_calls == [1]
    assert "tts_voice_team_reused" in events
    assert "tts_script_reused" in events


async def test_full_tts_rebuilds_a_current_script_with_unresolved_speakers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Automatic runs repair an old blocked script instead of reusing it."""
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    chapter_text = "“坐。”"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_narrator_profile_path.write_text(
        NarratorVoiceProfile(voice_id="mock-narrator", provider=TTSProvider.MOCK).model_dump_json(),
        encoding="utf-8",
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    blocked_script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                text="坐。",
            )
        ],
        source_text_hash=_source_text_hash(chapter_text),
        metadata={
            "source_reconciliation": {"status": "passed"},
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [0],
            },
        },
    )
    layout.tts_dubbing_script_path(1).write_text(blocked_script.model_dump_json(), encoding="utf-8")
    generation_calls: list[int] = []
    events: list[str] = []

    async def _regenerate_script(**kwargs):
        generation_calls.append(int(kwargs["chapter_number"]))
        return ExecutionResult(project_id=kwargs["project_id"], result={"regenerated": True})

    async def _fake_synthesize(**kwargs):
        return ExecutionResult(project_id=kwargs["project_id"], result={"is_complete": True})

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_generate_dubbing_script",
        _regenerate_script,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_synthesize_chapter",
        _fake_synthesize,
    )

    result = await execute_full_tts_pipeline(
        project_id="demo",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
        on_step_progress=lambda step, _data: events.append(step),
    )

    assert result.result == {"is_complete": True}
    assert generation_calls == [1]
    assert "tts_script_reused" not in events


async def test_background_tts_requires_manual_voice_team_rebuild(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Post-archive work may reuse voices, but must never assign new ones."""
    layout = ProjectLayout(tmp_path / "background-cast")
    layout.ensure_dirs()
    chapter_text = "这是已经归档的章节终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_narrator_profile_path.write_text(
        NarratorVoiceProfile(
            voice_id="mock-narrator",
            provider=TTSProvider.MOCK,
        ).model_dump_json(),
        encoding="utf-8",
    )
    build_calls: list[str] = []

    async def _unexpected_build(*_args, **_kwargs):
        build_calls.append("build")
        raise AssertionError("background TTS must not rebuild a voice team")

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_build_voice_team",
        _unexpected_build,
    )

    result = await execute_full_tts_pipeline(
        project_id="background-cast",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    assert result.result["error_code"] == "tts_voice_team_manual_rebuild_required"
    assert result.result["missing_artifact"] == "voice_team"
    assert build_calls == []
    assert not layout.tts_voice_team_path.exists()


async def test_background_tts_requires_manual_narrator_rebuild(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Background work cannot silently replace the established narrator either."""
    layout = ProjectLayout(tmp_path / "background-narrator")
    layout.ensure_dirs()
    chapter_text = "这是已经归档的章节终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    build_calls: list[str] = []

    async def _unexpected_build(*_args, **_kwargs):
        build_calls.append("build")
        raise AssertionError("background TTS must not rebuild the narrator")

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_build_narrator_profile",
        _unexpected_build,
    )

    result = await execute_full_tts_pipeline(
        project_id="background-narrator",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    assert result.result["error_code"] == "tts_voice_team_manual_rebuild_required"
    assert result.result["missing_artifact"] == "narrator_voice_profile"
    assert build_calls == []
    assert not layout.tts_narrator_profile_path.exists()


async def test_full_tts_pipeline_supports_narrator_only_final_chapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """One-click dubbing must not require a character bible for prose-only chapters."""

    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    chapter_text = "雨落在空无一人的长街上。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    layout.tts_narrator_profile_path.write_text(
        NarratorVoiceProfile(
            voice_id="mock-narrator",
            provider=TTSProvider.MOCK,
        ).model_dump_json(),
        encoding="utf-8",
    )
    events: list[str] = []
    calls: list[str] = []

    async def _fake_script(**_kwargs):
        calls.append("script")
        return ExecutionResult(project_id="demo", result={"segments": []})

    async def _fake_synthesize(**_kwargs):
        calls.append("synthesize")
        return ExecutionResult(project_id="demo", result={"is_complete": False})

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_generate_dubbing_script",
        _fake_script,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_synthesize_chapter",
        _fake_synthesize,
    )

    result = await execute_full_tts_pipeline(
        project_id="demo",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        on_step_progress=lambda step, _data: events.append(step),
    )

    team = VoiceTeamContract.model_validate_json(
        layout.tts_voice_team_path.read_text(encoding="utf-8")
    )
    assert result.result == {"is_complete": False}
    assert team.entries == []
    assert team.narrator_voice_id == "mock-narrator"
    assert calls == ["script", "synthesize"]
    assert "tts_voice_team_narrator_only" in events


async def test_manual_voice_design_remains_anchored_to_upstream_traits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    layout.characters_path.write_text(
        '{"characters": [{"character_id": "c1", "name": "林远", '
        '"age": "青年", "role": "主角", "personality": "沉稳克制", '
        '"voice": "短句，低沉"}]}',
        encoding="utf-8",
    )
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    adapter = MockTTSAdapter(latency_ms=0)

    class Registry:
        def get_adapter(self, provider):
            assert provider == TTSProvider.MOCK
            return adapter

    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: Registry()),
    )

    result = await execute_design_character_voice(
        project_id="demo",
        character_id="c1",
        description="制作人希望尾音更克制",
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
    )

    entry = VoiceTeamContract.model_validate(result.result).get_entry("c1")
    assert entry is not None
    assert entry.voice_source == "designed"
    assert "性格：沉稳克制" in entry.voice_design_prompt
    assert "说话习惯：短句，低沉" in entry.voice_design_prompt
    assert "不得覆盖上述角色特质" in entry.voice_design_prompt
    preview_path = layout.tts_audio_dir(0) / "design_c1.mp3"
    assert preview_path.exists()
    assert result.result["preview_audio_path"] == str(preview_path)
    library = VoiceLibrary.load(tmp_path)
    assert len(library.entries) == 1
    assert library.entries[0].voice_id == entry.voice_id


# ─── WP1: structured voice-team reuse diagnoses ──────────────────────────────
# Post-archive TTS failure must tell the author *which* voice to confirm,
# expire, or switch instead of one opaque string.  Each scenario asserts the
# diagnoses list carries the right per-character reason.


def _write_reusable_narrator(layout: ProjectLayout) -> None:
    """Write a minimally valid narrator profile so team reuse is the only gate."""
    layout.tts_narrator_profile_path.write_text(
        NarratorVoiceProfile(
            voice_id="mock-narrator",
            provider=TTSProvider.MOCK,
        ).model_dump_json(),
        encoding="utf-8",
    )


async def test_background_tts_failure_diagnoses_pending_approval(tmp_path: Path) -> None:
    """A pending (un-auditioned) voice surfaces as pending_approval in diagnoses."""
    layout = ProjectLayout(tmp_path / "pending")
    layout.ensure_dirs()
    chapter_text = "归档终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                approval_status="pending",  # ← blocks reuse
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    result = await execute_full_tts_pipeline(
        project_id="pending",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    assert result.result["error_code"] == "tts_voice_team_manual_rebuild_required"
    diagnoses = result.result["diagnoses"]
    reasons = {d["reason"] for d in diagnoses}
    assert "pending_approval" in reasons
    pending = [d for d in diagnoses if d["reason"] == "pending_approval"][0]
    assert pending["character_id"] == "c1"
    assert pending["character_name"] == "林远"
    assert "c1" in result.result["confirmable_character_ids"]


async def test_background_tts_failure_diagnoses_missing_character(tmp_path: Path) -> None:
    """A character present in the chapter but absent from the team is 'missing'."""
    layout = ProjectLayout(tmp_path / "missing")
    layout.ensure_dirs()
    chapter_text = "归档终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    # Chapter expects c1 (present) AND c2 (absent from team).
    result = await execute_full_tts_pipeline(
        project_id="missing",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[
            {"character_id": "c1", "name": "林远"},
            {"character_id": "c2", "name": "苏晚"},
        ],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    diagnoses = result.result["diagnoses"]
    missing = [d for d in diagnoses if d["reason"] == "missing"]
    assert len(missing) == 1
    assert missing[0]["character_id"] == "c2"
    # c1 is fully ready, must NOT appear in diagnoses.
    assert not any(d["character_id"] == "c1" for d in diagnoses)


async def test_background_tts_failure_diagnoses_expired_voice(tmp_path: Path) -> None:
    """An expired designed voice surfaces as 'expired' so the author can redesign."""
    layout = ProjectLayout(tmp_path / "expired")
    layout.ensure_dirs()
    chapter_text = "归档终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    _write_reusable_narrator(layout)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                expires_at=past,  # ← expired
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    result = await execute_full_tts_pipeline(
        project_id="expired",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    diagnoses = result.result["diagnoses"]
    reasons = {d["reason"] for d in diagnoses}
    assert "expired" in reasons


async def test_background_tts_failure_diagnoses_provider_mismatch(tmp_path: Path) -> None:
    """An entry on a different provider surfaces as 'provider_mismatch'."""
    layout = ProjectLayout(tmp_path / "mismatch")
    layout.ensure_dirs()
    chapter_text = "归档终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="minimax-c1",
                provider=TTSProvider.MINIMAX,  # ← mismatch with resolved mock
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,  # team default still mock
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    result = await execute_full_tts_pipeline(
        project_id="mismatch",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )

    diagnoses = result.result["diagnoses"]
    reasons = {d["reason"] for d in diagnoses}
    assert "provider_mismatch" in reasons


# ─── WP2: team-level confirmed flag ──────────────────────────────────────────


async def test_confirmed_team_short_circuits_reuse_check(tmp_path: Path) -> None:
    """A confirmed team is reused even if per-entry checks would theoretically
    run -- confirmed=True is a trusted author decision."""
    layout = ProjectLayout(tmp_path / "confirmed-shortcut")
    layout.ensure_dirs()
    chapter_text = "归档终稿。"
    layout.chapter_path(1).write_text(chapter_text, encoding="utf-8")
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
        confirmed=True,  # ← key signal
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    # Monkey-patch to verify synthesis is reached (team+script reused).
    synthesis_calls: list[int] = []

    async def _fake_synthesize(**kwargs) -> Any:
        synthesis_calls.append(int(kwargs["chapter_number"]))
        return ExecutionResult(
            project_id=kwargs["project_id"],
            result={"is_complete": True, "resumed": False},
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.execute_synthesize_chapter",
        _fake_synthesize,
    )

    await execute_full_tts_pipeline(
        project_id="confirmed-shortcut",
        chapter_number=1,
        chapter_text=chapter_text,
        characters=[{"character_id": "c1", "name": "林远"}],
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
        provider="mock",
        allow_voice_team_rebuild=False,
    )
    monkeypatch.undo()
    assert synthesis_calls == [1], "confirmed team should reach synthesis"


async def test_entry_change_invalidates_confirmation(tmp_path: Path) -> None:
    """After _replace_voice_entry touches any entry, confirmed must be False."""
    layout = ProjectLayout(tmp_path / "invalidate")
    layout.ensure_dirs()
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
        confirmed=True,
        confirmed_at=datetime.now(timezone.utc),
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    # Simulate a voice design/clone that calls _replace_voice_entry.
    from novel_forge.workspace.tts_ops.execution import _replace_voice_entry

    _replace_voice_entry(team, "c1", approval_status="pending")
    assert team.confirmed is False
    assert team.confirmed_at is None


async def test_confirm_team_with_pending_entry_returns_diagnoses(tmp_path: Path) -> None:
    """When any entry is pending, confirm_voice_team returns structured diagnoses."""
    layout = ProjectLayout(tmp_path / "confirm-blocked")
    layout.ensure_dirs()
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                approval_status="pending",
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    result = await execute_confirm_voice_team(
        project_id="confirm-blocked",
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
    )
    assert result.result["error_code"] == "tts_voice_team_confirm_blocked"
    assert any(d["reason"] == "pending_approval" for d in result.result["diagnoses"])
    assert "c1" in result.result["confirmable_character_ids"]


async def test_confirm_team_persists_confirmed_flag(tmp_path: Path) -> None:
    """A successful confirm writes confirmed=True back to voice_team.json."""
    layout = ProjectLayout(tmp_path / "confirm-persist")
    layout.ensure_dirs()
    _write_reusable_narrator(layout)
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-c1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
            )
        ],
        narrator_voice_id="mock-narrator",
        narrator_provider=TTSProvider.MOCK,
        default_provider=TTSProvider.MOCK,
    )
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")

    result = await execute_confirm_voice_team(
        project_id="confirm-persist",
        settings=Settings(_env_file=None, storage_root=tmp_path, tts_default_provider="mock"),
        layout=layout,
    )
    assert "error" not in result.result

    # Re-read from disk and verify confirmed flag persisted.
    persisted = VoiceTeamContract.model_validate_json(
        layout.tts_voice_team_path.read_text(encoding="utf-8")
    )
    assert persisted.confirmed is True
    assert persisted.confirmed_at is not None


# ─── P0: Dialogue coherence gradient ─────────────────────────────────────────


class TestDialogueCoherenceGradient:
    """Verify cross-segment emotion gradient in SynthesizeAudioStep."""

    def _make_step(self) -> SynthesizeAudioStep:
        from novel_forge.tts.gateway.factory import TTSAdapterRegistry

        registry = TTSAdapterRegistry.get_instance(Settings(_env_file=None))
        return SynthesizeAudioStep(
            registry,
            settings=Settings(_env_file=None),
        )

    def _make_segment(
        self,
        index: int,
        *,
        text: str = "这是一段测试文本",
        character_id: str = "c1",
        segment_type: SegmentType = SegmentType.DIALOGUE,
        emotion: EmotionTag = EmotionTag.NEUTRAL,
        emotion_intensity: float = 0.5,
    ) -> DubbingSegment:
        return DubbingSegment(
            segment_index=index,
            segment_type=segment_type,
            character_id=character_id,
            character_name="林远" if character_id == "c1" else "苏婉",
            text=text,
            emotion=emotion,
            emotion_intensity=emotion_intensity,
            source_paragraph=0,
        )

    def test_same_speaker_emotion_change_produces_gradient(self) -> None:
        """Same speaker changing emotion gets dampened speed/vol delta."""
        step = self._make_step()
        prev = self._make_segment(0, emotion=EmotionTag.ANGRY, emotion_intensity=0.8)
        curr = self._make_segment(1, emotion=EmotionTag.NEUTRAL, emotion_intensity=0.3)
        step._current_context_segments = [prev, curr]

        speed_delta, vol_delta, reason = step._dialogue_coherence_adjustment(
            curr,
            is_character_voice=True,
            is_short=False,
            is_extreme_short=False,
        )
        assert "same_speaker_emotion_gradient" in reason
        # The gradient should produce a non-zero adjustment.
        assert speed_delta != 0.0 or vol_delta != 0.0

    def test_speaker_switch_after_high_intensity_settles(self) -> None:
        """Switching speakers after high-intensity line applies settling."""
        step = self._make_step()
        prev = self._make_segment(
            0, character_id="c1", emotion=EmotionTag.ANGRY, emotion_intensity=0.9
        )
        curr = self._make_segment(
            1, character_id="c2", emotion=EmotionTag.NEUTRAL, emotion_intensity=0.3
        )
        step._current_context_segments = [prev, curr]

        speed_delta, vol_delta, reason = step._dialogue_coherence_adjustment(
            curr,
            is_character_voice=True,
            is_short=False,
            is_extreme_short=False,
        )
        assert "speaker_switch_settling" in reason
        assert speed_delta < 0  # Settling reduces speed slightly.

    def test_narration_segments_not_affected(self) -> None:
        """Narration segments do not trigger coherence adjustment."""
        step = self._make_step()
        prev = self._make_segment(0, segment_type=SegmentType.NARRATION, character_id="")
        curr = self._make_segment(1, segment_type=SegmentType.NARRATION, character_id="")
        step._current_context_segments = [prev, curr]

        speed_delta, vol_delta, reason = step._dialogue_coherence_adjustment(
            curr,
            is_character_voice=False,
            is_short=False,
            is_extreme_short=False,
        )
        assert speed_delta == 0.0
        assert vol_delta == 0.0
        assert reason == ""

    def test_extreme_short_not_affected(self) -> None:
        """Extreme-short utterances skip coherence adjustment."""
        step = self._make_step()
        prev = self._make_segment(0, emotion=EmotionTag.ANGRY, emotion_intensity=0.9)
        curr = self._make_segment(1, text="嗯", emotion=EmotionTag.NEUTRAL)
        step._current_context_segments = [prev, curr]

        speed_delta, vol_delta, reason = step._dialogue_coherence_adjustment(
            curr,
            is_character_voice=True,
            is_short=True,
            is_extreme_short=True,
        )
        assert speed_delta == 0.0
        assert reason == ""


# ─── P1: Context-aware segment gaps ──────────────────────────────────────────


class TestContextualGap:
    """Verify dynamic gap computation in AssembleAudioStep."""

    def _make_step(self) -> AssembleAudioStep:
        return AssembleAudioStep(settings=Settings(_env_file=None))

    def _seg(
        self,
        seg_type: SegmentType,
        char_id: str = "",
        text: str = "这是一段足够长的测试文本用于验证间隔逻辑是否正确触发",
    ) -> DubbingSegment:
        return DubbingSegment(
            segment_index=0,
            segment_type=seg_type,
            character_id=char_id,
            text=text,
            source_paragraph=0,
        )

    def test_same_speaker_gap(self) -> None:
        step = self._make_step()
        prev = self._seg(SegmentType.DIALOGUE, "c1")
        curr = self._seg(SegmentType.DIALOGUE, "c1")
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.SAME_SPEAKER_GAP_MS

    def test_speaker_switch_gap(self) -> None:
        step = self._make_step()
        prev = self._seg(SegmentType.DIALOGUE, "c1")
        curr = self._seg(SegmentType.DIALOGUE, "c2")
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.SPEAKER_SWITCH_GAP_MS

    def test_rapid_exchange_gap(self) -> None:
        """Short dialogue lines with different speakers get tighter gap (P5)."""
        step = self._make_step()
        prev = self._seg(SegmentType.DIALOGUE, "c1", text="你怎么看？")
        curr = self._seg(SegmentType.DIALOGUE, "c2", text="我不知道。")
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.RAPID_EXCHANGE_GAP_MS

    def test_narration_to_dialogue_gap(self) -> None:
        step = self._make_step()
        prev = self._seg(SegmentType.NARRATION)
        curr = self._seg(SegmentType.DIALOGUE, "c1")
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.NARRATION_TO_DIALOGUE_GAP_MS

    def test_dialogue_to_narration_gap(self) -> None:
        step = self._make_step()
        prev = self._seg(SegmentType.DIALOGUE, "c1")
        curr = self._seg(SegmentType.NARRATION)
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.DIALOGUE_TO_NARRATION_GAP_MS

    def test_narration_to_narration_default(self) -> None:
        step = self._make_step()
        prev = self._seg(SegmentType.NARRATION)
        curr = self._seg(SegmentType.NARRATION)
        assert step._contextual_gap_ms(prev, curr) == AssembleAudioStep.DEFAULT_SEGMENT_GAP_MS


# ─── P2: Single-char dialogue fold ───────────────────────────────────────────


class TestFoldSingleCharDialogue:
    """Verify single-character dialogue segments are folded into neighbors."""

    def _make_script(self, segments: list[DubbingSegment]) -> DubbingScript:
        return DubbingScript(chapter_number=1, segments=segments)

    def test_single_char_dialogue_folded_into_previous(self) -> None:
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        segments = [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                character_id="",
                character_name="",
                text="他沉默了一会儿。",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="",  # Unattributed single-char artifact
                character_name="",
                text="嗯",
                source_paragraph=0,
            ),
        ]
        script = self._make_script(segments)
        result, count = GenerateDubbingScriptStep._fold_single_char_dialogue(script)
        assert count == 1
        assert len(result.segments) == 1
        assert "嗯" in result.segments[0].text
        assert "他沉默了一会儿" in result.segments[0].text

    def test_attributed_single_char_preserved(self) -> None:
        """Single-char dialogue with an assigned speaker is intentional."""
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        segments = [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                character_id="",
                character_name="",
                text="沈岸说：",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="shen_an",  # Attributed → intentional
                character_name="沈岸",
                text="坐。",
                source_paragraph=0,
            ),
        ]
        script = self._make_script(segments)
        result, count = GenerateDubbingScriptStep._fold_single_char_dialogue(script)
        assert count == 0
        assert len(result.segments) == 2

    def test_single_char_with_paralinguistic_preserved(self) -> None:
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep
        from novel_forge.tts.schemas import ParalinguisticTag

        segments = [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="他沉默了一会儿。",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text="嗯",
                source_paragraph=0,
                paralinguistic_tags=[ParalinguisticTag(tag_type="hum", position=0.0)],
            ),
        ]
        script = self._make_script(segments)
        result, count = GenerateDubbingScriptStep._fold_single_char_dialogue(script)
        # Segment with paralinguistic_tags is preserved (artistic intent).
        assert count == 0
        assert len(result.segments) == 2

    def test_multi_char_dialogue_not_folded(self) -> None:
        from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep

        segments = [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="林远",
                text="好的",
                source_paragraph=0,
            ),
        ]
        script = self._make_script(segments)
        result, count = GenerateDubbingScriptStep._fold_single_char_dialogue(script)
        assert count == 0
        assert len(result.segments) == 1


# ─── P2b: Script review single-char neutralization ───────────────────────────


class TestScriptReviewSingleCharGuard:
    """Verify script_review neutralizes emotion on surviving single-char segments."""

    def test_single_char_emotion_neutralized(self) -> None:
        from novel_forge.tts.script_review import review_and_repair_dubbing_script

        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c1",
                    character_name="林远",
                    text="嗯",
                    emotion=EmotionTag.ANGRY,
                    emotion_intensity=0.8,
                    source_paragraph=0,
                ),
            ],
        )
        result = review_and_repair_dubbing_script(script)
        seg = result.segments[0]
        assert seg.emotion == EmotionTag.NEUTRAL
        assert seg.emotion_intensity == 0.0
        review_meta = result.metadata.get("professional_script_review", {})
        assert review_meta.get("single_char_neutralized", 0) == 1

    def test_multi_char_emotion_preserved(self) -> None:
        from novel_forge.tts.script_review import review_and_repair_dubbing_script

        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c1",
                    character_name="林远",
                    text="你为什么要这样做？",
                    emotion=EmotionTag.ANGRY,
                    emotion_intensity=0.8,
                    source_paragraph=0,
                ),
            ],
        )
        result = review_and_repair_dubbing_script(script)
        seg = result.segments[0]
        # Multi-char segment keeps its emotion (only numeric overrides stripped).
        assert seg.emotion == EmotionTag.ANGRY
