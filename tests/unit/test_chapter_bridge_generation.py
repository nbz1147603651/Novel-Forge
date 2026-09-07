"""Tests for BridgeStep."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.bridge_step import BridgeInput, BridgeStep


def _empty_story_kernel_context() -> dict:
    return {"entities": [], "relationships": [], "timeline": []}


@pytest.mark.asyncio
async def test_bridge_step_generates_structured_bridge(router, builder) -> None:
    from novel_forge.core.config import Settings

    step = BridgeStep(router, builder, settings=Settings(_env_file=None))
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="第一天，黄昏",
            location="图书馆外",
            pov="林远",
            must_carry_forward=["林远必须进入图书馆"],
        ),
        must_carry_forward=["林远必须进入图书馆"],
    )

    bridge = await step.run(
        BridgeInput(
            chapter_state_packet=packet,
            story_kernel_context=_empty_story_kernel_context(),
        )
    )

    assert bridge.to_chapter == 2
    assert bridge.opening_location
    assert bridge.opening_pov == "林远"
    assert bridge.bridge_summary


def test_bridge_normalization_blocks_unexplained_custody_jump() -> None:
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="第一天，黄昏",
            location="县衙耳房",
            pov="林远",
            must_carry_forward=["林远仍被临时扣押"],
        ),
        previous_chapter_ending="衙役将林远押进耳房，门闩咔哒一声落下。",
        must_carry_forward=["林远仍被临时扣押"],
    )

    normalized, _ = BridgeStep._normalize_bridge_payload(
        {
            "opening_location": "坊市",
            "opening_pov": "林远",
            "action_handoff": "林远在坊市继续调查。",
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert normalized["opening_location"] == "县衙耳房"
    assert normalized["transition_mode"] == "direct_continue"


def test_bridge_normalization_preserves_declared_pov_switch() -> None:
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="怀表记忆",
            goal="切换至陆云峥POV",
            pov_character="陆云峥",
            setting="智云科技办公室",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="深夜",
            location="沈念卿公寓",
            pov="沈念卿",
            must_carry_forward=["陆云峥同样在深夜失眠"],
        ),
        previous_chapter_ending="沈念卿失眠，远处另一个人也望着夜色。",
        must_carry_forward=["陆云峥同样在深夜失眠"],
    )

    normalized, _ = BridgeStep._normalize_bridge_payload(
        {
            "opening_location": "智云科技办公室",
            "opening_pov": "陆云峥",
            "transition_mode": "pov_switch",
            "action_handoff": "浦东另一端的陆云峥同样握着怀表望向窗外夜色。",
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert normalized["opening_pov"] == "陆云峥"
    assert normalized["transition_mode"] == "pov_switch"


def test_bridge_normalization_compacts_overwritten_prose_fields() -> None:
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="第一天，黄昏",
            location="图书馆外",
            pov="林远",
            must_carry_forward=["林远必须进入图书馆"],
        ),
        must_carry_forward=["林远必须进入图书馆"],
    )

    normalized, _ = BridgeStep._normalize_bridge_payload(
        {
            "emotional_carryover": (
                "林远的肩线仍绷着，呼吸也发紧，整个人像被黄昏压在门外。"
                "他一边戒备一边盘算，每一步都拖着上一章留下的迟疑与不安。"
            ),
            "action_handoff": (
                "林远停在图书馆门外，回想上一章的纸条与怀表，"
                "随后推门而入，准备沿着裂缝继续深查。"
            ),
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert len(normalized["emotional_carryover"]) <= 72
    assert len(normalized["action_handoff"]) <= 100


def test_bridge_normalization_keeps_causal_link() -> None:
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=1,
            time_marker="第一天，黄昏",
            location="图书馆外",
            pov="林远",
            open_questions=["裂缝另一端是谁"],
            must_carry_forward=["林远必须进入图书馆"],
        ),
        must_carry_forward=["林远必须进入图书馆"],
    )

    normalized, _ = BridgeStep._normalize_bridge_payload(
        {
            "causal_link": {
                "previous_event": "林远在门外听见馆内异响。",
                "causal_mechanism": "异响逼迫他立刻入内查明真相。",
                "unresolved_question": "馆内异响是否与裂缝有关",
                "open_threads": ["异响来源", "守夜人的隐瞒"],
            }
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert normalized["causal_link"]["previous_event"] == "林远在门外听见馆内异响。"
    assert normalized["causal_link"]["open_threads"] == ["异响来源", "守夜人的隐瞒"]


def test_bridge_normalization_exempts_story_anchor_terms_from_forbidden() -> None:
    packet = ChapterStatePacket(
        chapter_number=5,
        chapter_outline=ChapterOutline(
            chapter_number=5,
            title="对质",
            goal="逼出供词中的缺口",
            pov_character="令昭",
            setting="西院偏厅",
            expected_word_count=2600,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=4,
            time_marker="夜半",
            location="西院偏厅",
            pov="令昭",
            must_carry_forward=["令昭必须把账簿残页交给崔令仪", "父亲的态度仍未明朗"],
        ),
        must_carry_forward=["令昭必须把账簿残页交给崔令仪", "父亲的态度仍未明朗"],
        known_characters=["令昭", "崔令仪"],
    )

    normalized, _ = BridgeStep._normalize_bridge_payload(
        {
            "action_handoff": "令昭把账簿残页递给崔令仪，准备回去见父亲。",
            "pending_questions": ["父亲为何迟迟不表态"],
            "forbidden_repetition": ["令昭", "父亲", "崔令仪", "账簿残页", "惨淡月光"],
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert normalized["forbidden_repetition"] == ["惨淡月光"]


def test_bridge_prepare_prompt_memory_hints_formats_structured_motifs() -> None:
    prepared = BridgeStep._prepare_prompt_memory_hints(
        {
            "motif_continuity": {
                "active_motifs": [{"motif": "纸灰", "category": "意象"}],
                "suggested_callbacks": [
                    {"motif": "焚书残页", "reason": "呼应上一章火盆中的纸灰"}
                ],
            },
            "other_hint": ["保留"],
        }
    )

    assert prepared["motif_continuity"]["active_motifs"] == ["纸灰（意象）"]
    assert prepared["motif_continuity"]["suggested_callbacks"] == [
        "焚书残页：呼应上一章火盆中的纸灰"
    ]
    assert prepared["other_hint"] == ["保留"]
