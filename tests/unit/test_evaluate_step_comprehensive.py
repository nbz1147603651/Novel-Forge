"""Tests for EvaluateStep — happy path, error path, and boundary conditions."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep
from tests.unit.conftest import _MockRouter

# ── Fixtures / fakes ─────────────────────────────────────────────────────


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


def _make_step(json_payload: dict) -> tuple[EvaluateStep, _FakeBuilder]:
    builder = _FakeBuilder()
    step = EvaluateStep(
        _MockRouter(json_payload=json_payload, completion_tokens=200),
        builder,
        settings=Settings(),
    )
    return step, builder


# ── Happy Path Tests ─────────────────────────────────────────────────────


async def test_evaluate_step_happy_path() -> None:
    payload = {
        "scores": [
            {"dimension": "consistency", "score": 8.0, "comment": "一致性好"},
            {"dimension": "continuity", "score": 7.0, "comment": "连贯性良好"},
            {"dimension": "plot_progression", "score": 7.5, "comment": "本章有明确推进"},
            {"dimension": "character", "score": 7.5, "comment": "角色塑造不错"},
            {"dimension": "style", "score": 7.0, "comment": "风格一致"},
            {"dimension": "engagement", "score": 8.0, "comment": "吸引力强"},
            {"dimension": "pacing", "score": 7.5, "comment": "节奏适中"},
        ],
        "overall_score": 7.5,
        "passed": True,
        "threshold": 6.0,
        "summary": "评估通过",
        "repair_suggestions": [],
    }
    step, _ = _make_step(payload)

    input_text = "这是一个测试用的故事文本，包含完整的情节和角色。"

    result = await step.run(input_text)

    assert isinstance(result, EvalReport)
    assert result.overall_score > 0
    assert result.rubric_version
    assert result.prompt_template_hash
    assert result.score_confidence == "normal"


async def test_evaluate_step_marks_uniform_scores_as_suspect() -> None:
    payload = {
        "scores": [
            {"dimension": "consistency", "score": 8.0, "comment": "整体较好"},
            {"dimension": "continuity", "score": 8.0, "comment": "整体较好"},
            {"dimension": "plot_progression", "score": 8.0, "comment": "整体较好"},
            {"dimension": "character", "score": 8.0, "comment": "整体较好"},
            {"dimension": "style", "score": 8.0, "comment": "整体较好"},
            {"dimension": "engagement", "score": 8.0, "comment": "整体较好"},
            {"dimension": "pacing", "score": 8.0, "comment": "整体较好"},
        ],
        "overall_score": 8.0,
        "passed": True,
        "threshold": 6.0,
        "summary": "评估通过",
        "repair_suggestions": [],
    }
    step, _ = _make_step(payload)

    result = await step.run("这是一个测试用的故事文本，包含完整的情节和角色。")

    assert result.score_confidence == "suspect"
    assert result.score_diagnostics["all_dimensions_same_score"] is True
    assert result.score_diagnostics["diagnostic_only"] is True


async def test_evaluate_step_short_text() -> None:
    payload = {
        "consistency": {"score": 5.0, "comment": "文本过短"},
        "continuity": {"score": 5.0, "comment": "文本过短"},
        "character": {"score": 5.0, "comment": "文本过短"},
        "style": {"score": 5.0, "comment": "文本过短"},
        "engagement": {"score": 5.0, "comment": "文本过短"},
        "pacing": {"score": 5.0, "comment": "文本过短"},
    }
    step, _ = _make_step(payload)

    result = await step.run("短")

    assert isinstance(result, EvalReport)
    assert "plot_progression" in {score.dimension for score in result.scores}


# ── Boundary Condition Tests ─────────────────────────────────────────────


async def test_evaluate_step_threshold_configurable() -> None:
    """EvaluateStep should accept custom threshold."""
    payload = {
        "overall_score": 6.5,
        "plot_score": 6.5,
        "character_score": 6.5,
        "prose_score": 6.5,
        "pacing_score": 6.5,
        "strengths": [],
        "weaknesses": [],
        "suggestions": [],
    }
    builder = _FakeBuilder()
    step = EvaluateStep(
        _MockRouter(json_payload=payload, completion_tokens=200),
        builder,
        settings=Settings(),
        threshold=7.0,
    )

    result = await step.run("测试文本")
    assert isinstance(result, EvalReport)


async def test_evaluate_step_logs_word_count_validation() -> None:
    """EvaluateStep should log word count validation results."""
    payload = {
        "overall_score": 7.0,
        "plot_score": 7.0,
        "character_score": 7.0,
        "prose_score": 7.0,
        "pacing_score": 7.0,
        "strengths": [],
        "weaknesses": [],
        "suggestions": [],
    }
    step, _ = _make_step(payload)

    result = await step.run("这是一个足够长的测试文本，用于验证字数检查功能是否正常工作。" * 100)

    assert isinstance(result, EvalReport)
