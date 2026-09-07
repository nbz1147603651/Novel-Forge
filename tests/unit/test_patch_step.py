from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput
from novel_forge.prompts.builder import PromptBuilder


@pytest.mark.asyncio
async def test_patch_step_uses_dynamic_max_tokens_capped_by_config() -> None:
    captured: dict[str, object] = {}

    class _Builder:
        def build(self, task_type, context, *, max_tokens, temperature):
            captured["task_type"] = task_type
            captured["context"] = context
            captured["max_tokens"] = max_tokens
            captured["temperature"] = temperature
            return SimpleNamespace()

    class _Router:
        async def route(self, request):
            return SimpleNamespace(content='{"patches":[]}')

    settings = SimpleNamespace(
        patch_chapter_max_tokens=6144,
        patch_executor_version="v1",
        temp_patch_chapter=0.12,
    )
    step = ChapterPatchStep(_Router(), _Builder(), settings=settings)

    await step.run(
        PatchInput(
            chapter_number=4,
            chapter_text="第一段。\n\n第二段。",
            issues=[
                SimpleNamespace(
                    severity="medium",
                    location="第2段",
                    summary="重复信息需要压缩。",
                    fix_suggestion="删除重复句。",
                    issue_type="redundancy",
                    evidence_quote="第二段。",
                    fix_mode="replace",
                    paragraph_start=2,
                    paragraph_end=2,
                )
            ],
            cognitive_constraints=[
                {
                    "claim_id": "claim_patch",
                    "character_knowledge_coverage": {"林青": "partial"},
                }
            ],
        )
    )

    assert captured["task_type"] is TaskType.PATCH_CHAPTER
    assert 2048 <= int(captured["max_tokens"]) <= 6144
    assert captured["temperature"] == 0.12
    assert captured["context"]["cognitive_constraints"][0]["claim_id"] == "claim_patch"


@pytest.mark.asyncio
async def test_patch_step_rejects_invalid_patch_envelope_before_consumer() -> None:
    class _Builder:
        def build(self, task_type, context, *, max_tokens, temperature):
            return SimpleNamespace()

    class _Router:
        async def route(self, request):
            return SimpleNamespace(content='{"patches": 3}')

    settings = SimpleNamespace(
        patch_chapter_max_tokens=4096,
        patch_executor_version="v1",
        temp_patch_chapter=0.12,
    )
    step = ChapterPatchStep(_Router(), _Builder(), settings=settings)

    result = await step.run(
        PatchInput(
            chapter_number=4,
            chapter_text="第一段。\n\n第二段。",
            issues=[
                SimpleNamespace(
                    severity="medium",
                    location="第2段",
                    summary="重复信息需要压缩。",
                    fix_suggestion="删除重复句。",
                    issue_type="redundancy",
                    evidence_quote="第二段。",
                    fix_mode="replace",
                )
            ],
        )
    )

    assert result.revised_text == "第一段。\n\n第二段。"
    assert result.fallback is True
    assert result.patches_attempted == 0
    assert result.failure_kind == "internal"
    assert result.error_type
    assert result.failure_reason


@pytest.mark.asyncio
async def test_patch_step_normalizes_sparse_repair_directive_before_render() -> None:
    """Regression: 青瓦梦匙 chapter 6 failed before PATCH_CHAPTER reached the model."""

    captured: dict[str, object] = {}

    class _Router:
        def resolve_model_id_for_task(self, *_args, **_kwargs):
            return "mock-test"

        async def route(self, request):
            captured["request"] = request
            return SimpleNamespace(
                content=(
                    '{"patches":[{"original":"他用小螺丝刀旋开表后盖",'
                    '"replacement":"他隔着透明护罩检查怀表走时"}]}'
                )
            )

    settings = SimpleNamespace(
        patch_chapter_max_tokens=4096,
        patch_executor_version="v1",
        temp_patch_chapter=0.12,
    )
    step = ChapterPatchStep(_Router(), PromptBuilder(), settings=settings)
    result = await step.run(
        PatchInput(
            chapter_number=6,
            chapter_text="他用小螺丝刀旋开表后盖。",
            issues=[
                SimpleNamespace(
                    severity="critical",
                    location="",
                    summary="打开怀表后盖违反世界规则。",
                    fix_suggestion="只改写定位原句。",
                    issue_type="world_rule_conflict",
                    evidence_quote="他用小螺丝刀旋开表后盖",
                    fix_mode="replace",
                    repair_directive={
                        "repair_strategy": "保留检测结果，不得打开或摘下怀表。",
                        "required_context": ["怀表是唯一神经校准锚点。"],
                    },
                )
            ],
        )
    )

    assert "request" in captured
    assert result.fallback is False
    assert result.patches_applied == 1
    assert "透明护罩" in result.revised_text


@pytest.mark.asyncio
async def test_patch_step_can_propagate_failure_to_orchestration_policy() -> None:
    class _Builder:
        def build(self, *_args, **_kwargs):
            raise RuntimeError("prompt context exploded")

    settings = SimpleNamespace(patch_chapter_max_tokens=4096)
    step = ChapterPatchStep(object(), _Builder(), settings=settings)

    with pytest.raises(RuntimeError, match="prompt context exploded"):
        await step.run(
            PatchInput(
                chapter_number=6,
                chapter_text="原正文",
                issues=[],
                propagate_failure=True,
            )
        )


def test_patch_step_dynamic_budget_grows_with_issue_windows() -> None:
    settings = SimpleNamespace(patch_chapter_max_tokens=8192)
    step = ChapterPatchStep(object(), object(), settings=settings)
    small = [
        {"window_text": "短段。", "summary": "小修", "evidence_quote": "", "fix_mode": "replace"}
    ]
    large = [
        {
            "window_text": "长段。" * 500,
            "summary": "需要补强身份定位和承接逻辑。",
            "evidence_quote": "证据句。" * 80,
            "fix_mode": "replace",
        },
        {
            "window_text": "另一长段。" * 500,
            "summary": "需要删除重复信息。",
            "evidence_quote": "重复句。" * 80,
            "fix_mode": "replace",
        },
    ]

    assert step._compute_max_tokens(large) > step._compute_max_tokens(small)
