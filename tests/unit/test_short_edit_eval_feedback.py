"""Tests for short edit context enrichment with evaluation feedback."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.draft import EditResult
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore, RepairSuggestion
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.edit_step import EditStep


@pytest.mark.asyncio
async def test_completion_repair_receives_eval_feedback(
    monkeypatch: pytest.MonkeyPatch,
    router,
    builder,
    tmp_storage,
    runtime_settings,
) -> None:
    runner = ShortStoryRunner(router, builder, tmp_storage, settings=runtime_settings, max_edit_rounds=1)
    layout = ProjectLayout(tmp_storage.ensure_project_dir("short_eval_feedback"))
    layout.ensure_dirs()

    spec = StorySpec(
        title="旧站台",
        genre="mystery",
        theme="真相与代价",
        tone="克制",
        length_target=1800,
        language="zh",
        conflict_hint="主角必须在暴雨前读完那封信",
    )
    beats = StoryBeats.model_validate(
        {
            "beats": [
                {"sequence": 1, "summary": "主角收到信。", "tension_level": 3},
                {"sequence": 2, "summary": "主角做出选择。", "tension_level": 7},
            ],
            "total_estimated_words": 1800,
        }
    )
    eval_report = EvalReport(
        scores=[
            EvalScore(dimension="consistency", score=6.2, comment="因果交代偏弱"),
            EvalScore(dimension="style", score=7.6, comment="语言稳定"),
        ],
        overall_score=6.9,
        passed=True,
        threshold=6.0,
        summary="结尾收束略快，建议补足动作承接。",
        repair_suggestions=[
            RepairSuggestion(
                issue="结尾落点不足",
                location="最后一段",
                suggestion="补一段明确的情绪落点与动作回收",
                priority="high",
                dimension="continuity",
            )
        ],
    )
    execution_plan = {
        "completion_contract": {
            "core_question": "主角是否会拆信",
            "resolution_target": "主角在雨夜做出决定并收束",
        }
    }

    captured: dict[str, object] = {}

    async def _fake_run(self, input_data):  # noqa: ANN001
        captured["context"] = input_data.context
        return EditResult(revised_text="修复后正文", edit_notes=[], iteration=input_data.iteration)

    monkeypatch.setattr(EditStep, "run", _fake_run)

    await runner._run_completion_repair(
        layout=layout,
        spec=spec,
        beats=beats,
        blueprint=None,
        execution_plan=execution_plan,
        current_text="原始正文",
        issues=["结尾未形成完整收束"],
        pass_label="short_completion_repair_after_eval",
        iteration=3,
        eval_report=eval_report,
    )

    ctx = captured["context"]
    assert isinstance(ctx, dict)
    assert "eval_feedback" in ctx
    feedback = ctx["eval_feedback"]
    assert isinstance(feedback, dict)
    assert feedback["overall_score"] == 6.9
    assert feedback["low_dimensions"][0]["dimension"] == "consistency"
    assert feedback["repair_suggestions"][0]["issue"] == "结尾落点不足"


@pytest.mark.asyncio
async def test_quality_repair_receives_targeted_focus_issues(
    monkeypatch: pytest.MonkeyPatch,
    router,
    builder,
    tmp_storage,
    runtime_settings,
) -> None:
    runner = ShortStoryRunner(router, builder, tmp_storage, settings=runtime_settings, max_edit_rounds=1)
    layout = ProjectLayout(tmp_storage.ensure_project_dir("short_quality_feedback"))
    layout.ensure_dirs()

    spec = StorySpec(
        title="旧站台",
        genre="mystery",
        theme="真相与代价",
        tone="克制",
        length_target=1800,
        language="zh",
        conflict_hint="主角必须在暴雨前读完那封信",
    )
    beats = StoryBeats.model_validate(
        {
            "beats": [
                {"sequence": 1, "summary": "主角收到信。", "tension_level": 3},
                {"sequence": 2, "summary": "主角做出选择。", "tension_level": 7},
            ],
            "total_estimated_words": 1800,
        }
    )
    eval_report = EvalReport(
        scores=[
            EvalScore(dimension="continuity", score=6.5, comment="结尾收束略急"),
            EvalScore(dimension="style", score=6.8, comment="氛围还可更集中"),
        ],
        overall_score=6.9,
        passed=True,
        threshold=6.0,
        summary="主题回应偏弱，尾段余韵不足。",
        repair_suggestions=[
            RepairSuggestion(
                issue="尾段回响不足",
                location="最后一段",
                suggestion="补一处动作或意象呼应主题",
                priority="high",
                dimension="continuity",
            )
        ],
    )

    captured: dict[str, object] = {}

    async def _fake_run(self, input_data):  # noqa: ANN001
        captured["context"] = input_data.context
        return EditResult(revised_text="修复后正文", edit_notes=[], iteration=input_data.iteration)

    monkeypatch.setattr(EditStep, "run", _fake_run)

    await runner._run_quality_repair(
        layout=layout,
        spec=spec,
        beats=beats,
        blueprint=None,
        execution_plan={},
        current_text="原始正文",
        issues=["最后一段：尾段回响不足；补一处动作或意象呼应主题"],
        pass_label="short_quality_repair_after_eval",
        iteration=3,
        eval_report=eval_report,
    )

    ctx = captured["context"]
    assert isinstance(ctx, dict)
    assert ctx["repair_focus_issues"][0].startswith("最后一段")
    assert "主题回应" in ctx["repair_warning"]
    assert "eval_feedback" in ctx
