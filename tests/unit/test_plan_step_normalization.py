"""Tests for PlanChapterStep beat normalization safeguards."""

from __future__ import annotations

import json

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.artifacts import ArtifactScope, ChapterSourceSliceArtifact
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import (
    CarryForwardItem,
    ChapterExitState,
    RelationshipState,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.long.services.guidance_contract_audit import audit_plan_contract
from novel_forge.pipeline.steps.plan_step import PlanChapterStep, PlanInput, missing_core_plan_keys
from novel_forge.pipeline.steps.planning.core import validate_literary_contract_plan_coverage
from novel_forge.pipeline.steps.planning.hints import _patch_missing_beat_scenes


class _PlanBuilder:
    def build(
        self,
        task_type,
        context,
        *,
        max_tokens: int,
        temperature: float,
        prior_messages=None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "plan"}],
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _PlanRouter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            content=self.content,
            model_id=request.model_id or "mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        )


class _SequencedPlanRouter:
    def __init__(
        self,
        responses: list[ModelResponse],
        *,
        capped_response_indices: set[int] | None = None,
    ) -> None:
        self.responses = responses
        self.capped_response_indices = capped_response_indices or set()
        self.calls = 0
        self.max_tokens_seen: list[int] = []
        self.temperatures_seen: list[float] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.max_tokens_seen.append(request.max_tokens)
        self.temperatures_seen.append(request.temperature)
        index = min(self.calls, len(self.responses) - 1)
        call_index = self.calls
        self.calls += 1
        response = self.responses[index]
        if call_index in self.capped_response_indices:
            return response.model_copy(
                update={
                    "completion_tokens": request.max_tokens,
                    "total_tokens": response.prompt_tokens + request.max_tokens,
                }
            )
        return response


def _with_plan_contract(payload: dict) -> dict:
    """Fill PLAN_CHAPTER keys that are now part of the strict response contract."""
    return {
        "chapter_type": "crisis",
        "emotional_arc": "从试探转为主动推进。",
        "relationship_evolution": [],
        "forbidden_elements": [],
        "forbidden_elements_soft": [],
        "forbidden_elements_quota": [],
        "intentional_callbacks": [],
        "foreshadowing_plan": [],
        "key_revelations": [],
        "world_rule_applications": [],
        "required_literals": [],
        "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": []},
        **payload,
    }


def test_plan_normalization_preserves_requirement_provenance_without_claiming_fulfillment():
    requirement = {
        "source": "chapter_contract.required_events:0",
        "scope": "scene_02",
        "satisfaction": "narrative",
        "status": "fulfilled",
        "evidence": "尚未写出的证据",
        "source_text_hash": "invented",
    }
    payload = _with_plan_contract(
        {
            "scene_intents": [{"summary": "得知密码"}, {"summary": "输入密码开门"}],
            "cross_scene_intent": {
                "cross_scene_references": [
                    {
                        "from_scene": "scene_01",
                        "to_scene": "scene_02",
                        "ref_type": "callback",
                        "description": "先取得密码才能打开门",
                        "requirement": requirement,
                    }
                ]
            },
            "required_literals": [
                {
                    "contract_id": "password",
                    "literal": "0707",
                    "scene_id": "scene_02",
                    "reason": "密码原文",
                    "placement_hint": "输入门禁时",
                    "requirement": {**requirement, "satisfaction": "literal"},
                }
            ],
        }
    )
    normalized = PlanChapterStep._normalize_plan_payload(
        payload,
        outline=ChapterOutline(chapter_number=1, title="开门", goal="进入档案室"),
        packet=ChapterStatePacket(
            chapter_number=1, chapter_outline={"chapter_number": 1, "goal": "进入档案室"}
        ),
        min_beats=2,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )
    cross_ref = normalized["cross_scene_intent"]["cross_scene_references"][0]
    literal = normalized["required_literals"][0]
    for item, kind in ((cross_ref, "narrative"), (literal, "literal")):
        assert item["requirement"]["source"] == requirement["source"]
        assert item["requirement"]["scope"] == "scene_02"
        assert item["requirement"]["satisfaction"] == kind
        assert item["requirement"]["status"] == "pending"
        assert item["requirement"]["evidence"] == ""
        assert item["requirement"]["source_text_hash"] == ""


def _literary_stage_cards() -> dict:
    return {
        "source": {
            "literary_contract": {
                "schema": "literary_contract_v1",
                "character": {
                    "pov_character": "玄昱",
                    "required_characters": ["玄昱"],
                },
                "plot": {
                    "required_events": ["进入账房"],
                    "required_progressions": ["发现账册缺页"],
                    "completion_criteria": ["账册缺页成为下一步线索"],
                    "exit_state_targets": ["玄昱带走账册残页"],
                },
                "setting": {
                    "primary_location": "账房",
                    "scene_design_goals": ["账房的门闩限制行动"],
                },
                "pov": {"pov_character": "玄昱", "pov_scope": "limited"},
                "theme": {
                    "primary_theme": "身份",
                    "theme_duties": ["身份与真相的代价"],
                    "arc_milestones": [
                        {"character": "玄昱", "milestone_description": "从旁观转为追问"}
                    ],
                },
                "style": {
                    "narrative_voice": "第三人称限知",
                    "character_voices": [{"name": "玄昱", "voice": "克制短句"}],
                },
            }
        }
    }


def _literary_source_slice() -> ChapterSourceSliceArtifact:
    return ChapterSourceSliceArtifact(
        project_id="test",
        scope=ArtifactScope(kind="chapter", ids=["1"]),
        artifact_id="chapter_source_slice:001",
        payload={
            "runtime": {
                "schema": "runtime_capsule_v1",
                "chapter_contract": {
                    "schema": "RuntimeChapterContract",
                    "chapter_number": 1,
                    "title": "账房风声",
                    "required_events": ["进入账房"],
                    "required_progressions": ["发现账册缺页"],
                    "literary_contract": _literary_stage_cards()["source"]["literary_contract"],
                },
                "world": {},
                "entities": [],
                "style": {},
                "creative_direction": {},
                "narrative_contract": {},
            }
        },
    )


def test_literary_contract_plan_coverage_flags_critical_missing_fields() -> None:
    diagnostics = validate_literary_contract_plan_coverage(
        {
            "scene_intents": [
                {
                    "summary": "众人在门外等候。",
                    "pov_character": "旁观者",
                }
            ],
            "required_state_transitions": [],
        },
        _literary_stage_cards(),
    )

    assert diagnostics["status"] == "fail"
    assert diagnostics["critical_missing"] == [
        "character.required_motivation",
        "plot.required_progression",
        "pov.character",
    ]
    assert "setting.scene_resistance" in diagnostics["warning_missing"]


def test_literary_contract_plan_coverage_passes_when_scene_fields_cover_contract() -> None:
    diagnostics = validate_literary_contract_plan_coverage(
        {
            "scene_intents": [
                {
                    "summary": "玄昱进入账房，发现账册缺页。",
                    "pov_character": "玄昱",
                    "location": "账房",
                    "conflict": "门闩和账房管事同时阻止他查看账册。",
                    "choice_pressure": "若强查会暴露身份，若退走会失去残页。",
                    "scene_resistance": "账房的门闩限制行动。",
                    "required_outcome": "玄昱带走账册残页，身份与真相的代价压到眼前。",
                    "symbol_usage_policy": "残页只象征身份裂缝，不直接解释主题。",
                    "character_motivations": [
                        {
                            "character": "玄昱",
                            "motivation": "确认旧案是否牵连自己身份。",
                            "stake": "继续追问会破坏眼前的安全身份。",
                        }
                    ],
                    "dialogue_voice_targets": {"玄昱": "克制短句"},
                }
            ],
            "required_state_transitions": ["账册缺页成为下一步线索"],
        },
        _literary_stage_cards(),
    )

    assert diagnostics == {
        "status": "pass",
        "critical_missing": [],
        "warning_missing": [],
        "details": "literary_contract_v1 coverage check",
    }


def test_literary_contract_coverage_accepts_distributed_event_evidence() -> None:
    stage_cards = {
        "source": {
            "literary_contract": {
                "schema": "literary_contract_v1",
                "plot": {
                    "required_events": [
                        "许清禾上门求助，确认她符合梦侦探行规的接案条件，收取报酬记忆存入旧玻璃罐"
                    ]
                },
            }
        }
    }
    diagnostics = validate_literary_contract_plan_coverage(
        {
            "scene_intents": [
                {
                    "summary": "许清禾冒雨来到事务所求助。",
                    "required_outcome": "沈岸核验她连续七天同一噩梦与现实幻觉的接案条件。",
                    "owned_events": ["沈岸取走她一段无关记忆，存进旧玻璃罐。"],
                }
            ],
            "required_state_transitions": [],
        },
        stage_cards,
    )

    assert diagnostics["critical_missing"] == []


def test_missing_outline_beat_becomes_owned_event_without_meta_text() -> None:
    patched = _patch_missing_beat_scenes(
        [{"scene_id": "scene_01", "summary": "主角进入旧宅", "purpose": "调查"}],
        ["主角进入旧宅", "钟声敲响，窗外出现黑影"],
    )

    assert "补充节拍" not in patched[0]["summary"]
    assert "钟声敲响，窗外出现黑影" in patched[0]["owned_events"]


def test_normalize_beats_splits_oversized_single_beat() -> None:
    raw = ["；".join([f"情节点{i}" for i in range(1, 10)])]
    beats = PlanChapterStep._normalize_beats(
        raw,
        min_beats=4,
        max_beats=8,
        max_chars=20,
    )
    assert len(beats) >= 4
    assert len(beats) <= 8
    assert all(len(b) <= 20 for b in beats)


def test_normalize_beats_backfills_when_too_few() -> None:
    beats = PlanChapterStep._normalize_beats(
        ["只返回了一条"],
        min_beats=4,
        max_beats=8,
        max_chars=120,
    )
    assert len(beats) == 4
    assert beats[0] == "只返回了一条"


def test_compact_contract_text_trims_overwritten_prose() -> None:
    compact = PlanChapterStep._compact_contract_text(
        "在上午十点二十分，林晚仍留在工作台前，灯光、机柜与低鸣共同维持着上一章的余波，"
        "她以戒备姿态继续检视音频资料并由此切入下一轮探查。",
        max_chars=40,
    )

    assert len(compact) <= 40


def test_normalize_plan_payload_backfills_scene_contract_fields() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="回声",
        goal="逼近事故真相",
        pov_character="林晚",
        setting="机库走廊",
        expected_word_count=3200,
    )
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=outline,
        canon_context={},
        bridge=ChapterBridge(
            to_chapter=2,
            emotional_carryover="惊魂未定，但不得不继续追查。",
            forbidden_repetition=["雨幕意象"],
        ),
        active_relationships=[
            RelationshipState(
                pair_id="linwan-zhoulin",
                characters=["林晚", "周临"],
                public_status="互相试探",
                last_shift_event="上一章暂时联手",
            )
        ],
        accumulated_forbidden_repetition=["冷白灯光"],
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "required_literals": [
                {
                    "contract_id": "door-code",
                    "literal": "七零四九",
                    "scene_id": "scene_02",
                    "reason": "这是实际门禁密码",
                    "placement_hint": "林晚在封锁门输入密码时",
                }
            ],
            "scene_intents": [
                {
                    "summary": "林晚试探周临的真实立场",
                    "required_characters": ["林晚", "周临"],
                    "location": "机库走廊",
                },
                {
                    "summary": "林晚在封锁门前确认下一步行动",
                    "required_characters": ["林晚"],
                    "location": "封锁门",
                },
            ],
        },
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    first_scene = normalized["scene_intents"][0]
    last_scene = normalized["scene_intents"][-1]

    assert normalized["required_literals"] == [
        {
            "contract_id": "door-code",
            "literal": "七零四九",
            "scene_id": "scene_02",
            "reason": "这是实际门禁密码",
            "placement_hint": "林晚在封锁门输入密码时",
        }
    ]

    assert first_scene["character_motivations"]
    assert first_scene["conflict"]
    assert first_scene["relationship_dynamics"]
    assert first_scene["emotional_beat"]
    assert first_scene["sensory_notes"]
    assert first_scene["required_outcome"]
    assert first_scene["time_marker"]
    assert last_scene["sensory_notes"]
    assert normalized["relationship_evolution"]
    assert normalized["forbidden_elements"] == []
    assert normalized["forbidden_elements_soft"] == ["雨幕意象", "冷白灯光"]
    assert {
        (item["text"], item["source"], item["level"])
        for item in normalized["forbidden_element_sources"]
    } >= {
        ("雨幕意象", "bridge", "soft"),
        ("冷白灯光", "accumulated", "soft"),
    }


def test_normalize_plan_payload_compacts_sensory_notes_to_candidate_anchors() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="异常回声",
        goal="进入机库确认信号",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=3000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {
                    "summary": "林晚进入机库",
                    "required_characters": ["林晚"],
                    "sensory_notes": (
                        "['金属冷光：顶灯闪烁', '低频嗡鸣：墙内设备震动', "
                        "'消毒水气味：空气发涩', '指尖触感：门把手冰凉']"
                    ),
                }
            ],
        },
        outline=outline,
        packet=None,
        min_beats=1,
        max_beats=4,
        beat_max_chars=120,
        style_profile=None,
    )

    notes = normalized["scene_intents"][0]["sensory_notes"]
    assert notes == "金属冷光：顶灯闪烁；低频嗡鸣：墙内设备震动"
    assert "消毒水气味" not in notes
    assert "指尖触感" not in notes


def test_normalize_plan_payload_compacts_merged_sensory_note_lists() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="异常回声",
        goal="进入机库确认信号",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=3000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {
                    "summary": "林晚进入机库",
                    "required_characters": ["林晚"],
                    "sensory_notes": (
                        "['金属冷光：顶灯闪烁', '低频嗡鸣：墙内设备震动']；"
                        "['消毒水气味：空气发涩', '门把手冰凉']"
                    ),
                }
            ],
        },
        outline=outline,
        packet=None,
        min_beats=1,
        max_beats=4,
        beat_max_chars=120,
        style_profile=None,
    )

    notes = normalized["scene_intents"][0]["sensory_notes"]
    assert notes == "金属冷光：顶灯闪烁；低频嗡鸣：墙内设备震动"
    assert "消毒水气味" not in notes


def test_normalize_plan_payload_respects_sensory_note_item_limit() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="异常回声",
        goal="进入机库确认信号",
        pov_character="林晚",
        setting="地下机库",
        expected_word_count=3000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {
                    "summary": "林晚进入机库",
                    "required_characters": ["林晚"],
                    "sensory_notes": "金属冷光；低频嗡鸣；消毒水气味",
                }
            ],
        },
        outline=outline,
        packet=None,
        min_beats=1,
        max_beats=4,
        beat_max_chars=120,
        style_profile=None,
        sensory_notes_max_items=1,
    )

    assert normalized["scene_intents"][0]["sensory_notes"] == "金属冷光"


def test_compute_plan_max_tokens_uses_higher_floor_for_long_chapter() -> None:
    assert PlanChapterStep._compute_plan_max_tokens(2500) == 10240
    assert PlanChapterStep._compute_plan_max_tokens(3200) == 14336
    assert PlanChapterStep._compute_plan_max_tokens(4500) == 16384


def test_missing_core_plan_keys_follows_contract() -> None:
    missing = missing_core_plan_keys(
        {
            "scene_intents": [],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
        }
    )

    assert missing == [
        "world_rule_applications",
        "required_literals",
        "chapter_type",
        "emotional_arc",
        "relationship_evolution",
        "forbidden_elements",
        "forbidden_elements_soft",
        "forbidden_elements_quota",
        "intentional_callbacks",
        "foreshadowing_plan",
        "key_revelations",
        "cross_scene_intent",
    ]


def test_normalized_plan_adds_default_cross_scene_intent_for_wave() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="试探",
        goal="推进试探关系",
        expected_word_count=2000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {"scene_id": "scene_01", "summary": "第一场推进线索。"},
                {"scene_id": "scene_02", "summary": "第二场兑现压力。"},
            ],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
        },
        outline=outline,
        packet=None,
        style_profile=None,
    )

    assert normalized["cross_scene_intent"] == {
        "cross_scene_references": [],
        "pacing_curve": [3, 3],
    }


async def test_plan_step_stops_when_required_keys_still_missing_after_retry() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="试探",
        goal="推进试探关系",
        pov_character="林晚",
        setting="旧仓库",
        expected_word_count=2800,
    )
    payload = (
        '{"scene_intents":[],"opening_contract":"承接","closing_contract":"留钩",'
        '"required_state_transitions":[],"cross_scene_intent":'
        '{"cross_scene_references":[],"pacing_curve":[]}}'
    )
    router = _PlanRouter(payload)
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    with pytest.raises(KeyError, match="chapter_type"):
        await step.run(
            PlanInput(
                chapter_outline=outline,
                canon_context={},
            )
        )

    assert router.calls == 2


async def test_plan_step_retries_once_for_literary_contract_coverage_gap() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="账房风声",
        goal="追查账册",
        pov_character="玄昱",
        setting="账房",
        expected_word_count=2800,
    )
    first_payload = _with_plan_contract(
        {
            "scene_intents": [
                {
                    "summary": "众人在账房外等候。",
                    "pov_character": "玄昱",
                    "location": "账房",
                }
            ],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
            "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": [3]},
        }
    )
    retry_payload = _with_plan_contract(
        {
            "scene_intents": [
                {
                    "summary": "玄昱进入账房，发现账册缺页。",
                    "pov_character": "玄昱",
                    "location": "账房",
                    "conflict": "账房管事阻止他翻检旧账。",
                    "choice_pressure": "追问会暴露他的身份疑点。",
                    "scene_resistance": "账房门闩与管事的迟疑共同阻碍行动。",
                    "required_outcome": "玄昱带走账册残页，身份与真相的代价浮现。",
                    "symbol_usage_policy": "账册残页只做身份裂缝的象征。",
                    "character_motivations": [
                        {
                            "character": "玄昱",
                            "motivation": "确认旧案是否牵连自己的身份。",
                            "stake": "若继续追问会失去安全伪装。",
                        }
                    ],
                    "dialogue_voice_targets": {"玄昱": "克制短句"},
                }
            ],
            "opening_contract": "承接",
            "closing_contract": "玄昱带走账册残页",
            "required_state_transitions": ["账册缺页成为下一步线索"],
            "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": [3]},
        }
    )
    router = _SequencedPlanRouter(
        [
            ModelResponse(
                content=json.dumps(first_payload, ensure_ascii=False),
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                latency_ms=1.0,
                cost_usd=0.0,
            ),
            ModelResponse(
                content=json.dumps(retry_payload, ensure_ascii=False),
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                latency_ms=1.0,
                cost_usd=0.0,
            ),
        ]
    )
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    plan = await step.run(
        PlanInput(
            chapter_outline=outline,
            canon_context={},
            chapter_source_slice=_literary_source_slice(),
        )
    )

    assert router.calls == 2
    scene = plan.model_dump(mode="json")["scene_intents"][0]
    assert scene["character_motivations"][0]["character"] == "玄昱"
    assert "账册残页" in scene["required_outcome"]


async def test_plan_step_blocks_when_literary_critical_gap_survives_retry() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="账房风声",
        goal="追查账册",
        pov_character="玄昱",
        setting="账房",
        expected_word_count=2800,
    )
    incomplete_payload = _with_plan_contract(
        {
            "scene_intents": [{"summary": "众人在账房外等候。", "pov_character": "玄昱"}],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
            "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": [3]},
        }
    )
    responses = [
        ModelResponse(
            content=json.dumps(incomplete_payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        )
        for _ in range(2)
    ]
    router = _SequencedPlanRouter(responses)
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    with pytest.raises(ConsistencyViolationError, match="文学合同未被章节计划覆盖"):
        await step.run(
            PlanInput(
                chapter_outline=outline,
                canon_context={},
                chapter_source_slice=_literary_source_slice(),
            )
        )

    assert router.calls == 2


async def test_plan_step_escalates_tokens_after_capped_second_failure() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="试探",
        goal="推进试探关系",
        pov_character="林晚",
        setting="旧仓库",
        expected_word_count=2800,
    )
    missing_contract_key_payload = _with_plan_contract(
        {
            "scene_intents": [],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
        }
    )
    missing_contract_key_payload.pop("chapter_type", None)
    responses = [
        ModelResponse(
            content=json.dumps(missing_contract_key_payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
        ModelResponse(
            content=json.dumps(missing_contract_key_payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
        ModelResponse(
            content=json.dumps(
                _with_plan_contract(
                    {
                        "scene_intents": [],
                        "opening_contract": "承接",
                        "closing_contract": "留钩",
                        "required_state_transitions": [],
                        "chapter_type": "crisis",
                    }
                ),
                ensure_ascii=False,
            ),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=200,
            total_tokens=210,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
    ]
    router = _SequencedPlanRouter(responses, capped_response_indices={1})
    router.output_limit_for_task = lambda *_args, **_kwargs: 16384
    settings = Settings(_env_file=None)
    step = PlanChapterStep(router, _PlanBuilder(), settings=settings)

    plan = await step.run(
        PlanInput(
            chapter_outline=outline,
            canon_context={},
        )
    )

    assert plan.chapter_type == "crisis"
    assert router.calls == 3
    assert router.max_tokens_seen[0] == router.max_tokens_seen[1]
    assert router.max_tokens_seen[2] == 16384
    assert router.temperatures_seen == [settings.temp_plan_chapter, 0.0, 0.0]


async def test_plan_step_escalates_tokens_when_cross_scene_intent_missing() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="试探",
        goal="推进试探关系",
        pov_character="林晚",
        setting="旧仓库",
        expected_word_count=2800,
    )
    missing_cross_scene_payload = _with_plan_contract(
        {
            "scene_intents": [],
            "opening_contract": "承接",
            "closing_contract": "留钩",
            "required_state_transitions": [],
        }
    )
    missing_cross_scene_payload.pop("cross_scene_intent", None)
    responses = [
        ModelResponse(
            content=json.dumps(missing_cross_scene_payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
        ModelResponse(
            content=json.dumps(missing_cross_scene_payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
        ModelResponse(
            content=json.dumps(
                _with_plan_contract(
                    {
                        "scene_intents": [],
                        "opening_contract": "承接",
                        "closing_contract": "留钩",
                        "required_state_transitions": [],
                    }
                ),
                ensure_ascii=False,
            ),
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=200,
            total_tokens=210,
            latency_ms=1.0,
            cost_usd=0.0,
        ),
    ]
    router = _SequencedPlanRouter(responses, capped_response_indices={1})
    router.output_limit_for_task = lambda *_args, **_kwargs: 16384
    settings = Settings(_env_file=None)
    step = PlanChapterStep(router, _PlanBuilder(), settings=settings)

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))

    assert plan.cross_scene_intent["cross_scene_references"] == []
    assert len(plan.cross_scene_intent["pacing_curve"]) == len(plan.scene_intents)
    assert router.calls == 3
    assert router.max_tokens_seen[0] == router.max_tokens_seen[1]
    assert router.max_tokens_seen[2] == 16384
    assert router.temperatures_seen == [settings.temp_plan_chapter, 0.0, 0.0]


async def test_plan_step_derives_pov_knowledge_constraints_from_kernel_context() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="暗线",
        goal="令昭追查账簿残页",
        pov_character="令昭",
        setting="旧书库",
        expected_word_count=1200,
    )
    payload = _with_plan_contract(
        {
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "令昭进入旧书库寻找账簿残页。",
                    "purpose": "推进调查。",
                    "conflict": "线索被人刻意遮掩。",
                    "required_characters": ["令昭", "沈鹤卿"],
                    "character_motivations": [
                        {
                            "character": "令昭",
                            "motivation": "确认残页去向",
                            "stake": "错过证据",
                        }
                    ],
                    "entry_state_refs": ["上一章发现残页缺口"],
                    "required_outcome": "令昭确认残页被转移。",
                    "exit_target_state": "她决定追查沈鹤卿的去向。",
                    "location": "旧书库",
                    "time_marker": "深夜",
                    "pov_character": "令昭",
                    "pov_scope": "limited",
                    "target_words": 1200,
                }
            ],
            "opening_contract": "开场落在旧书库门口。",
            "closing_contract": "章末令昭转向追查沈鹤卿。",
            "required_state_transitions": ["令昭从疑惑转为主动追查"],
        }
    )
    kernel_context = {
        "entities": [
            {
                "entity_id": "char_lz",
                "name": "令昭",
                "entity_type": "character",
                "sensory_access_rules": ["看不见密室内部的动作。"],
            },
            {
                "entity_id": "char_hq",
                "name": "沈鹤卿",
                "entity_type": "character",
            },
        ],
        "knowledge_ledger": [
            {
                "entry_id": "k_hq_secret",
                "entity_id": "char_hq",
                "fact": "沈鹤卿已经把残页交给密探",
                "knowledge_type": "known",
                "source_chapter": 1,
                "revealed_in_chapter": 0,
                "visibility": "private",
            },
            {
                "entry_id": "k_lz_private",
                "entity_id": "char_lz",
                "fact": "令昭自己的隐秘判断",
                "knowledge_type": "known",
                "source_chapter": 1,
                "revealed_in_chapter": 0,
                "visibility": "private",
            },
            {
                "entry_id": "k_hq_future_reveal",
                "entity_id": "char_hq",
                "fact": "沈鹤卿未来公开的证词",
                "knowledge_type": "known",
                "source_chapter": 1,
                "revealed_in_chapter": 9,
                "visibility": "public",
            },
        ],
    }
    router = _PlanRouter(json.dumps(payload, ensure_ascii=False))
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    plan = await step.run(PlanInput.from_kernel_context(outline, kernel_context))

    constraints = plan.scene_intents[0].pov_knowledge_constraints
    assert "【沈鹤卿】的私密已知事实" in constraints.forbidden_knowledge
    assert "第9章才可公开揭示的信息（沈鹤卿）" in constraints.forbidden_knowledge
    assert all("残页交给密探" not in item for item in constraints.forbidden_knowledge)
    assert all("未来公开的证词" not in item for item in constraints.forbidden_knowledge)
    assert "令昭自己的隐秘判断" not in "；".join(constraints.forbidden_knowledge)
    assert constraints.sensory_limits == ["看不见密室内部的动作。"]
    assert constraints.scope_label == "limited"


async def test_plan_step_keeps_sensory_pov_constraints_without_knowledge_ledger() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="隔墙",
        goal="令昭误判隔壁动静",
        pov_character="令昭",
        setting="客栈",
        expected_word_count=1000,
    )
    payload = _with_plan_contract(
        {
            "scene_intents": [
                {
                    "scene_id": "scene_01",
                    "summary": "令昭在客栈房内听见模糊响动。",
                    "purpose": "制造误判。",
                    "conflict": "隔墙声音不清。",
                    "required_characters": ["令昭"],
                    "entry_state_refs": [],
                    "required_outcome": "令昭误判隔壁发生争执。",
                    "exit_target_state": "她决定出门查看。",
                    "location": "客栈",
                    "time_marker": "夜间",
                    "pov_character": "令昭",
                    "target_words": 1000,
                }
            ],
            "opening_contract": "开场落在客栈房内。",
            "closing_contract": "章末令昭出门查看。",
            "required_state_transitions": [],
        }
    )
    kernel_context = {
        "entities": [
            {
                "entity_id": "char_lz",
                "name": "令昭",
                "entity_type": "character",
                "sensory_access_rules": ["听不清隔壁低声交谈。"],
            }
        ],
        "knowledge_ledger": [],
    }
    router = _PlanRouter(json.dumps(payload, ensure_ascii=False))
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    plan = await step.run(PlanInput.from_kernel_context(outline, kernel_context))

    constraints = plan.scene_intents[0].pov_knowledge_constraints
    assert constraints.forbidden_knowledge == []
    assert constraints.sensory_limits == ["听不清隔壁低声交谈。"]


async def test_plan_step_merges_missing_beat_coverage_without_hollow_scenes() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="逆光重逢",
        goal="完成峰会初遇并建立似曾相识母题",
        pov_character="沈念卿",
        setting="2025年4月，上海陆家嘴，行业峰会VIP休息室及主会场",
        expected_word_count=4500,
        beats_summary=[
            "沈念卿在VIP休息室逆光整理演讲稿，身后传来熟悉脚步声",
            "陆云峥越过人群与她四目相对，无声唤出念卿",
            "沈念卿窒息感骤起，本能后退并攥紧讲稿",
            "陆云峥克制颔首后退入人群，只在心里确认找到她",
            "林绾绾端咖啡出现，用毒舌点破沈念卿的失态",
            "沈念卿完成峰会演讲，余光搜索听众席却找不到他",
            "酒会上智云科技的名字刺入耳膜，商战暗线露出轻微钩子",
            "红酒意外引发人群骚动，陆云峥侧脸被灯光照亮",
            "两人隔着半个会场再次对视，他举杯遥敬，她眩晕扶柱",
        ],
    )
    payload = {
        "chapter_type": "crisis",
        "opening_contract": "开场落在VIP休息室，先交代沈念卿压住不安核对演讲稿。",
        "closing_contract": "章末沈念卿带着无法解释的困惑离场，林绾绾决定私下追问。",
        "required_state_transitions": ["沈念卿从职业冷静转为困惑在意"],
        "scene_intents": [
            {
                "scene_id": "scene_01",
                "summary": "沈念卿在VIP休息室整理演讲稿时与陆云峥四目相对。",
                "purpose": "完成初遇触发。",
                "conflict": "职业冷静与身体本能后退发生冲突。",
                "required_characters": ["沈念卿", "陆云峥"],
                "character_motivations": [
                    {"character": "沈念卿", "motivation": "稳住演讲前状态", "stake": "公开失态"},
                    {"character": "陆云峥", "motivation": "确认梦中人", "stake": "执念失焦"},
                ],
                "entry_state_refs": ["演讲前核对流程"],
                "required_outcome": "完成四目相对与无声唤名，沈念卿后退半步。",
                "exit_target_state": "沈念卿意识到自己无法解释这次失态。",
                "location": "VIP休息室",
                "time_marker": "2025年4月上午",
                "target_words": 1500,
            },
            {
                "scene_id": "scene_02",
                "summary": "林绾绾看穿沈念卿的异样并用毒舌保护她。",
                "purpose": "建立闺蜜观察者位置。",
                "conflict": "沈念卿想遮掩，林绾绾已经看穿。",
                "required_characters": ["沈念卿", "林绾绾"],
                "character_motivations": [
                    {"character": "林绾绾", "motivation": "保护闺蜜体面", "stake": "异常被外人察觉"}
                ],
                "entry_state_refs": ["初遇后沈念卿指节发白"],
                "required_outcome": "林绾绾成为第一个察觉异常的人。",
                "exit_target_state": "沈念卿恢复表面镇定但留下破绽。",
                "location": "休息室通道",
                "time_marker": "演讲前",
                "target_words": 800,
            },
            {
                "scene_id": "scene_03",
                "summary": "沈念卿完成演讲却在掌声里寻找白衬衫身影。",
                "purpose": "展示职业能力与无意识在意的反差。",
                "conflict": "演讲职责要求专注，内心却被初遇牵引。",
                "required_characters": ["沈念卿"],
                "character_motivations": [
                    {"character": "沈念卿", "motivation": "完成发言", "stake": "职业声誉"}
                ],
                "entry_state_refs": ["林绾绾追问刚才的人是谁"],
                "required_outcome": "演讲成功，同时确认她在搜索陆云峥。",
                "exit_target_state": "她不明白自己为何在意陌生人的去向。",
                "location": "主会场",
                "time_marker": "演讲环节",
                "target_words": 1100,
            },
            {
                "scene_id": "scene_04",
                "summary": "酒会上两人再次隔场对视，陆云峥举杯遥敬，沈念卿眩晕扶柱。",
                "purpose": "完成章末钩子。",
                "conflict": "公开社交场合的体面与无法压制的眩晕反应冲突。",
                "required_characters": ["沈念卿", "陆云峥", "林绾绾"],
                "character_motivations": [
                    {"character": "陆云峥", "motivation": "克制守望", "stake": "靠近会伤到她"}
                ],
                "entry_state_refs": ["演讲后寻找未果"],
                "required_outcome": "智云科技名字出现，两人隔场对视，沈念卿眩晕。",
                "exit_target_state": "似曾相识母题成立，林绾绾决定追问。",
                "location": "鸡尾酒会",
                "time_marker": "午前酒会",
                "target_words": 1100,
            },
        ],
    }
    router = _PlanRouter(json.dumps(_with_plan_contract(payload), ensure_ascii=False))
    step = PlanChapterStep(
        router,
        _PlanBuilder(),
        settings=Settings(_env_file=None, long_plan_max_scene_switches=5),
    )

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))
    findings = audit_plan_contract(plan=plan, chapter_number=1, target_word_count=4500)

    assert findings == []
    assert len(plan.scene_intents) <= 5
    assert sum(scene.target_words for scene in plan.scene_intents) == 4500
    assert all(scene.conflict for scene in plan.scene_intents)
    assert all(scene.character_motivations for scene in plan.scene_intents)
    assert all(scene.required_outcome for scene in plan.scene_intents)
    assert all(scene.exit_target_state for scene in plan.scene_intents)
    assert all(scene.time_marker for scene in plan.scene_intents)
    assert not any("补充节拍" in scene.summary for scene in plan.scene_intents)
    assert any(scene.owned_events for scene in plan.scene_intents)


async def test_plan_step_never_inserts_local_scenes_for_missing_beats() -> None:
    outline = ChapterOutline(
        chapter_number=7,
        title="暗潮",
        goal="让调查线与关系线同时升压",
        pov_character="林晚",
        setting="旧码头",
        expected_word_count=3200,
        beats_summary=[
            "林晚抵达旧码头确认货柜编号",
            "周临迟到并隐瞒自己刚见过线人",
            "货柜门内出现被烧过的账页",
            "两人在警笛声里带着账页撤离",
        ],
    )
    payload = {
        "chapter_type": "crisis",
        "opening_contract": "开场落在旧码头入口，林晚先确认货柜编号。",
        "closing_contract": "章末两人带着账页撤离，并留下线人身份疑点。",
        "required_state_transitions": ["林晚确认周临仍有隐瞒"],
        "scene_intents": [
            {
                "scene_id": "scene_01",
                "summary": "林晚在旧码头入口确认异常货柜。",
                "purpose": "启动调查行动。",
                "conflict": "线索诱人但现场风险不明。",
                "required_characters": ["林晚"],
                "character_motivations": [
                    {"character": "林晚", "motivation": "拿到账页线索", "stake": "调查停滞"}
                ],
                "entry_state_refs": ["上一章锁定旧码头"],
                "required_outcome": "林晚确认货柜编号并决定进入。",
                "exit_target_state": "调查从外围观察转入现场取证。",
                "location": "旧码头入口",
                "time_marker": "夜间",
                "target_words": 1600,
            },
            {
                "scene_id": "scene_02",
                "summary": "林晚和周临在警笛声里带着账页撤离。",
                "purpose": "完成章末撤离和疑点保留。",
                "conflict": "保住账页与追问周临之间只能先取其一。",
                "required_characters": ["林晚", "周临"],
                "character_motivations": [
                    {"character": "周临", "motivation": "掩住线人身份", "stake": "线人暴露"}
                ],
                "entry_state_refs": ["货柜取证"],
                "required_outcome": "两人带着账页撤离，周临隐瞒线人会面。",
                "exit_target_state": "线索到手但信任裂缝扩大。",
                "location": "旧码头货柜区",
                "time_marker": "夜间稍后",
                "target_words": 1600,
            },
        ],
    }
    router = _PlanRouter(json.dumps(_with_plan_contract(payload), ensure_ascii=False))
    step = PlanChapterStep(
        router,
        _PlanBuilder(),
        settings=Settings(_env_file=None, long_plan_max_scene_switches=6),
    )

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))
    findings = audit_plan_contract(plan=plan, chapter_number=7, target_word_count=3200)

    assert findings == []
    assert len(plan.scene_intents) == 2
    assert all("_patched" not in scene.scene_id for scene in plan.scene_intents)
    assert not any("补充节拍" in scene.summary for scene in plan.scene_intents)
    assert any(scene.owned_events for scene in plan.scene_intents)
    assert sum(scene.target_words for scene in plan.scene_intents) == 3200


async def test_plan_step_assigns_unclaimed_required_state_transitions_to_scenes() -> None:
    outline = ChapterOutline(
        chapter_number=23,
        title="窗口之半",
        goal="风季倒数第一天，信号窗口收窄至每日仅余一次。",
        pov_character="沈鹿溪",
        setting="民宿楼顶、苏皖房间",
        expected_word_count=3000,
        beats_summary=[
            "沈鹿溪在民宿楼顶完成短片剪辑框架",
            "苏皖在房间独自处置协议",
        ],
        time_anchor="风季倒数第一天",
    )
    transition = "风季从倒数第一天推进到倒数第一天，信号窗口收窄至每日仅余一次"
    payload = {
        "chapter_type": "emotional",
        "opening_contract": "沈鹿溪清晨走上民宿楼顶开始剪辑。",
        "closing_contract": "沈鹿溪完成最终剪辑，发布留待下一章。",
        "required_state_transitions": [
            transition,
            "苏皖在民宿房间将未签字协议放进抽屉，独自承担选择后果",
        ],
        "scene_intents": [
            {
                "scene_id": "scene_01",
                "summary": "沈鹿溪上楼拼接风声纪录短片素材。",
                "purpose": "推进短片剪辑。",
                "conflict": "窗口时间有限，素材仍需整理。",
                "required_characters": ["沈鹿溪"],
                "character_motivations": [
                    {"character": "沈鹿溪", "motivation": "完成剪辑", "stake": "错过窗口"}
                ],
                "entry_state_refs": ["上一章确认只剩最后一天"],
                "required_outcome": "沈鹿溪完成素材拼接和画外音对齐。",
                "exit_target_state": "沈鹿溪完成剪辑框架，下楼喝水。",
                "location": "民宿楼顶",
                "time_marker": "清晨",
                "target_words": 1500,
            },
            {
                "scene_id": "scene_02",
                "summary": "沈鹿溪经过苏皖房间，看见苏皖处置协议。",
                "purpose": "完成苏皖协议支线镜像。",
                "conflict": "签或不签都要独自承担后果。",
                "required_characters": ["沈鹿溪", "苏皖"],
                "character_motivations": [
                    {"character": "苏皖", "motivation": "自己处置协议", "stake": "失去借口"}
                ],
                "entry_state_refs": ["沈鹿溪下楼"],
                "required_outcome": "苏皖将未签字协议放进抽屉，独自承担选择后果。",
                "exit_target_state": "苏皖完成协议处置，沈鹿溪离开。",
                "location": "民宿苏皖房间",
                "time_marker": "上午",
                "target_words": 1500,
            },
        ],
    }
    router = _PlanRouter(json.dumps(_with_plan_contract(payload), ensure_ascii=False))
    step = PlanChapterStep(router, _PlanBuilder(), settings=Settings(_env_file=None))

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))
    findings = audit_plan_contract(plan=plan, chapter_number=23, target_word_count=3000)

    assert findings == []
    assert transition in plan.scene_intents[0].owned_state_changes


async def test_plan_step_scene_limit_preserves_original_closing_scene() -> None:
    outline = ChapterOutline(
        chapter_number=8,
        title="断桥",
        goal="压缩追逃并留下章末钩子",
        pov_character="林晚",
        setting="城郊断桥",
        expected_word_count=3600,
    )
    scenes = []
    for index in range(1, 7):
        scenes.append(
            {
                "scene_id": f"scene_{index:02d}",
                "summary": f"第{index}场推进追逃压力。"
                if index < 6
                else "第六场最终收束，林晚在断桥下发现新标记。",
                "purpose": f"推进追逃节点 {index}。",
                "conflict": "追兵压近与证据保全发生冲突。",
                "required_characters": ["林晚", "周临"],
                "character_motivations": [
                    {"character": "林晚", "motivation": "保住证据", "stake": "线索断裂"}
                ],
                "entry_state_refs": [f"追逃节点 {index - 1}"],
                "required_outcome": f"完成追逃节点 {index} 的可见变化。",
                "exit_target_state": f"局势进入追逃节点 {index + 1}。",
                "location": "城郊断桥",
                "time_marker": f"夜间推进段 {index}",
                "target_words": 600,
            }
        )
    payload = {
        "chapter_type": "crisis",
        "opening_contract": "开场承接追逃压力。",
        "closing_contract": "章末林晚在断桥下发现新标记。",
        "required_state_transitions": ["林晚确认追兵背后另有指挥者"],
        "scene_intents": scenes,
    }
    router = _PlanRouter(json.dumps(_with_plan_contract(payload), ensure_ascii=False))
    step = PlanChapterStep(
        router,
        _PlanBuilder(),
        settings=Settings(_env_file=None, long_plan_max_scene_switches=4),
    )

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))
    findings = audit_plan_contract(plan=plan, chapter_number=8, target_word_count=3600)

    assert findings == []
    assert len(plan.scene_intents) == 4
    assert "第六场最终收束" in plan.scene_intents[-1].summary
    assert "第5场推进追逃压力" in plan.scene_intents[-2].summary
    assert sum(scene.target_words for scene in plan.scene_intents) == 3600


async def test_plan_step_enforces_configured_scene_limit_over_outline_beats() -> None:
    """Outline beats must never raise the scene cap above the configured limit.

    Regression: ch10 had 11 beats_summary items which inflated _outline_scene_floor
    to 11, overriding the user's long_plan_max_scene_switches=8.  The fix caps the
    floor at the configured max so that enforce_scene_switch_limit always trims.
    """
    outline = ChapterOutline(
        chapter_number=2,
        title="对手初现",
        goal="按顺序完成峰会多节拍交锋",
        pov_character="沈念卿",
        setting="峰会会场",
        expected_word_count=4200,
        beats_summary=[f"节拍 {index}" for index in range(1, 9)],
    )
    scenes = []
    for index in range(1, 9):
        scenes.append(
            {
                "scene_id": f"scene_{index:02d}",
                "summary": f"第{index}场按时间顺序推进。",
                "purpose": f"覆盖节拍 {index}。",
                "conflict": "公开体面与暗线压力冲突。",
                "required_characters": ["沈念卿"],
                "character_motivations": [
                    {"character": "沈念卿", "motivation": "维持主动", "stake": "失去信息优势"}
                ],
                "entry_state_refs": [f"上一场出口 {index - 1}"],
                "required_outcome": f"完成节拍 {index} 的可观察结果。",
                "exit_target_state": f"进入节拍 {index + 1}。",
                "location": f"会场区域 {index}",
                "time_marker": f"时间段 {index}",
                "target_words": 525,
            }
        )
    router = _PlanRouter(
        json.dumps(
            _with_plan_contract(
                {
                    "chapter_type": "crisis",
                    "opening_contract": "开场承接上一章通道余波。",
                    "closing_contract": "章末留下供应商危机出口。",
                    "required_state_transitions": ["沈念卿从被动警觉转为主动应对"],
                    "scene_intents": scenes,
                }
            ),
            ensure_ascii=False,
        )
    )
    step = PlanChapterStep(
        router,
        _PlanBuilder(),
        settings=Settings(_env_file=None, long_plan_max_scene_switches=5),
    )

    plan = await step.run(PlanInput(chapter_outline=outline, canon_context={}))

    assert len(plan.scene_intents) == 5
    # Trimmer keeps first (max_scenes-1) + last, then re-numbers sequentially
    assert plan.scene_intents[0].scene_id == "scene_01"
    # Original scene_08 is preserved as the closing scene (renumbered to scene_05)
    assert plan.scene_intents[-1].scene_id == "scene_05"
    # Original scene_08 content should be in the last scene
    assert "第8场" in plan.scene_intents[-1].summary or "节拍 8" in plan.scene_intents[-1].summary


def test_normalize_plan_payload_consumes_chapter_type_and_scene_target_words() -> None:
    outline = ChapterOutline(
        chapter_number=5,
        title="回潮",
        goal="压缩冲突并逼出关系抉择",
        pov_character="林晚",
        setting="旧档案馆",
        expected_word_count=3600,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "chapter_type": "情感关系章",
            "scene_intents": [
                {
                    "summary": "林晚与周临在档案馆对峙",
                    "required_characters": ["林晚", "周临"],
                    "location": "旧档案馆",
                    "target_words": 900,
                },
                {
                    "summary": "林晚独自核对证据并做出取舍",
                    "required_characters": ["林晚"],
                    "location": "阅览室",
                },
            ],
        },
        outline=outline,
        packet=None,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    assert normalized["chapter_type"] == "emotional"
    scene_targets = [scene["target_words"] for scene in normalized["scene_intents"]]
    assert all(target > 0 for target in scene_targets)
    assert abs(sum(scene_targets) - 3600) <= 360


def test_normalize_plan_payload_filters_story_anchors_and_keeps_callbacks() -> None:
    outline = ChapterOutline(
        chapter_number=6,
        title="旧账",
        goal="确认残页去向",
        pov_character="周明",
        setting="西官仓耳房",
        expected_word_count=2800,
    )
    packet = ChapterStatePacket(
        chapter_number=6,
        chapter_outline=outline,
        canon_context={},
        bridge=ChapterBridge(
            to_chapter=6,
            bridge_summary="承接残页交接",
            action_handoff="周明守着账簿残页，等赵成安的消息。",
            forbidden_repetition=["账簿残页", "惨淡月光"],
        ),
        must_carry_forward=["账簿残页线索必须落地"],
        known_characters=["周明", "赵成安"],
        accumulated_forbidden_repetition=["父亲", "冷白灯光"],
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "forbidden_elements": ["周明", "账簿残页", "惨淡月光", "金丝颤动"],
            "forbidden_elements_soft": ["父亲", "冷白灯光"],
            "intentional_callbacks": ["金丝颤动"],
        },
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    assert normalized["forbidden_elements"] == ["惨淡月光"]
    assert normalized["forbidden_elements_soft"] == ["冷白灯光"]
    assert normalized["intentional_callbacks"] == ["金丝颤动"]


def test_normalize_plan_payload_canonicalizes_source_forbidden_terms() -> None:
    outline = ChapterOutline(
        chapter_number=7,
        title="袖底",
        goal="确认残页去向",
        pov_character="周明",
        setting="西官仓耳房",
        expected_word_count=2800,
    )
    packet = ChapterStatePacket(
        chapter_number=7,
        chapter_outline=outline,
        canon_context={},
        bridge=ChapterBridge(
            to_chapter=7,
            action_handoff="周明把账簿残页交给赵成安，确认账簿残页线索落地。",
            forbidden_repetition=[
                "避免重复使用「指节发白」（近8章平均3.0次/章）",
                "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
                "冷雨",
            ],
        ),
        known_characters=["周明", "赵成安"],
        must_carry_forward=["账簿残页线索必须落地"],
        accumulated_forbidden_repetition=[
            "避免重复使用「袖中」（近8章平均9.0次/章）",
            "本章必须确认账簿残页线索落地",
            "冷白灯光",
        ],
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {},
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    assert "指节发白" in normalized["forbidden_elements_soft"]
    assert "冷雨" in normalized["forbidden_elements_soft"]
    assert "袖中" in normalized["forbidden_elements_soft"]
    assert "冷白灯光" in normalized["forbidden_elements_soft"]
    assert not any("避免重复使用" in item for item in normalized["forbidden_elements_soft"])
    assert not any("账簿残页线索落地" in item for item in normalized["forbidden_elements_soft"])
    assert {
        (item["text"], item["source"], item["level"])
        for item in normalized["forbidden_element_sources"]
    } >= {
        ("指节发白", "bridge", "soft"),
        ("袖中", "accumulated", "soft"),
    }


def test_normalize_plan_payload_ranks_and_caps_forbidden_by_relevance() -> None:
    outline = ChapterOutline(
        chapter_number=8,
        title="雨夜铃声",
        goal="在灯光摇晃中逼近铜铃线索",
        pov_character="周明",
        setting="西官仓耳房",
        expected_word_count=2800,
    )
    packet = ChapterStatePacket(
        chapter_number=8,
        chapter_outline=outline,
        canon_context={},
        accumulated_forbidden_repetition=[
            "袖中",
            "远山",
            "冷白灯光",
            "铜铃声",
            "旧木门",
        ],
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {
                    "summary": "灯光摇晃，铜铃被风带动，周明确认线索方向。",
                    "required_outcome": "周明决定沿声音追查。",
                }
            ]
        },
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
        forbidden_soft_max_items=2,
        forbidden_sources_max_items=2,
        rank_forbidden_by_relevance=True,
    )

    assert normalized["forbidden_elements_soft"] == ["铜铃声", "冷白灯光"]
    assert [item["text"] for item in normalized["forbidden_element_sources"]] == [
        "铜铃声",
        "冷白灯光",
    ]


def test_normalize_plan_payload_uses_motif_context_as_ranking_evidence() -> None:
    outline = ChapterOutline(
        chapter_number=9,
        title="风声",
        goal="追查门后异响",
        pov_character="周明",
        setting="西官仓耳房",
        expected_word_count=2800,
    )
    packet = ChapterStatePacket(
        chapter_number=9,
        chapter_outline=outline,
        canon_context={},
        accumulated_forbidden_repetition=["旧木门", "冷风", "远山"],
    )
    memory_hints = {
        "motif_continuity": {
            "active_motifs": [
                {
                    "motif_id": "m-door",
                    "name": "旧木门",
                    "category": "声音",
                    "occurrence_count": 3,
                    "recent_chapters": [7, 8],
                    "thematic_meaning": "紧张入口的听觉锚点",
                },
                {
                    "motif_id": "m-wind",
                    "name": "冷风",
                    "category": "主题",
                    "occurrence_count": 4,
                    "recent_chapters": [6, 8],
                },
            ],
            "suggested_callbacks": [{"motif": "冷风", "reason": "主题回环"}],
        }
    }

    normalized = PlanChapterStep._normalize_plan_payload(
        {},
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
        memory_hints=memory_hints,
        forbidden_soft_max_items=1,
        forbidden_sources_max_items=1,
        rank_forbidden_by_relevance=True,
    )

    assert normalized["forbidden_elements_soft"] == ["旧木门"]
    assert normalized["forbidden_element_sources"][0]["motif_id"] == "m-door"
    assert normalized["forbidden_element_sources"][0]["motif_category"] == "声音"


def test_normalize_plan_payload_keeps_source_level_after_quota_dedup() -> None:
    outline = ChapterOutline(
        chapter_number=10,
        title="铃声",
        goal="有限回收铜铃线索",
        pov_character="周明",
        setting="西官仓耳房",
        expected_word_count=2800,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "forbidden_elements": ["铜铃声"],
            "forbidden_elements_quota": ["铜铃声"],
        },
        outline=outline,
        packet=None,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
        rank_forbidden_by_relevance=True,
    )

    assert normalized["forbidden_elements"] == []
    assert normalized["forbidden_elements_quota"] == ["铜铃声"]
    assert normalized["forbidden_element_sources"][0]["text"] == "铜铃声"
    assert normalized["forbidden_element_sources"][0]["level"] == "quota"
    assert normalized["forbidden_element_sources"][0]["reason"] == (
        "plan_chapter forbidden_elements_quota"
    )


def test_trim_plan_canon_context_keeps_prompt_consumed_fields_only() -> None:
    canon_context = {
        "characters": {f"角色{i}": {"gender": "女" if i % 2 else "男"} for i in range(1, 15)},
        "recent_events": [{"chapter": i, "event": f"事件{i}"} for i in range(1, 20)],
        "active_foreshadowing": [{"description": f"伏笔{i}"} for i in range(1, 12)],
        "entity_reference_graph": {
            "identity_links": [
                {
                    "source": "小林",
                    "target": "林晚",
                    "link_type": "alias_of",
                    "description": "周临对林晚的私下称呼。",
                }
            ],
            "context_links": [
                {
                    "source": "异常信号",
                    "target": "隐藏终端",
                    "link_type": "emitted_by",
                    "description": "异常信号来自隐藏终端。",
                }
            ],
        },
        "unused_field": {"blob": "should_not_pass"},
    }

    trimmed = PlanChapterStep._trim_plan_canon_context(
        canon_context,
        focus_characters=["角色9", "角色1"],
        max_characters=6,
        max_recent_events=5,
        max_foreshadowing=4,
    )

    assert set(trimmed.keys()) == {
        "characters",
        "recent_events",
        "active_foreshadowing",
        "entity_reference_graph",
    }
    assert trimmed["entity_reference_graph"]["identity_links"][0]["source"] == "小林"


def test_trim_plan_canon_context_preserves_complete_scoped_entity_links() -> None:
    canon_context = {
        "characters": {"林晚": {}, "周临": {}},
        "entity_reference_graph": {
            "identity_links": [
                {
                    "source": "旧称号",
                    "target": "远古组织",
                    "link_type": "title_of",
                    "confidence": 1.0,
                },
                {
                    "source": "小林",
                    "target": "林晚",
                    "link_type": "alias_of",
                    "description": "周临对林晚的私下称呼。",
                    "confidence": 0.7,
                },
            ],
            "context_links": [
                {
                    "source": "异常信号",
                    "target": "隐藏终端",
                    "link_type": "emitted_by",
                    "confidence": 0.9,
                },
                {
                    "source": "林晚",
                    "target": "怀表",
                    "link_type": "owned_by",
                    "confidence": 0.5,
                },
            ],
        },
    }

    trimmed = PlanChapterStep._trim_plan_canon_context(
        canon_context,
        focus_characters=["林晚"],
    )

    refs = trimmed["entity_reference_graph"]
    assert [item["source"] for item in refs["identity_links"]] == ["旧称号", "小林"]
    assert [item["source"] for item in refs["context_links"]] == ["异常信号", "林晚"]


def test_trim_plan_memory_hints_formats_structured_motif_payloads() -> None:
    trimmed = PlanChapterStep._trim_plan_memory_hints(
        {
            "motif_continuity": {
                "active_motifs": [{"motif": "纸灰", "category": "意象"}],
                "forbidden_repetition": [{"motif": "冷月"}],
                "suggested_callbacks": [{"motif": "焚书残页", "reason": "呼应开篇火盆中的纸灰"}],
            }
        }
    )

    motif_continuity = trimmed["motif_continuity"]
    assert motif_continuity["active_motifs"] == ["纸灰（意象）"]
    assert motif_continuity["forbidden_repetition"] == ["冷月"]
    assert motif_continuity["suggested_callbacks"] == ["焚书残页：呼应开篇火盆中的纸灰"]
    assert all("{" not in item for values in motif_continuity.values() for item in values)


def test_trim_plan_memory_hints_keeps_prompt_consumed_fields_only() -> None:
    hints = {
        "relevant_history": [
            {
                "chapter_number": 8,
                "event_summary": "旧线索回响",
                "relevance_score": 0.91,
                "unused": "x",
            },
            {"chapter_number": 7, "event_summary": "另一条线", "relevance_score": 0.73},
        ],
        "outline_context": {
            "chapter_summary": "最近几章围绕失踪案推进。",
            "unresolved_questions": ["谁在暗中接应", "钥匙来源"],
            "relationship_changes": ["林晚↔周临:互信上升"],
            "similar_events": [{"chapter_number": 2, "event_summary": "不会进入模板"}],
        },
        "layered_context": {
            "L0_identity": "标题：测试书 | 题材：科幻",
            "L1_core_memory": "主角已确认异常信号并继续追查。",
            "L2_on_demand": "近期出现了第二次回响。",
            "L3_deep_search": "不会进入模板",
        },
        "previous_chapter_events": [{"chapter_number": 9, "event_summary": "不会进入模板"}],
    }

    trimmed = PlanChapterStep._trim_plan_memory_hints(hints)

    assert set(trimmed.keys()) == {"relevant_history", "outline_context", "layered_context"}
    assert len(trimmed["relevant_history"]) == 2
    assert set(trimmed["relevant_history"][0].keys()) == {
        "chapter_number",
        "event_summary",
        "relevance_score",
    }
    assert set(trimmed["outline_context"].keys()) == {
        "chapter_summary",
        "unresolved_questions",
        "relationship_changes",
    }
    assert set(trimmed["layered_context"].keys()) == {"L1_core_memory"}


def test_normalize_plan_payload_caps_foreshadowing_plan_to_two() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="余烬",
        goal="推进调查线索",
        pov_character="林晚",
        setting="旧城区",
        expected_word_count=3000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [{"summary": "林晚追查仓库线索", "required_characters": ["林晚"]}],
            "foreshadowing_plan": ["伏笔A", "伏笔B", "伏笔C"],
        },
        outline=outline,
        packet=None,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    assert normalized["foreshadowing_plan"] == ["伏笔A", "伏笔B"]


def test_normalize_plan_payload_enforces_outline_cast_and_emotional_plan() -> None:
    outline = ChapterOutline(
        chapter_number=3,
        title="账房风声",
        goal="玄昱追查账房旧案",
        pov_character_id="char_xuanyu",
        pov_character_name="玄昱",
        pov_character="玄昱",
        involved_character_ids=["char_xuanyu"],
        required_character_ids=["char_xuanyu"],
        involved_character_names=["玄昱"],
        involved_characters=["玄昱"],
        cast_plan={
            "pov_entity_id": "char_xuanyu",
            "required_character_ids": ["char_xuanyu"],
            "support_character_ids": [],
            "mention_only_entity_ids": [],
            "forbidden_active_character_ids": ["char_xuancang"],
        },
        emotional_plan={
            "subject_entity_id": "char_xuanyu",
            "entry_state": "玄昱压住焦躁",
            "pressure_source": "账房旧账逼近真相",
            "relationship_choice": "玄昱选择暂时保护证人",
            "turning_emotion": "警惕转为承担",
            "exit_aftertaste": "余疑未散",
            "expression_channels": ["action", "dialogue"],
        },
        setting="账房",
        expected_word_count=3000,
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "scene_intents": [
                {
                    "summary": "玄昱与玄苍查账",
                    "required_characters": ["玄昱", "玄苍"],
                    "pov_character": "玄苍",
                    "character_motivations": [
                        {"character": "玄昱", "motivation": "追查旧案", "stake": "失去线索"},
                        {"character": "玄苍", "motivation": "误导调查", "stake": "身份暴露"},
                    ],
                    "dialogue_voice_targets": {"玄昱": "短句", "玄苍": "诱导"},
                }
            ],
            "relationship_evolution": [],
            "emotional_arc": "",
        },
        outline=outline,
        packet=None,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    first_scene = normalized["scene_intents"][0]
    assert first_scene["required_characters"] == ["玄昱"]
    assert first_scene["pov_character"] == "玄昱"
    assert first_scene["character_motivations"] == [
        {"character": "玄昱", "motivation": "追查旧案", "stake": "失去线索"}
    ]
    assert first_scene["dialogue_voice_targets"] == {"玄昱": "短句"}
    assert first_scene["choice_pressure"] == "账房旧账逼近真相；玄昱选择暂时保护证人"
    assert first_scene["emotional_beat"] == "警惕转为承担"
    assert normalized["relationship_evolution"] == ["玄昱选择暂时保护证人"]
    assert (
        normalized["emotional_arc"] == "玄昱压住焦躁 → 账房旧账逼近真相 → 警惕转为承担 → 余疑未散"
    )


def test_build_character_identity_cards_prefers_personality_then_backstory() -> None:
    outline = ChapterOutline(
        chapter_number=1,
        title="开篇",
        goal="建立冲突",
        pov_character="风伏京",
        setting="皇城",
        expected_word_count=3200,
    )
    packet = ChapterStatePacket(
        chapter_number=1,
        chapter_outline=outline,
        canon_context={},
        character_profiles=[
            {
                "name": "赵元",
                "role": "supporting",
                "personality": "务实、强硬、铁面无私。他是皇城司指挥使，天子心腹。",
                "backstory": "出身寒门，历经三十余战。",
            },
            {
                "name": "苏令娴",
                "role": "supporting",
                "personality": "",
                "backstory": "太医院医女，擅长辨药与解毒。",
            },
        ],
    )

    cards = PlanChapterStep._build_character_identity_cards(packet)

    assert cards[0]["name"] == "赵元"
    assert "皇城司指挥使" in cards[0]["identity"]
    assert cards[1]["name"] == "苏令娴"
    assert "太医院医女" in cards[1]["identity"]


def test_normalize_plan_payload_rewrites_conflicting_opening_contract_when_transition_required() -> (
    None
):
    outline = ChapterOutline(
        chapter_number=3,
        title="禁室",
        goal="推进审问线索",
        pov_character="风伏京",
        setting="风府",
        expected_word_count=3000,
    )
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=outline,
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="戌末",
            location="辑异司门外",
            pov="风伏京",
        ),
        previous_chapter_ending="风伏京被押回耳房，门外两名衙役看守。",
        bridge=ChapterBridge(
            to_chapter=3,
            opening_time="亥初",
            opening_location="风府禁室",
            opening_pov="风伏京",
            action_handoff="押解队将风伏京从辑异司门外带往风府禁室。",
        ),
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "opening_contract": "开场即禁室审问，不得回溯或解释过程。",
            "scene_intents": [
                {
                    "summary": "禁室审问开始",
                    "required_characters": ["风伏京", "赵元"],
                    "required_outcome": "让审问迅速进入核心对抗。",
                }
            ],
        },
        outline=outline,
        packet=packet,
        min_beats=4,
        max_beats=8,
        beat_max_chars=120,
        style_profile=None,
    )

    assert "开场先交代角色如何从" in normalized["opening_contract"]
    first_scene = normalized["scene_intents"][0]
    assert first_scene["entry_state_refs"]
    assert "押解队将风伏京" in first_scene["entry_state_refs"][0]
    assert "离场/押解/脱身交代" in first_scene["required_outcome"]


def test_carry_forward_context_is_not_replayed_as_current_state_transition() -> None:
    outline = ChapterOutline(
        chapter_number=2,
        title="裂缝回应",
        goal="验证怀表异常",
        pov_character="林远",
        setting="废弃图书馆",
        expected_word_count=3000,
    )
    carry_forward = "怀表与裂缝之间的联系需要在下一章验证"
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=outline,
        canon_context={},
        must_carry_forward=[CarryForwardItem(text=carry_forward)],
    )

    normalized = PlanChapterStep._normalize_plan_payload(
        {
            "required_state_transitions": ["林远进入图书馆"],
            "scene_intents": [
                {
                    "summary": "林远进入图书馆检查裂缝。",
                    "required_outcome": "确认裂缝仍在扩张。",
                    "exit_target_state": "林远决定继续调查。",
                }
            ],
        },
        outline=outline,
        packet=packet,
        style_profile=None,
    )

    assert normalized["required_state_transitions"] == ["林远进入图书馆"]
    assert not any(
        carry_forward in scene["owned_state_changes"] for scene in normalized["scene_intents"]
    )
