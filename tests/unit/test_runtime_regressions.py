"""Regression tests for recently fixed runtime defects."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from novel_forge.common.plot_guard import (
    PlotGuardHandler as SharedPlotGuardHandler,
)
from novel_forge.common.plot_guard import (
    _run_plot_guard_judge,
)
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import PlotGuardDecision
from novel_forge.core.schemas.continuity import ChapterBridge
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.pronoun_check_step import (
    PronounCheckStep,
    check_pronoun_consistency,
)
from novel_forge.pipeline.steps.short_blueprint_step import ShortBlueprintStep
from novel_forge.prompts.builder import PromptBuilder


def test_cli_auto_runner_uses_shared_plot_guard_handler() -> None:
    from novel_forge.cli import chapter_runner as cli_chapter_runner

    assert cli_chapter_runner.PlotGuardHandler is SharedPlotGuardHandler


async def test_plot_guard_assist_mode_defaults_on_eof(tmp_path: Path) -> None:
    """Non-interactive CLI runs should apply the assist-mode default choice."""

    class EOFPrompt:
        @staticmethod
        def ask(*_args, **_kwargs) -> str:
            raise EOFError

    class RecordingConsole:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def print(self, *args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.messages.append(" ".join(str(arg) for arg in args))

    console = RecordingConsole()
    handler = SharedPlotGuardHandler(
        layout=ProjectLayout(tmp_path / "project"),
        storage=FileSystemStorage(tmp_path),
        router=ModelRouter({"mock": MockAdapter()}),
        builder=PromptBuilder(),
        settings=SimpleNamespace(long_ai_judge_apply_entity_actions=False),
        ai_judge_apply_mode="assist",
        auto_mode=False,
    )

    user_action, should_continue = await handler._apply_assist_mode(
        chapter_number=1,
        report=CreativeReport(),
        decision=PlotGuardDecision(decision="continue_with_constraints"),
        report_data={},
        console=console,
        prompt=EOFPrompt,
    )

    assert user_action == "apply_ai_assist_default"
    assert should_continue is True
    assert any("未检测到交互输入" in message for message in console.messages)


async def test_plot_guard_judge_parse_failure_uses_conservative_default(
    tmp_path: Path,
) -> None:
    """Malformed AI judge JSON must not fail checkpoint resolution."""

    class BrokenJudgeRouter:
        @staticmethod
        def output_limit_for_task(*_args, **_kwargs) -> int:
            return 1536

        @staticmethod
        async def route(_request: object) -> SimpleNamespace:
            return SimpleNamespace(content="not valid json")

    class StubBuilder:
        @staticmethod
        def build(*_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace()

    layout = ProjectLayout(tmp_path / "project")
    storage = FileSystemStorage(tmp_path)
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": 1,
            "chapters": [{"chapter_number": 1, "goal": "完成章节测试"}],
            "synopsis": "测试故事。",
        },
    )

    decision = await _run_plot_guard_judge(
        chapter_number=1,
        report=CreativeReport(),
        result=SimpleNamespace(
            alignment_report=None,
            continuity_report=None,
            causal_report=None,
            eval_report=None,
        ),
        layout=layout,
        storage=storage,
        router=BrokenJudgeRouter(),  # type: ignore[arg-type]
        builder=StubBuilder(),  # type: ignore[arg-type]
        settings=SimpleNamespace(
            long_ai_judge_max_context_chapters=2,
            long_ai_judge_max_tokens=1536,
            long_plot_guard_mode="ai_judge",
            temp_plot_guard_judge=0.2,
        ),
    )

    assert decision.decision == "continue_with_constraints"
    assert decision.risk_level == "medium"
    assert "JSON 无法解析" in decision.reasoning_brief


def test_plot_guard_normalizes_legacy_apply_mode(tmp_path: Path) -> None:
    handler = SharedPlotGuardHandler(
        layout=ProjectLayout(tmp_path / "project"),
        storage=FileSystemStorage(tmp_path),
        router=ModelRouter({"mock": MockAdapter()}),
        builder=PromptBuilder(),
        settings=SimpleNamespace(),
        ai_judge_apply_mode="accept",
        auto_mode=True,
    )

    assert handler.ai_judge_apply_mode == "trust"


async def test_check_pronoun_consistency_without_explicit_step_dependencies() -> None:
    """The convenience helper should not depend on PipelineStep internals."""

    result = await check_pronoun_consistency(
        chapter_text="她走进来。",
        chapter_plan={"pov_character": "小芳"},
        character_bible={"characters": [{"name": "小芳", "gender": "女"}]},
    )

    assert result["passed"] is True
    assert result["issues"] == []
    assert result["requires_rewrite"] is False


async def test_pronoun_check_ignores_wrong_pronouns_inside_chinese_dialogue(
    router,
    builder,
    runtime_settings,
) -> None:
    """Quoted speech should not trigger a false positive rewrite request."""

    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": "“小芳问：他，真的会来吗？”屋里没人接话。",
            "chapter_plan": {"pov_character": "小芳"},
            "chapter_number": 1,
            "character_bible": {"characters": [{"name": "小芳", "gender": "女"}]},
            "canon_context": {},
        }
    )

    assert result["passed"] is True
    assert result["issues"] == []
    assert result["requires_rewrite"] is False


async def test_pronoun_check_fast_path_when_no_pronouns_present(
    router,
    builder,
    runtime_settings,
) -> None:
    """Texts without pronoun characters should bypass deep scanning."""

    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": "小芳走进旧图书馆，灯光很暗，空气中有灰尘味。",
            "chapter_plan": {"pov_character": "小芳"},
            "chapter_number": 1,
            "character_bible": {"characters": [{"name": "小芳", "gender": "女"}]},
            "canon_context": {},
        }
    )

    assert result["passed"] is True
    assert result["issues"] == []
    assert result["requires_rewrite"] is False


async def test_pronoun_check_detects_pov_issue_when_character_not_named(
    router,
    builder,
    runtime_settings,
) -> None:
    """Name-light POV passages should still catch obvious flipped pronouns."""

    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": "他站在雨里，沉默很久。",
            "chapter_plan": {"pov_character": "小芳"},
            "chapter_number": 1,
            "character_bible": {"characters": [{"name": "小芳", "gender": "女"}]},
            "canon_context": {},
        }
    )

    assert result["passed"] is False
    assert result["pov_issues"] >= 1
    assert result["total_issues"] >= 1
    assert result["requires_rewrite"] is True


async def test_pronoun_check_prefers_character_bible_over_conflicting_canon_context(
    router,
    builder,
    runtime_settings,
) -> None:
    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": "沈照夜抬起眼，他没有再说话。",
            "chapter_plan": {"pov_character": "崔令仪", "involved_characters": ["沈照夜"]},
            "chapter_number": 81,
            "character_bible": {"characters": [{"name": "沈照夜", "gender": "男"}]},
            "canon_context": {"characters": {"沈照夜": {"gender": "女"}}},
        }
    )

    assert result["passed"] is True
    assert result["issues"] == []


async def test_pronoun_check_does_not_treat_object_pronoun_as_named_character_flip(
    router,
    builder,
    runtime_settings,
) -> None:
    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": (
                "陆云峥穿过人流，目光落在沈念卿身上。"
                "隔着签约台的灯光，他的眼神像是在问她：怎么了？她没有回答。"
            ),
            "chapter_plan": {
                "pov_character": "沈念卿",
                "involved_characters": ["陆云峥", "沈念卿"],
            },
            "chapter_number": 4,
            "character_bible": {
                "characters": [
                    {"name": "陆云峥", "gender": "男"},
                    {"name": "沈念卿", "gender": "女"},
                ]
            },
            "canon_context": {},
        }
    )

    assert result["passed"] is True
    assert result["issues"] == []


async def test_pronoun_check_ignores_object_pronouns_after_action_verbs(
    router,
    builder,
    runtime_settings,
) -> None:
    step = PronounCheckStep(router, builder, settings=runtime_settings)
    result = await step.run(
        {
            "chapter_text": (
                "陆云峥没有打断她，只把签约台上的钢笔推回原处。"
                "陆云峥走到了她身侧，压低声音提醒她别急。"
                "陆云峥的表情冷了几分；稍后他的目光越过她，看向门口。"
            ),
            "chapter_plan": {
                "pov_character": "沈念卿",
                "involved_characters": ["陆云峥", "沈念卿"],
            },
            "chapter_number": 4,
            "character_bible": {
                "characters": [
                    {"name": "陆云峥", "gender": "男"},
                    {"name": "沈念卿", "gender": "女"},
                ]
            },
            "canon_context": {},
        }
    )

    assert result["passed"] is True
    assert result["issues"] == []


def test_causal_validation_step_imports_chapter_bridge_from_continuity_schema() -> None:
    """Importing the step module should accept the continuity schema bridge model."""

    module = importlib.import_module("novel_forge.pipeline.steps.causal_validation_step")
    payload = module.CausalValidationInput(
        chapter_number=2,
        chapter_text="上一章的影响延续到了这一章。",
        chapter_bridge=ChapterBridge(to_chapter=2),
        causal_link={"previous_event": "上一章冲突", "causal_mechanism": "余波推动角色行动"},
    )

    assert payload.chapter_bridge.to_chapter == 2


def test_reading_power_report_restore_ignores_local_pipeline_metadata() -> None:
    """Persisted reading-power reports include metadata outside the Pydantic contract."""

    from novel_forge.pipeline.long.chapter_flow import _coerce_reading_power_report

    report = _coerce_reading_power_report(
        {
            "chapter": 3,
            "hook_type": "mystery",
            "hook_strength": "strong",
            "source_text_hash": "local-only",
            "pipeline_stage": "quality_stage",
        }
    )

    assert report is not None
    assert report.chapter == 3
    assert report.hook_type == "mystery"


async def test_short_story_runner_continues_when_blueprint_generation_fails(
    tmp_path: Path,
    runtime_settings,
    monkeypatch,
) -> None:
    """Blueprint failures should degrade to beats generation instead of aborting the run."""

    runner = ShortStoryRunner(
        ModelRouter({"mock": MockAdapter()}),
        PromptBuilder(),
        FileSystemStorage(tmp_path),
        settings=runtime_settings,
        max_edit_rounds=1,
    )

    async def fail_blueprint(_self, _spec):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(ShortBlueprintStep, "run", fail_blueprint)

    result = await runner.run(
        {
            "title": "测试故事",
            "genre": "fantasy",
            "theme": "勇气与牺牲",
            "tone": "epic",
            "length_target": 3000,
            "language": "zh",
        },
        project_id="blueprint_degrade",
    )

    assert result.blueprint is None
    assert result.final_text
    assert result.warnings == [
        "短篇叙事蓝图信息不足，本次将按精简蓝图继续生成，结构稳定性可能下降。"
    ]
    assert (tmp_path / "blueprint_degrade" / "chapters" / "short_story.md").exists()


async def test_short_story_runner_surfaces_creative_analysis_warning(
    tmp_path: Path,
    runtime_settings,
    monkeypatch,
) -> None:
    """Creative-analysis degradation should be visible in the final result warnings."""

    runner = ShortStoryRunner(
        ModelRouter({"mock": MockAdapter()}),
        PromptBuilder(),
        FileSystemStorage(tmp_path),
        settings=runtime_settings,
        max_edit_rounds=1,
    )

    async def no_creative_summary(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(runner, "_run_creative_analysis", no_creative_summary)

    result = await runner.run(
        {
            "title": "测试故事",
            "genre": "fantasy",
            "theme": "勇气与牺牲",
            "tone": "epic",
            "length_target": 3000,
            "language": "zh",
        },
        project_id="creative_warning",
    )

    assert result.final_text
    assert "短篇创作分析生成失败，正文已完成，但创作分析报告缺失。" in result.warnings
