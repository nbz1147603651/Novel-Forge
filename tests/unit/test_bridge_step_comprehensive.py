"""Tests for BridgeStep — happy path, error path, and boundary conditions."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterExitState, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.bridge_step import BridgeInput, BridgeStep
from tests.unit.conftest import _MockRouter


class _FakeBuilder:
    def __init__(self) -> None:
        self.last_context: dict | None = None

    def build(
        self,
        task_type,
        context: dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        top_p: float | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        prior_messages: list | None = None,
    ) -> ModelRequest:
        self.last_context = context
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_packet(
    chapter_number: int = 2,
    previous_exit: ChapterExitState | None = None,
) -> ChapterStatePacket:
    return ChapterStatePacket(
        chapter_number=chapter_number,
        chapter_outline=ChapterOutline(
            chapter_number=chapter_number,
            goal="探索新地点",
            setting="废弃图书馆",
            pov_character="林远",
        ),
        previous_exit_state=previous_exit,
        previous_chapter_ending="",
        previous_bridge=None,
        accumulated_forbidden_repetition=[],
        must_carry_forward=["上一章的悬念"],
    )


def _make_step(json_payload: dict) -> tuple[BridgeStep, _FakeBuilder]:
    builder = _FakeBuilder()
    step = BridgeStep(
        _MockRouter(json_payload=json_payload, completion_tokens=200),
        builder,
        settings=Settings(),
    )
    return step, builder


def _empty_story_kernel_context() -> dict:
    return {"entities": [], "relationships": [], "timeline": []}


async def test_bridge_step_happy_path_with_previous_exit() -> None:
    payload = {
        "opening_time": "清晨",
        "opening_location": "废弃图书馆",
        "opening_pov": "林远",
        "transition_mode": "action_handoff",
        "emotional_carryover": "紧张与期待",
        "action_handoff": "林远决定前往废弃图书馆寻找线索",
        "causal_link": {
            "previous_event": "上一章发现神秘信件",
            "causal_mechanism": "信件指引前往图书馆",
            "unresolved_question": "信件的来源是什么",
            "open_threads": ["神秘信件", "图书馆的秘密"],
        },
        "pending_questions": ["信件的来源是什么"],
        "forbidden_repetition": [],
        "bridge_summary": "林远前往废弃图书馆",
        "sensory_anchors": ["潮湿的空气", "陈旧的书味"],
        "opening_acceptance_criteria": ["开场承接上一章动作"],
    }
    step, _ = _make_step(payload)

    previous_exit = ChapterExitState(
        chapter_number=1,
        time_marker="夜晚",
        location="雾霭小镇",
        pov="林远",
        must_carry_forward=["神秘信件"],
        open_questions=["信件的来源"],
    )
    packet = _make_packet(chapter_number=2, previous_exit=previous_exit)

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        tone="mysterious",
    )

    result = await step.run(input_data)

    assert isinstance(result, ChapterBridge)
    assert result.to_chapter == 2
    assert result.opening_location == "废弃图书馆"


def test_bridge_normalize_filters_non_rhetorical_forbidden_sources() -> None:
    packet = _make_packet(
        chapter_number=2,
        previous_exit=ChapterExitState(
            chapter_number=1,
            location="耳房",
            pov="周明",
            must_carry_forward=["账簿残页"],
            open_questions=["赵成安是否会来"],
        ),
    )
    normalized, _issues = BridgeStep._normalize_bridge_payload(
        {
            "opening_location": "耳房",
            "opening_pov": "周明",
            "transition_mode": "direct_continue",
            "action_handoff": "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
            "forbidden_repetition": [
                "避免重复使用「冷白灯光」（近8章平均3.0次/章）",
                "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
                "金丝微颤",
            ],
        },
        packet,
        story_kernel_context=_empty_story_kernel_context(),
    )

    assert normalized["forbidden_repetition"] == ["冷白灯光", "金丝微颤"]


async def test_bridge_step_happy_path_no_previous_exit() -> None:
    payload = {
        "opening_time": "清晨",
        "opening_location": "小镇",
        "opening_pov": "主角",
        "transition_mode": "synthetic",
        "emotional_carryover": "",
        "action_handoff": "开始新的冒险",
        "causal_link": {
            "previous_event": "",
            "causal_mechanism": "新的开始",
            "unresolved_question": "",
            "open_threads": [],
        },
        "pending_questions": [],
        "forbidden_repetition": [],
        "bridge_summary": "故事开始",
        "sensory_anchors": [],
        "opening_acceptance_criteria": [],
    }
    step, _ = _make_step(payload)

    packet = _make_packet(chapter_number=1, previous_exit=None)

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        tone="epic",
    )

    result = await step.run(input_data)

    assert isinstance(result, ChapterBridge)
    assert result.from_chapter == 0
    assert result.to_chapter == 1


async def test_bridge_step_empty_payload_uses_defaults() -> None:
    payload = {
        "opening_time": "",
        "opening_location": "",
        "opening_pov": "",
        "transition_mode": "",
        "emotional_carryover": "",
        "action_handoff": "",
        "causal_link": {},
        "pending_questions": [],
        "forbidden_repetition": [],
        "bridge_summary": "",
        "sensory_anchors": [],
        "opening_acceptance_criteria": [],
    }
    step, _ = _make_step(payload)

    previous_exit = ChapterExitState(
        chapter_number=1,
        time_marker="夜晚",
        location="小镇",
        pov="林远",
        must_carry_forward=["悬念"],
        open_questions=["问题"],
    )
    packet = _make_packet(chapter_number=2, previous_exit=previous_exit)

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        tone="mysterious",
    )

    result = await step.run(input_data)

    assert isinstance(result, ChapterBridge)
    assert result.to_chapter == 2


async def test_bridge_step_reading_power_hint_passed_to_context() -> None:
    payload = {
        "opening_time": "清晨",
        "opening_location": "城堡",
        "opening_pov": "主角",
        "transition_mode": "direct_continue",
        "emotional_carryover": "",
        "action_handoff": "继续探索",
        "causal_link": {
            "previous_event": "",
            "causal_mechanism": "",
            "unresolved_question": "",
            "open_threads": [],
        },
        "pending_questions": [],
        "forbidden_repetition": [],
        "bridge_summary": "摘要",
        "sensory_anchors": [],
        "opening_acceptance_criteria": [],
    }
    step, builder = _make_step(payload)

    packet = _make_packet()
    hint = {"hook_type_constraint": "开头保留悬念", "force_resolve_suspense": ["旧线索"]}

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        reading_power_hint=hint,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    quality = builder.last_context["stage_cards"]["quality"]
    assert quality["hook_type_constraint"] == "开头保留悬念"
    assert quality["force_resolve_suspense"] == ["旧线索"]
    assert "chapter_hook" not in quality
    assert "in_chapter_payoffs" not in quality


async def test_bridge_step_memory_hints_formatted() -> None:
    payload = {
        "opening_time": "清晨",
        "opening_location": "城堡",
        "opening_pov": "主角",
        "transition_mode": "direct_continue",
        "emotional_carryover": "",
        "action_handoff": "继续",
        "causal_link": {
            "previous_event": "",
            "causal_mechanism": "",
            "unresolved_question": "",
            "open_threads": [],
        },
        "pending_questions": [],
        "forbidden_repetition": [],
        "bridge_summary": "摘要",
        "sensory_anchors": [],
        "opening_acceptance_criteria": [],
    }
    step, builder = _make_step(payload)

    packet = _make_packet()
    memory_hints = {"motif_continuity": {"active_motifs": [{"motif": "怀表"}]}}

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        memory_hints=memory_hints,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    # Bridge capsule only includes motif_repetition_risks;
    # active_motifs is not consumed by bridge_chapter.j2
    memory_card = builder.last_context["stage_cards"].get("memory", {})
    assert "active_motifs" not in memory_card


async def test_bridge_step_style_profile_passed() -> None:
    payload = {
        "opening_time": "清晨",
        "opening_location": "城堡",
        "opening_pov": "主角",
        "transition_mode": "direct_continue",
        "emotional_carryover": "",
        "action_handoff": "继续",
        "causal_link": {
            "previous_event": "",
            "causal_mechanism": "",
            "unresolved_question": "",
            "open_threads": [],
        },
        "pending_questions": [],
        "forbidden_repetition": [],
        "bridge_summary": "摘要",
        "sensory_anchors": [],
        "opening_acceptance_criteria": [],
    }
    step, builder = _make_step(payload)

    packet = _make_packet()
    style_profile = {
        "modules": [
            {
                "name": "sentence_length",
                "rules": ["使用短句为主"],
            }
        ]
    }

    input_data = BridgeInput(
        chapter_state_packet=packet,
        story_kernel_context=_empty_story_kernel_context(),
        genre="fantasy",
        style_profile=style_profile,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context["stage_cards"]["style"]["modules"][0]["name"] == "sentence_length"
