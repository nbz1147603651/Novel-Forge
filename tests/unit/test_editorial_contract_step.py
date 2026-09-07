from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.editorial_contract_step import (
    EditorialContractInput,
    EditorialContractStep,
    _normalize_character_voices,
    _normalize_element_directives,
    _normalize_revelation_ladder,
    _sanitize_element_id,
)
from novel_forge.prompts.builder import PromptBuilder


class _RecordingBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskType, dict[str, Any], int, float]] = []

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        self.calls.append((task_type, context, max_tokens, temperature))
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": task_type.value}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _EditorialRouter:
    def __init__(self) -> None:
        self.tasks: list[TaskType] = []
        self.output_limits = {
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: 32768,
            TaskType.DERIVE_EDITORIAL_STRUCTURE: 65536,
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: 65536,
            TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: 32768,
        }

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return self.output_limits.get(task_type, 8192)

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "mock-model"

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task_type)
        payload = {
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: {
                "character_voices": [
                    {
                        "character": "主角甲",
                        "sentence_profile": "短句，先结论后补证据。",
                        "explanation_bias": "少解释，用行动代替自白。",
                        "emotion_syntax": "情绪升高时句子更短。",
                        "signature_moves": ["先看事实", "用反问截断"],
                        "taboo_patterns": ["长篇命运独白"],
                        "sample_lines": ["先看证据。"],
                    }
                ]
            },
            TaskType.DERIVE_EDITORIAL_STRUCTURE: {
                "climax_markers": [
                    {
                        "chapter_number": 40,
                        "climax_type": "main",
                        "description": "主线摊牌与核心选择完成。",
                        "expected_aftermath_chapters": 6,
                    }
                ],
                "denouement_budget": {
                    "expected_chapters": 6,
                    "max_confirmation_scenes": 2,
                    "required_new_functions": ["余波后果", "关系制度化"],
                    "forbidden_repeats": ["重复圆满确认"],
                },
                "revelation_ladder": [
                    {
                        "thread": "信物线",
                        "stage": "物证",
                        "stage_order": 1,
                        "target_chapter": 20,
                        "trigger": "怀表刻字",
                        "allowed_disclosure": "只确认怀表不是偶然出现。",
                        "required_action_consequence": "主角主动查证来源。",
                    }
                ],
                "time_bridge_policies": ["跨月转场必须明确标注。"],
                "title_policy": {
                    "max_reuse": 2,
                    "allowed_repeated_titles": ["核心回环标题"],
                    "naming_strategy": "重复标题只保留核心回环。",
                },
            },
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: {
                "theme_policies": ["主题通过选择和后果呈现。"],
                "symbol_policies": [
                    {
                        "symbol": "怀表",
                        "narrative_function": "时间与承诺",
                        "explanation_policy": "explain_once",
                        "escalation_rule": "每次出现改变语境。",
                        "max_explicit_explanations": 1,
                    }
                ],
                "scene_resistance_rules": [
                    {
                        "scene_type": "对峙",
                        "required_resistance": "必须有空间或流程阻力。",
                        "examples": ["门被挡住"],
                    }
                ],
                "expression_channel_budget": {
                    "somatic_reaction": 1,
                    "action_tag": 2,
                    "dialogue_tag": 2,
                    "sensory_anchor": 3,
                },
                "expression_channel_profiles": [
                    {
                        "channel_id": "contract_body_reaction",
                        "channel": "somatic_reaction",
                        "label": "合同压力下的身体反应",
                        "surface_forms": ["心口一紧", "指尖发凉"],
                        "trigger_contexts": ["对峙", "签约"],
                        "risk_reason": "容易把所有压力都写成同一身体反应。",
                        "replacement_axes": ["物件操作", "对白停顿"],
                        "allowed_when": "核心摊牌或首次确认风险时可少量保留。",
                        "cooldown_chapters": 3,
                        "actor_scope": "global",
                        "confidence": 0.82,
                    }
                ],
                "body_signal_budget_per_high_emotion_scene": 1,
                "forbidden_confirmation_phrases": ["核心主题句"],
                "revision_priorities": ["先压结构", "再分声纹"],
            },
            TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: {
                "editorial_element_directives": [
                    {
                        "element_id": "editorial_denouement_budget",
                        "element_name": "高潮后余波预算",
                        "directive_type": "压缩余波",
                        "target_window": "主高潮后",
                        "linked_characters": [],
                        "requirement": "余波章节必须承担新功能。",
                        "success_criteria": "读者能区分余波与终场。",
                    }
                ]
            },
        }[request.task_type]
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            cost_usd=0.0,
        )


class _DriftyEditorialRouter(_EditorialRouter):
    async def route(self, request: ModelRequest) -> ModelResponse:
        if request.task_type != TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES:
            return await super().route(request)
        self.tasks.append(request.task_type)
        payload = {
            "character_voices": [
                {
                    "character": "林晚",
                    "sentence_profile": "短促，先观察再补一句。",
                    "explanation_bias": "用数据报告绕开情绪。",
                    "emotion_syntax": ["情绪升高时咬笔", "眼睛睁大伴随歪头"],
                    "taboo_patterns": ["成熟冷静的长篇议论"],
                    "sample_lines": ["老师，这组数据不太对。"],
                },
                {
                    "character": "周正阳",
                    "sentence_profile": "直接质询，少铺垫。",
                    "explanation_bias": "先给判断，再追问证据。",
                    "emotion_syntax": ["质疑时摘下眼镜", "下眼镜直接看人"],
                    "sample_lines": ["你的实验思路有问题。"],
                },
            ]
        }
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            cost_usd=0.0,
        )


class _RangeDriftEditorialRouter(_EditorialRouter):
    async def route(self, request: ModelRequest) -> ModelResponse:
        if request.task_type != TaskType.DERIVE_EDITORIAL_STRUCTURE:
            return await super().route(request)
        self.tasks.append(request.task_type)
        payload = {
            "climax_markers": [
                {
                    "chapter_number": 70,
                    "climax_type": "main",
                    "description": "终局选择与尾声意象同时落地。",
                    "expected_aftermath_chapters": [65, 70],
                },
                {
                    "chapter_number": 50,
                    "climax_type": "subplot",
                    "description": "支线清算完成。",
                    "expected_aftermath_chapters": [55, 60],
                },
                {
                    "chapter_number": 30,
                    "climax_type": "emotional",
                    "description": "关系后果落地。",
                    "expected_aftermath_chapters": "35-40",
                },
            ],
            "denouement_budget": {
                "expected_chapters": 0,
                "max_confirmation_scenes": 2,
                "required_new_functions": ["余波后果"],
                "forbidden_repeats": ["重复圆满确认"],
            },
            "revelation_ladder": [
                {
                    "thread": "信物线",
                    "stage": "终局",
                    "stage_order": 1,
                    "target_chapter": 60,
                    "trigger": "信物共鸣",
                    "allowed_disclosure": "只释放终局必要信息。",
                    "required_action_consequence": "角色必须作出选择。",
                }
            ],
            "time_bridge_policies": ["跨月转场必须明确标注。"],
            "title_policy": {
                "max_reuse": 2,
                "allowed_repeated_titles": ["核心回环标题"],
                "naming_strategy": "重复标题只保留核心回环。",
            },
        }
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            cost_usd=0.0,
        )


class _PolicyObjectEditorialRouter(_EditorialRouter):
    async def route(self, request: ModelRequest) -> ModelResponse:
        if request.task_type not in {
            TaskType.DERIVE_EDITORIAL_STRUCTURE,
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        }:
            return await super().route(request)
        response = await super().route(request)
        payload = json.loads(response.content)
        if request.task_type == TaskType.DERIVE_EDITORIAL_STRUCTURE:
            payload["time_bridge_policies"] = [
                {"policy": "现代线章节跨月需标注具体月份与事件节点。"},
                {"policy": "民国记忆闪回不按线性日历推算。"},
            ]
        else:
            payload["theme_policies"] = [
                {"policy": "主题必须通过行动后果呈现。"},
            ]
            payload["forbidden_confirmation_phrases"] = [
                {"phrase": "不应采用该键", "policy": "禁止反复确认圆满。"},
            ]
            payload["revision_priorities"] = [
                {"rule": "先压结构，再分声纹。"},
            ]
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id=response.model_id,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
        )


class _TitlePolicyDriftEditorialRouter(_EditorialRouter):
    async def route(self, request: ModelRequest) -> ModelResponse:
        if request.task_type != TaskType.DERIVE_EDITORIAL_STRUCTURE:
            return await super().route(request)
        response = await super().route(request)
        payload = json.loads(response.content)
        payload["title_policy"] = {
            "max_reuse": 0,
            "allowed_titles": [{"title": "残玉归心"}, "月下残局"],
            "naming_strategy": "全书标题尽量唯一，只有首尾回环可复用。",
        }
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id=response.model_id,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
        )


class _DenouementDescriptionDriftEditorialRouter(_EditorialRouter):
    async def route(self, request: ModelRequest) -> ModelResponse:
        response = await super().route(request)
        if request.task_type != TaskType.DERIVE_EDITORIAL_STRUCTURE:
            return response
        payload = json.loads(response.content)
        payload["denouement_budget"]["forbidden_repeats_description"] = (
            "结尾一章只能完成一项收束，不得新开反派动作。"
        )
        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id=response.model_id,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd,
        )


def _settings() -> Settings:
    return Settings.model_validate({"temp_plan_outline": 0.7})


def test_editorial_contract_step_splits_parallel_parts_and_merges_contract() -> None:
    router = _EditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    contract = asyncio.run(
        step.run(
            EditorialContractInput(
                title="测试长篇",
                total_chapters=61,
                story_bible={"synopsis": "核心梗概" * 200, "unused_blob": "x" * 10000},
                character_bible={
                    "characters": [
                        {
                            "name": "主角甲",
                            "role": "protagonist",
                            "personality": "谨慎" * 400,
                            "unused_blob": "x" * 10000,
                        }
                    ]
                    * 12
                },
                style_profile={
                    "summary": "克制细腻",
                    "modules": [{"name": "模块", "rules": ["短"]}],
                },
                blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 40}]},
                blueprint_elements={
                    "extension_elements": [
                        {
                            "element_id": "editorial_denouement_budget",
                            "name": "高潮后余波预算",
                            "prompt_hint": "压缩余波。",
                            "unused_blob": "x" * 10000,
                        }
                    ]
                },
            )
        )
    )

    assert isinstance(contract, EditorialContract)
    assert contract.project_title == "测试长篇"
    assert contract.main_climax().chapter_number == 40
    assert contract.editorial_element_directives[0].element_id == "editorial_denouement_budget"
    assert contract.expression_channel_profiles[0].channel_id == "contract_body_reaction"
    assert contract.expression_channel_profiles[0].replacement_axes == ["物件操作", "对白停顿"]

    expected_tasks = {
        TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
    }
    assert set(router.tasks) == expected_tasks
    assert TaskType.DERIVE_EDITORIAL_CONTRACT not in router.tasks
    # Editorial derivation parts use the shared bounded-JSON budget policy.
    # Mock router returns 32K/65K; expected = min(router, policy cap 16K).
    assert [call[2] for call in builder.calls] == [16384, 16384, 16384, 16384]
    assert all(call[3] == 0.1 for call in builder.calls)

    voice_context = next(
        context
        for task_type, context, _, _ in builder.calls
        if task_type == TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES
    )
    assert "unused_blob" not in json.dumps(voice_context, ensure_ascii=False)
    assert len(voice_context["character_bible"]["characters"]) == 1
    assert voice_context["voice_character_whitelist"] == ["主角甲"]


def test_normalize_character_voices_repairs_list_text_and_missing_signature_moves() -> None:
    voices = _normalize_character_voices(
        [
            {
                "character": "林晚",
                "sentence_profile": "短句。",
                "explanation_bias": "先问事实。",
                "emotion_syntax": ["紧张时咬笔", "眼睛睁大"],
                "taboo_patterns": "长篇自白",
                "sample_lines": "老师，数据不对。",
            }
        ]
    )

    assert voices[0]["emotion_syntax"] == "紧张时咬笔；眼睛睁大"
    assert voices[0]["signature_moves"] == ["林晚保持可识别的动作或句法习惯"]
    assert voices[0]["taboo_patterns"] == ["长篇自白"]
    assert voices[0]["sample_lines"] == ["老师，数据不对。"]


def test_normalize_character_voices_defaults_empty_required_text_fields() -> None:
    voices = _normalize_character_voices(
        [
            {
                "character": "林晚",
                "sentence_profile": [],
                "explanation_bias": [],
                "emotion_syntax": [],
            }
        ]
    )

    assert voices[0]["sentence_profile"] == "林晚的句式保持清晰可辨。"
    assert voices[0]["explanation_bias"] == "林晚通过行动和细节表达立场。"
    assert voices[0]["emotion_syntax"] == "林晚的情绪通过句法变化而非直白陈述表达。"


def test_normalize_character_voices_drops_unmatched_and_does_not_backfill() -> None:
    """Regression: silent backfill with _default_character_voice placeholders
    produced fake-looking "句式保持清晰可辨" entries in the UI. The contract
    now requires LLM to use exact names from the bible whitelist; if a name
    doesn't resolve, the entry is dropped, never backfilled with a placeholder.
    """
    voices = _normalize_character_voices(
        [
            {
                "character": "周雨薇",
                "sentence_profile": "短句。",
                "explanation_bias": "少解释。",
                "emotion_syntax": "情绪升高时停顿。",
                "signature_moves": ["垂眼"],
            },
            {
                "character": "林素素",
                "sentence_profile": "快句。",
                "explanation_bias": "先报事实。",
                "emotion_syntax": "紧张时连珠炮。",
                "signature_moves": ["推眼镜"],
            },
        ],
        valid_characters=["沈念卿", "陆云峥", "林素素"],
    )

    assert [item["character"] for item in voices] == ["林素素"]
    assert voices[0]["sentence_profile"] == "快句。"
    assert voices[0]["signature_moves"] == ["推眼镜"]


def test_normalize_character_voices_backfills_exact_missing_source_profiles() -> None:
    voices = _normalize_character_voices(
        [],
        valid_characters=["苏晚", "阿荞"],
        character_profiles=[
            {"name": "苏晚", "voice": "温柔短句，以符号暗示真相。", "personality": "克制。"},
            {"name": "阿荞", "voice": "低声慢语，只回答必要信息。", "personality": "谨慎。"},
        ],
    )

    assert [item["character"] for item in voices] == ["苏晚", "阿荞"]
    assert voices[0]["sentence_profile"] == "温柔短句，以符号暗示真相。"
    assert voices[1]["explanation_bias"] == "谨慎。"


def test_resolve_voice_character_name_rejects_ambiguous_ties() -> None:
    """Regression: 玄琰 must NOT resolve to 玄苍 (or 玄昱) when multiple
    candidates tie at SequenceMatcher.ratio()=0.5. Better to drop the entry
    than to silently misroute it to a different character."""
    from novel_forge.pipeline.steps.editorial_contract_step import (
        _resolve_voice_character_name,
    )

    bible = ["玄昱", "玄苍", "沈清漪", "鬼蛹", "商漪", "宇文铎"]
    assert _resolve_voice_character_name("玄琰", bible) == ""
    assert _resolve_voice_character_name("鬼目", bible) == "鬼蛹"
    assert _resolve_voice_character_name("宇文钧", bible) == "宇文铎"
    assert _resolve_voice_character_name("沈清漪", bible) == "沈清漪"
    assert _resolve_voice_character_name("无此人", bible) == ""


def test_normalize_revelation_ladder_repairs_text_and_number_drift() -> None:
    ladder = _normalize_revelation_ladder(
        [
            {
                "thread": ["信物线", "暗线"],
                "stage": [],
                "order": "2",
                "chapter": "15",
                "trigger": ["怀表", "旧照片"],
                "allowed_disclosure": [],
                "required_action_consequence": ["主角追查来源"],
                "rationale": "模型解释不应透传。",
                "evidence_based": "输入证据不属于契约字段。",
            }
        ]
    )

    assert ladder[0]["thread"] == "信物线；暗线"
    assert ladder[0]["stage"] == "第1级阶段揭示"
    assert ladder[0]["stage_order"] == 2
    assert ladder[0]["target_chapter"] == 15
    assert ladder[0]["trigger"] == "怀表；旧照片"
    assert ladder[0]["allowed_disclosure"] == "第1级只释放本级必要信息"
    assert ladder[0]["required_action_consequence"] == "主角追查来源"
    assert "rationale" not in ladder[0]
    assert "evidence_based" not in ladder[0]


def test_normalize_element_directives_repairs_aliases_and_missing_fields() -> None:
    directives = _normalize_element_directives(
        [
            {
                "element": "editorial_denouement_budget",
                "name": "高潮后余波预算",
                "directive": ["压缩余波", "避免重复确认"],
                "window": [],
                "linked_characters": "主角甲",
                "rule": [],
                "success": ["读者能看见新后果"],
                "unexpected_field": "不应透传",
            },
            {
                "requirement": "场景必须有阻力。",
                "success_criteria": "冲突推进。",
                "linked_charifiers": ["主角乙", "配角丙"],
            },
        ]
    )

    assert directives[0]["element_id"] == "editorial_denouement_budget"
    assert directives[0]["element_name"] == "高潮后余波预算"
    assert directives[0]["directive_type"] == "压缩余波；避免重复确认"
    assert directives[0]["target_window"] == "全书适用"
    assert directives[0]["linked_characters"] == ["主角甲"]
    assert directives[0]["requirement"] == "该指令需要在具体场景中落实"
    assert directives[0]["success_criteria"] == "读者能看见新后果"
    assert "unexpected_field" not in directives[0]
    assert directives[1]["element_id"] == "unknown_editorial_element_2"
    assert directives[1]["linked_characters"] == ["主角乙", "配角丙"]


def test_sanitize_element_id_strips_stray_colon_from_known_library_element() -> None:
    # Regression: JSON repair could corrupt ``horror_dread_rhythm`` to
    # ``horror_dread_rhy:thm``; the defensive layer must restore it.
    assert _sanitize_element_id("horror_dread_rhy:thm") == "horror_dread_rhythm"
    assert _sanitize_element_id("i:nformation_asymmetry") == "information_asymmetry"
    assert _sanitize_element_id("rela:tionship_trust_arc") == "relationship_trust_arc"
    assert _sanitize_element_id("edi:torial_revelation_ladder") == "editorial_revelation_ladder"


def test_sanitize_element_id_preserves_genuine_unknowns_and_clean_ids() -> None:
    # A genuine unknown that merely contains a colon must not be rewritten,
    # so the validator still reports a real ``editorial_element_unknown``.
    assert _sanitize_element_id("totally_made_up:id") == "totally_made_up:id"
    # Clean known ids are returned unchanged.
    assert _sanitize_element_id("horror_dread_rhythm") == "horror_dread_rhythm"
    # Whitespace is trimmed.
    assert _sanitize_element_id("  horror_dread_rhythm  ") == "horror_dread_rhythm"


def test_normalize_element_directives_cleans_corrupted_element_ids() -> None:
    # End-to-end: a corrupted element_id flowing through normalization is
    # restored to the real library id before validation sees it.
    directives = _normalize_element_directives(
        [
            {"element_id": "horror_dread_rhy:thm", "element_name": "恐惧节律"},
            {"element_id": "real_unknown_id", "element_name": "未知"},
        ]
    )
    assert directives[0]["element_id"] == "horror_dread_rhythm"
    # Genuine unknown preserved so the validator can flag it honestly.
    assert directives[1]["element_id"] == "real_unknown_id"


def test_editorial_contract_step_rejects_character_voice_schema_drift() -> None:
    router = _DriftyEditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    with pytest.raises(ValueError, match="JSON schema validation failed"):
        asyncio.run(
            step.run(
                EditorialContractInput(
                    title="测试长篇",
                    total_chapters=61,
                    story_bible={"synopsis": "核心梗概"},
                    character_bible={"characters": [{"name": "林晚"}, {"name": "周正阳"}]},
                    blueprint={"synopsis": "蓝图"},
                    blueprint_elements={},
                )
            )
        )


def test_editorial_contract_step_repairs_climax_aftermath_range_drift() -> None:
    router = _RangeDriftEditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    contract = asyncio.run(
        step.run(
            EditorialContractInput(
                title="测试长篇",
                total_chapters=70,
                story_bible={"synopsis": "核心梗概"},
                character_bible={"characters": [{"name": "主角甲"}]},
                blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 60}]},
                blueprint_elements={},
            )
        )
    )

    assert contract.main_climax().chapter_number == 60
    assert contract.main_climax().expected_aftermath_chapters == 2
    assert contract.climax_markers[1].expected_aftermath_chapters == 10
    assert contract.climax_markers[2].expected_aftermath_chapters == 10


def test_editorial_contract_step_repairs_denouement_description_drift() -> None:
    router = _DenouementDescriptionDriftEditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    contract = asyncio.run(
        step.run(
            EditorialContractInput(
                title="测试长篇",
                total_chapters=61,
                story_bible={"synopsis": "核心梗概"},
                character_bible={"characters": [{"name": "主角甲"}]},
                blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 40}]},
                blueprint_elements={},
            )
        )
    )

    assert contract.denouement_budget.forbidden_repeats == [
        "重复圆满确认",
        "结尾一章只能完成一项收束，不得新开反派动作。",
    ]
    assert router.tasks.count(TaskType.DERIVE_EDITORIAL_STRUCTURE) == 1


def test_editorial_contract_step_repairs_policy_object_lists() -> None:
    router = _PolicyObjectEditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    contract = asyncio.run(
        step.run(
            EditorialContractInput(
                title="测试长篇",
                total_chapters=61,
                story_bible={"synopsis": "核心梗概"},
                character_bible={"characters": [{"name": "主角甲"}]},
                blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 40}]},
                blueprint_elements={},
            )
        )
    )

    assert contract.time_bridge_policies == [
        "现代线章节跨月需标注具体月份与事件节点。",
        "民国记忆闪回不按线性日历推算。",
    ]
    assert contract.theme_policies == ["主题必须通过行动后果呈现。"]
    assert contract.forbidden_confirmation_phrases == ["禁止反复确认圆满。"]
    assert contract.revision_priorities == ["先压结构，再分声纹。"]


def test_editorial_contract_step_repairs_title_policy_no_reuse_drift() -> None:
    router = _TitlePolicyDriftEditorialRouter()
    builder = _RecordingBuilder()
    step = EditorialContractStep(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        settings=_settings(),
    )

    contract = asyncio.run(
        step.run(
            EditorialContractInput(
                title="弈心锁玉",
                total_chapters=120,
                story_bible={"synopsis": "核心梗概"},
                character_bible={"characters": [{"name": "主角甲"}]},
                blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 88}]},
                blueprint_elements={},
            )
        )
    )

    assert contract.title_policy.max_reuse == 1
    assert contract.title_policy.allowed_repeated_titles == ["残玉归心", "月下残局"]
    assert contract.title_policy.naming_strategy == "全书标题尽量唯一，只有首尾回环可复用。"
