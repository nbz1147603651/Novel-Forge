"""Speaker attribution prior preservation and Bailian platform contract tests.

Covers the dubbing script generation flow fixes:
- P0: voice-profile alias indexing + fuzzy colon-turn attribution
- P1: adjudicator uncertainty preserves the generator's speaker prior
- P2: Bailian (DashScope) vocal-tag platform contract
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from novel_forge.core.config import Settings
from novel_forge.tts.pipeline.generate_script_step import (
    GenerateDubbingScriptStep,
    _QuoteReviewCandidate,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    ParalinguisticTag,
    SegmentType,
    VoiceCastEntry,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import unresolved_speaker_indices
from novel_forge.tts.script_review import apply_tts_platform_contract


def _step() -> GenerateDubbingScriptStep:
    step = GenerateDubbingScriptStep(object(), object(), settings=Settings())
    step._character_names = {"c1": "沈岸", "c2": "晚清深宅案客户"}
    return step


def _team() -> VoiceTeamContract:
    return VoiceTeamContract(
        entries=[
            VoiceCastEntry(character_id="c1", character_name="沈岸", voice_id="v1"),
            VoiceCastEntry(character_id="c2", character_name="晚清深宅案客户", voice_id="v2"),
        ]
    )


# ── P0: character map aliases + fuzzy colon attribution ──────────────────────


def test_build_character_map_indexes_voice_profile_aliases() -> None:
    step = _step()
    character_voices = [
        {"character": "晚清深宅案客户", "aliases": ["客户", "深宅客户"]},
        {"character": "未注册角色", "aliases": ["路人"]},
        {"character": "沈岸", "appellations": ["沈先生"]},
    ]

    aliases = step._build_character_map(_team(), character_voices)

    assert aliases["客户"] == "c2"
    assert aliases["深宅客户"] == "c2"
    assert aliases["晚清深宅案客户"] == "c2"
    assert aliases["沈先生"] == "c1"
    # Aliases of characters absent from the voice team are not indexed.
    assert "路人" not in aliases
    # Backward compatible: voice_team-only call still works.
    assert step._build_character_map(_team())["沈岸"] == "c1"


def test_explicit_speaker_evidence_fuzzy_matches_unique_short_form() -> None:
    step = _step()
    paragraph = "客户说：“账本在哪？”"
    quote_start = paragraph.index("“")
    quote_end = paragraph.rindex("”") + 1
    character_map = {"c2": "c2", "晚清深宅案客户": "c2"}

    character_id, reason = step._explicit_speaker_evidence(
        paragraph, quote_start, quote_end, character_map
    )

    assert character_id == "c2"
    assert reason == "fuzzy_colon_turn"


def test_explicit_speaker_evidence_fuzzy_handles_multi_char_speech_verbs() -> None:
    step = _step()
    paragraph = "客户沉声道：“账本在哪？”"
    quote_start = paragraph.index("“")
    quote_end = paragraph.rindex("”") + 1
    character_map = {"c2": "c2", "晚清深宅案客户": "c2"}

    character_id, reason = step._explicit_speaker_evidence(
        paragraph, quote_start, quote_end, character_map
    )

    assert character_id == "c2"
    assert reason == "fuzzy_colon_turn"


def test_explicit_speaker_evidence_fuzzy_rejects_ambiguous_short_form() -> None:
    step = _step()
    paragraph = "客户说：“账本在哪？”"
    quote_start = paragraph.index("“")
    quote_end = paragraph.rindex("”") + 1
    character_map = {
        "c2": "c2",
        "晚清深宅案客户": "c2",
        "c3": "c3",
        "民国当铺客户": "c3",
    }

    character_id, reason = step._explicit_speaker_evidence(
        paragraph, quote_start, quote_end, character_map
    )

    assert character_id == ""
    assert reason == ""


def test_explicit_speaker_evidence_exact_name_still_wins() -> None:
    step = _step()
    paragraph = "沈岸说：“账本在哪？”"
    quote_start = paragraph.index("“")
    quote_end = paragraph.rindex("”") + 1
    character_map = {"c1": "c1", "沈岸": "c1", "c2": "c2", "晚清深宅案客户": "c2"}

    character_id, reason = step._explicit_speaker_evidence(
        paragraph, quote_start, quote_end, character_map
    )

    assert character_id == "c1"
    assert reason == "named_colon_turn"


# ── P1: adjudicator uncertainty preserves the generator prior ────────────────


def _adjudication_fixture() -> tuple[DubbingScript, list[_QuoteReviewCandidate]]:
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(segment_index=0, segment_type=SegmentType.NARRATION, text="雨还在下。"),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="c2",
                character_name="晚清深宅案客户",
                text="账本在哪？",
            ),
            DubbingSegment(segment_index=2, segment_type=SegmentType.NARRATION, text="没有人应答。"),
        ],
    )
    candidates = [
        _QuoteReviewCandidate(
            candidate_id="p1_q0",
            segment_index=1,
            paragraph_index=1,
            quote_index=0,
            text="账本在哪？",
            context="客户说：“账本在哪？”",
            current_character_id="c2",
            role_status="ambiguous",
            role_reason="colon attribution uses a short form",
        )
    ]
    return script, candidates


def test_prior_assignment_confirmable_safety_constraints() -> None:
    confirmable = GenerateDubbingScriptStep._prior_assignment_is_confirmable
    allowed = {"c1", "c2"}

    # Empty prior or illegal cast member is never confirmable.
    assert not confirmable("q0", "", allowed, [])
    assert not confirmable("q0", "c9", allowed, [])

    # No rebuttal at all → keep the prior.
    assert confirmable("q0", "c2", allowed, [])
    # Low-confidence reassignment is not a rebuttal.
    assert confirmable(
        "q0",
        "c2",
        allowed,
        [{"candidate_id": "q0", "confidence": 0.6, "character_id": "c1", "verdict": "resolved"}],
    )
    # High-confidence reassignment to another legal character is a rebuttal.
    assert not confirmable(
        "q0",
        "c2",
        allowed,
        [{"candidate_id": "q0", "confidence": 0.9, "character_id": "c1", "verdict": "resolved"}],
    )
    # High-confidence reassignment to an illegal character is not a rebuttal.
    assert confirmable(
        "q0",
        "c2",
        allowed,
        [{"candidate_id": "q0", "confidence": 0.9, "character_id": "c9", "verdict": "resolved"}],
    )
    # Decisions about other candidates are irrelevant.
    assert confirmable(
        "q0",
        "c2",
        allowed,
        [{"candidate_id": "q1", "confidence": 0.9, "character_id": "c1", "verdict": "resolved"}],
    )


async def test_adjudication_keeps_prior_when_adjudicator_uncertain() -> None:
    step = _step()
    script, candidates = _adjudication_fixture()
    # Role verified, speaker uncertain (verdict=ambiguous): the adjudicator
    # could not verify the prior but produced no rebuttal either.
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": "p1_q0",
                    "segment_role": "dialogue",
                    "verdict": "ambiguous",
                    "character_id": "",
                    "confidence": 0.9,
                    "evidence": "客户说：“账本在哪？”",
                    "rationale": "局部上下文不足以核验说话人。",
                }
            ],
            "summary": "保留先验。",
        }
    )

    adjudicated = await step._adjudicate_ambiguous_quote_roles(script, candidates, _team())

    adjudication = adjudicated.metadata["speaker_adjudication"]
    assert adjudication["unresolved_segment_indices"] == []
    assert adjudication["prior_confirmed_segment_indices"] == [1]
    segment = adjudicated.segments[1]
    assert segment.segment_type == SegmentType.DIALOGUE
    assert segment.character_id == "c2"
    assert segment.character_name == "晚清深宅案客户"
    # Prior-confirmed segments must not trigger the manual review gate.
    assert not unresolved_speaker_indices(adjudicated)


async def test_adjudication_still_reviews_high_confidence_rebuttal() -> None:
    step = _step()
    script, candidates = _adjudication_fixture()
    # The adjudicator confidently names another legal character, but its
    # evidence is not source-anchored, so the decision cannot be accepted
    # directly; it still counts as a rebuttal and blocks prior preservation.
    step._call_with_retry = AsyncMock(
        return_value={
            "decisions": [
                {
                    "candidate_id": "p1_q0",
                    "segment_role": "dialogue",
                    "verdict": "resolved",
                    "character_id": "c1",
                    "confidence": 0.9,
                    "evidence": "无法在上下文中锚定的证据",
                    "rationale": "怀疑改判他人，但证据未锚定。",
                }
            ],
            "summary": "存在反证。",
        }
    )

    adjudicated = await step._adjudicate_ambiguous_quote_roles(script, candidates, _team())

    adjudication = adjudicated.metadata["speaker_adjudication"]
    assert adjudication["unresolved_segment_indices"] == [1]
    assert adjudication["prior_confirmed_segment_indices"] == []
    # The unresolved fallback keeps the original character for human review.
    assert adjudicated.segments[1].character_id == "c2"
    assert 1 in unresolved_speaker_indices(adjudicated)


# ── P2: Bailian (DashScope) platform contract ────────────────────────────────


def _bailian_script() -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                text="先别说话。",
                paralinguistic_tags=[
                    ParalinguisticTag(tag_type="laugh", position=0.1),
                    ParalinguisticTag(tag_type="breath", position=0.3),
                    ParalinguisticTag(tag_type="pause", position=0.5, duration_ms=300),
                    ParalinguisticTag(tag_type="sigh", position=0.8),
                ],
            )
        ],
    )


def test_dashscope_contract_derives_tags_from_adapter() -> None:
    from novel_forge.tts.gateway.adapters import dashscope_adapter
    from novel_forge.tts.platform.dashscope_contract import (
        DASHSCOPE_AIGC_TAG_MODELS,
        dashscope_supported_vocal_tags,
        is_bailian_provider,
    )

    assert DASHSCOPE_AIGC_TAG_MODELS == frozenset(dashscope_adapter._AIGC_TAG_MODELS)
    aigc_tags = dashscope_supported_vocal_tags("cosyvoice-v3-flash")
    assert {"laugh", "sigh", "breath", "cough", "cry", "whisper", "pause", "silence"} <= aigc_tags
    assert dashscope_supported_vocal_tags("qwen3-tts-flash") == frozenset({"pause", "silence"})
    assert is_bailian_provider("DashScope")
    assert is_bailian_provider("bailian")
    assert is_bailian_provider("qwen3")
    assert not is_bailian_provider("minimax")


def test_bailian_contract_keeps_performance_tags_on_aigc_models() -> None:
    result = apply_tts_platform_contract(
        _bailian_script(), provider_id="bailian", model_id="qwen-audio-3.0-tts-plus"
    )

    assert [tag.tag_type for tag in result.segments[0].paralinguistic_tags] == [
        "laugh",
        "breath",
        "pause",
        "sigh",
    ]
    contract = result.metadata["tts_platform_contract"]
    assert contract["platform"] == "bailian"
    assert contract["aigc_tag_model"] is True
    assert contract["interjections_supported"] is True
    assert contract["removed_unsupported_interjections"] == 0


def test_bailian_contract_strips_performance_tags_on_instruction_only_models() -> None:
    for model_id in ("qwen3-tts-flash", "cosyvoice-v3.5-plus"):
        result = apply_tts_platform_contract(
            _bailian_script(), provider_id="dashscope", model_id=model_id
        )

        assert [tag.tag_type for tag in result.segments[0].paralinguistic_tags] == ["pause"]
        contract = result.metadata["tts_platform_contract"]
        assert contract["platform"] == "bailian"
        assert contract["aigc_tag_model"] is False
        assert contract["interjections_supported"] is False
        assert contract["removed_unsupported_interjections"] == 3
        assert contract["supported_vocal_tags"] == ["pause", "silence"]
