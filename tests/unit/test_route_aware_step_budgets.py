from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, SceneIntent
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.gateway.types import ModelRequest
from novel_forge.narrative_state.schemas import CandidateStateDelta
from novel_forge.pipeline.long.services.generation import scene_writing
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    _claim_extraction_target_output_chars,
)
from novel_forge.pipeline.steps.editorial_check_step import EditorialCheckInput, EditorialCheckStep
from novel_forge.pipeline.steps.expression_observation_step import (
    ExpressionObservationInput,
    ExpressionObservationStep,
)
from novel_forge.pipeline.steps.state_adjudication_step import (
    StateDeltaAdjudicationInput,
    StateDeltaAdjudicationStep,
)
from novel_forge.pipeline.token_budget import (
    calculate_route_aware_max_tokens,
    normalize_task_max_tokens,
    route_bounded_json_output_budget,
)


class _LimitRouter:
    def __init__(self, limits: dict[TaskType, int]) -> None:
        self.limits = limits

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return self.limits.get(task_type, 8192)


class _CaptureBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskType, int, float]] = []

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        self.calls.append((task_type, max_tokens, temperature))
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _CaptureEditorialCheckStep(EditorialCheckStep):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_tokens_seen: int | None = None
        self.context_seen: dict[str, Any] | None = None

    async def _call_with_retry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.max_tokens_seen = int(kwargs["max_tokens"])
        self.context_seen = args[1]
        return {"summary": "ok", "findings": [], "revision_plan": [], "metrics": {}}


class _CaptureExpressionStep(ExpressionObservationStep):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_tokens_seen: int | None = None

    async def _call_with_retry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.max_tokens_seen = int(kwargs["max_tokens"])
        return {"observations": [], "skipped_reason": "", "source_text_hash": "ignored"}


class _CaptureStateDeltaStep(StateDeltaAdjudicationStep):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_tokens_seen: int | None = None

    async def _call_with_retry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.max_tokens_seen = int(kwargs["max_tokens"])
        return {
            "candidate_id": "c1",
            "verdict": "accept",
            "severity": "low",
            "rationale": "证据足够。",
        }


def _minimal_editorial_contract() -> EditorialContract:
    return EditorialContract.model_validate(
        {
            "project_title": "测试",
            "character_voices": [
                {
                    "character": "主角",
                    "sentence_profile": "短句。",
                    "explanation_bias": "少解释。",
                    "emotion_syntax": "紧张时句子变短。",
                    "signature_moves": ["停顿"],
                    "taboo_patterns": ["长篇独白"],
                    "sample_lines": ["先看证据。"],
                }
            ],
            "climax_markers": [
                {
                    "chapter_number": 10,
                    "climax_type": "main",
                    "description": "主线选择完成。",
                    "expected_aftermath_chapters": 1,
                }
            ],
            "denouement_budget": {
                "expected_chapters": 1,
                "max_confirmation_scenes": 1,
                "required_new_functions": ["余波后果"],
                "forbidden_repeats": ["重复确认"],
            },
            "theme_policies": ["主题通过行动呈现。"],
            "symbol_policies": [
                {
                    "symbol": "怀表",
                    "narrative_function": "承诺",
                    "explanation_policy": "explain_once",
                    "escalation_rule": "每次出现改变语境。",
                    "max_explicit_explanations": 1,
                }
            ],
            "scene_resistance_rules": [
                {
                    "scene_type": "对峙",
                    "required_resistance": "必须有阻力。",
                    "examples": ["门被挡住"],
                }
            ],
        }
    )


def test_bounded_json_budget_policy_caps_large_route_limits() -> None:
    router = _LimitRouter({TaskType.DERIVE_EDITORIAL_STRUCTURE: 65536})

    assert route_bounded_json_output_budget(router, TaskType.DERIVE_EDITORIAL_STRUCTURE) == 16384


def test_bounded_json_budget_policy_respects_smaller_route_limits() -> None:
    router = _LimitRouter({TaskType.DERIVE_EDITORIAL_STRUCTURE: 8192})

    assert route_bounded_json_output_budget(router, TaskType.DERIVE_EDITORIAL_STRUCTURE) == 8192


def test_route_aware_json_budget_uses_contract_floor_and_task_cap() -> None:
    router = _LimitRouter({TaskType.EVALUATE: 65536})

    assert (
        calculate_route_aware_max_tokens(
            router,
            TaskType.EVALUATE,
            1200,
            min_tokens=2048,
        )
        == 8192
    )


def test_route_aware_json_budget_respects_smaller_model_limit() -> None:
    router = _LimitRouter({TaskType.HUMANIZE_SCAN: 4096})

    assert (
        calculate_route_aware_max_tokens(
            router,
            TaskType.HUMANIZE_SCAN,
            1200,
            min_tokens=2048,
        )
        == 4096
    )


def test_normalize_task_max_tokens_protects_legacy_json_literals() -> None:
    router = _LimitRouter({TaskType.HUMANIZE_SCAN: 65536})

    assert normalize_task_max_tokens(router, TaskType.HUMANIZE_SCAN, 2048) == 8192


def test_normalize_task_max_tokens_uses_fragment_floor_without_required_contract() -> None:
    router = _LimitRouter({TaskType.BEATS: 65536})

    assert (
        normalize_task_max_tokens(
            router,
            TaskType.BEATS,
            1024,
            include_contract_required_keys=False,
        )
        == 4096
    )


@pytest.mark.asyncio
async def test_editorial_check_uses_routed_model_output_budget() -> None:
    step = _CaptureEditorialCheckStep(
        _LimitRouter({TaskType.CHECK_EDITORIAL: 32768}),
        None,
        settings=Settings(_env_file=None),
    )

    await step.run(
        EditorialCheckInput(
            chapter_number=1,
            chapter_text="主角按住怀表，停了一下。",
            editorial_contract=_minimal_editorial_contract(),
        )
    )

    assert step.max_tokens_seen == 32768


@pytest.mark.asyncio
async def test_editorial_check_passes_chapter_hard_boundaries() -> None:
    step = _CaptureEditorialCheckStep(
        _LimitRouter({TaskType.CHECK_EDITORIAL: 32768}),
        None,
        settings=Settings(_env_file=None),
    )

    await step.run(
        EditorialCheckInput(
            chapter_number=23,
            chapter_text="她把短片存成草稿，没有发布。",
            editorial_contract=_minimal_editorial_contract(),
            chapter_contract={
                "forbidden_changes": ["不得让沈鹿溪在本章完成最终发布，最终发布留给第24章"],
                "completion_criteria": ["完成最终剪辑，发布尚未发生"],
            },
            forbidden_reveal_boundaries=[{"rule": "不得让风能路灯在本章提前亮起，属第24章"}],
        )
    )

    assert step.context_seen is not None
    assert step.context_seen["chapter_contract"]["completion_criteria"] == [
        "完成最终剪辑，发布尚未发生"
    ]
    assert step.context_seen["forbidden_reveal_boundaries"] == [
        {"rule": "不得让风能路灯在本章提前亮起，属第24章"}
    ]


@pytest.mark.asyncio
async def test_expression_observation_uses_routed_model_output_budget() -> None:
    step = _CaptureExpressionStep(
        _LimitRouter({TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: 24576}),
        None,
        settings=Settings(_env_file=None),
    )

    await step.run(
        ExpressionObservationInput(
            chapter_number=1,
            chapter_text="她心口一紧，按住怀表。",
            expression_channel_profiles=[
                {
                    "channel_id": "body_signal",
                    "channel": "somatic_reaction",
                    "label": "身体反应",
                    "surface_forms": ["心口一紧"],
                    "trigger_contexts": ["高压"],
                    "risk_reason": "重复。",
                    "replacement_axes": ["动作"],
                    "allowed_when": "首次高压场景可少量使用。",
                    "confidence": 0.8,
                }
            ],
        )
    )

    assert step.max_tokens_seen == 24576


@pytest.mark.asyncio
async def test_state_delta_adjudication_uses_routed_model_output_budget() -> None:
    step = _CaptureStateDeltaStep(
        _LimitRouter({TaskType.ADJUDICATE_STATE_DELTA: 16384}),
        None,
        settings=Settings(_env_file=None),
    )

    await step.run(
        StateDeltaAdjudicationInput(
            chapter_number=1,
            candidate=CandidateStateDelta(
                candidate_id="c1",
                chapter_number=1,
                delta_type="event",
                summary="主角发现怀表异常。",
            ),
            chapter_contract={},
            current_state={},
            evidence_window="怀表忽然发烫。",
        )
    )

    assert step.max_tokens_seen == 16384


@pytest.mark.asyncio
async def test_scene_plan_validation_uses_routed_model_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _CaptureBuilder()

    async def fake_route_json_object_with_retry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"valid": True, "issues": []}

    monkeypatch.setattr(
        scene_writing.llm_h,
        "route_json_object_with_retry",
        fake_route_json_object_with_retry,
    )
    runner = SimpleNamespace(
        _builder=builder,
        _router=_LimitRouter({TaskType.VALIDATE_SCENE_PLAN: 12288}),
    )
    bundle = SimpleNamespace(chapter_outline=ChapterOutline(chapter_number=1, goal="测试"))
    bridge = ChapterBridge(to_chapter=1, action_handoff="主角抵达钟楼")
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="主角检查怀表。",
                required_outcome="发现怀表异常。",
                exit_target_state="开始追查。",
                draft_order=1,
            )
        ]
    )

    await scene_writing.validate_scene_plan_with_llm(
        runner=runner,
        bundle=bundle,
        bridge=bridge,
        plan=plan,
        chapter_number=1,
    )

    assert builder.calls == [(TaskType.VALIDATE_SCENE_PLAN, 12288, 0.0)]


def test_claim_extraction_target_scales_with_claim_limit() -> None:
    settings = SimpleNamespace(init_blueprint_holistic_claim_max_tokens=4096)

    assert (
        _claim_extraction_target_output_chars(
            settings,
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            6,
        )
        == 9600
    )
    assert (
        _claim_extraction_target_output_chars(
            settings,
            TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
            10,
        )
        == 16000
    )
