from __future__ import annotations

from collections.abc import Iterable

import pytest

from novel_forge.core.exceptions import PipelineError
from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short.stages.adaptive_revision import run_adaptive_short_revision
from novel_forge.pipeline.short_runner import ShortStoryRunner


def _text(label: str) -> str:
    return f"{label}\n\n" + "雨声落在窗上，主角在急诊室门口做出了选择。" * 18 + "他终于转身走进去。"


def _eval(*, continuity: float = 8.5, intent: float = 10.0) -> EvalReport:
    scores = [
        EvalScore(dimension="consistency", score=8.5, comment="设定稳定"),
        EvalScore(dimension="continuity", score=continuity, comment="承接可核验"),
        EvalScore(dimension="plot_progression", score=8.5, comment="有实质推进"),
        EvalScore(dimension="character", score=8.5, comment="人物稳定"),
        EvalScore(dimension="style", score=8.5, comment="风格稳定"),
        EvalScore(dimension="engagement", score=8.5, comment="有吸引力"),
        EvalScore(dimension="pacing", score=8.5, comment="节奏稳定"),
        EvalScore(dimension="intent_compliance", score=intent, comment="硬意图已保留"),
    ]
    overall = round(sum(item.score for item in scores) / len(scores), 2)
    return EvalReport(
        scores=scores,
        overall_score=overall,
        passed=overall >= 6.0 and intent >= 9.0,
        threshold=6.0,
        summary="评估完成。",
    )


def _inputs(tmp_storage, router, builder, runtime_settings, *, max_rounds: int = 2):  # type: ignore[no-untyped-def]
    runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=runtime_settings,
        max_edit_rounds=max_rounds,
    )
    layout = ProjectLayout(tmp_storage.ensure_project_dir(f"adaptive_{max_rounds}"))
    layout.ensure_dirs()
    draft = _text("初稿")
    tmp_storage.save_text(layout.short_draft_path(0), draft)
    spec = StorySpec(
        theme="选择与代价",
        genre="现实悬疑",
        tone="克制",
        length_target=500,
        ending_style="阶段性收束",
    )
    beats = StoryBeats.model_validate(
        {
            "beats": [
                {"sequence": 1, "summary": "主角面临选择", "tension_level": 4},
                {"sequence": 2, "summary": "主角承担结果", "tension_level": 7},
            ],
            "total_estimated_words": 500,
        }
    )
    execution_plan = {"completion_contract": {"resolution_target": "主角做出选择"}}
    return runner, layout, spec, beats, execution_plan, draft


def _sequence(values: Iterable[EvalReport]):
    queue = list(values)

    async def _fake_eval(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        if not queue:
            raise AssertionError("unexpected evaluation call")
        return queue.pop(0)

    return queue, _fake_eval


@pytest.mark.asyncio
async def test_clean_short_story_skips_all_edits_and_resume_reuses_checkpoint(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    eval_calls = 0
    edit_calls = 0

    async def _fake_eval(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal eval_calls
        del args, kwargs
        eval_calls += 1
        return _eval()

    async def _fake_edit(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal edit_calls
        del args, kwargs
        edit_calls += 1
        return _text("不应调用")

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", _fake_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    first = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )
    second = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert first.rounds_used == 0
    assert first.text == draft
    assert second.resumed is True
    assert eval_calls == 1
    assert edit_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reports", "expected_rounds"),
    [
        ([_eval(continuity=6.0), _eval()], 1),
        ([_eval(continuity=5.0), _eval(continuity=6.5), _eval()], 2),
    ],
)
async def test_adaptive_revision_uses_only_needed_rounds(
    monkeypatch,
    tmp_storage,
    router,
    builder,
    runtime_settings,
    reports,
    expected_rounds,
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings, max_rounds=10
    )
    _remaining, fake_eval = _sequence(reports)
    edits: list[int] = []

    async def _fake_edit(*args, iteration, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        edits.append(iteration)
        candidate = _text(f"修订{iteration}")
        tmp_storage.save_text(layout.short_draft_path(iteration), candidate)
        return candidate

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", fake_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    result = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert result.rounds_used == expected_rounds
    assert edits == list(range(1, expected_rounds + 1))
    assert result.unresolved_issues == ()


@pytest.mark.asyncio
async def test_regressed_candidate_is_rolled_back(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    _remaining, fake_eval = _sequence([_eval(continuity=6.5), _eval(continuity=4.0)])

    async def _fake_edit(*args, iteration, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        candidate = _text("退化修订")
        tmp_storage.save_text(layout.short_draft_path(iteration), candidate)
        return candidate

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", fake_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    result = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert result.rounds_used == 1
    assert result.text == draft
    assert result.eval_report.overall_score == _eval(continuity=6.5).overall_score
    assert result.unresolved_issues


@pytest.mark.asyncio
async def test_hard_intent_conflict_candidate_can_never_replace_validated_text(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    _remaining, fake_eval = _sequence(
        [_eval(continuity=6.0, intent=10.0), _eval(continuity=9.0, intent=2.0)]
    )

    async def _fake_edit(*args, iteration, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        candidate = _text("擅自改成悲剧结局并切换POV")
        tmp_storage.save_text(layout.short_draft_path(iteration), candidate)
        return candidate

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", fake_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    result = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert result.text == draft
    assert result.eval_report.scores[-1].score == 10.0


@pytest.mark.asyncio
async def test_zero_revision_limit_never_edits_even_when_issues_remain(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings, max_rounds=0
    )
    edit_calls = 0

    async def _fake_eval(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return _eval(continuity=4.0)

    async def _fake_edit(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal edit_calls
        del args, kwargs
        edit_calls += 1
        return _text("不应调用")

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", _fake_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    result = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert result.rounds_used == 0
    assert result.text == draft
    assert result.unresolved_issues
    assert edit_calls == 0


@pytest.mark.asyncio
async def test_untrustworthy_provider_evaluation_blocks_instead_of_silent_degrade(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings
    )

    async def _fallback_eval(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return _eval().model_copy(
            update={
                "is_fallback": True,
                "evaluation_status": "degraded",
                "fallback_reason": "provider_timeout",
            }
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation",
        _fallback_eval,
    )

    with pytest.raises(PipelineError, match="short_adaptive_revision"):
        await run_adaptive_short_revision(
            runner, layout, spec, beats, None, plan, draft
        )


@pytest.mark.asyncio
async def test_resume_after_edit_evaluates_candidate_without_editing_again(
    monkeypatch, tmp_storage, router, builder, runtime_settings
) -> None:  # type: ignore[no-untyped-def]
    runner, layout, spec, beats, plan, draft = _inputs(
        tmp_storage, router, builder, runtime_settings, max_rounds=1
    )
    eval_calls = 0
    edit_calls = 0

    async def _flaky_eval(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal eval_calls
        del args, kwargs
        eval_calls += 1
        if eval_calls == 1:
            return _eval(continuity=6.0)
        if eval_calls == 2:
            raise RuntimeError("provider interrupted")
        return _eval()

    async def _fake_edit(*args, iteration, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal edit_calls
        del args, kwargs
        edit_calls += 1
        candidate = _text("恢复候选")
        tmp_storage.save_text(layout.short_draft_path(iteration), candidate)
        return candidate

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_final_evaluation", _flaky_eval
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.adaptive_revision.run_adaptive_edit", _fake_edit
    )

    with pytest.raises(RuntimeError, match="provider interrupted"):
        await run_adaptive_short_revision(runner, layout, spec, beats, None, plan, draft)
    resumed = await run_adaptive_short_revision(
        runner, layout, spec, beats, None, plan, draft
    )

    assert resumed.text.startswith("恢复候选")
    assert resumed.rounds_used == 1
    assert edit_calls == 1
    assert eval_calls == 3
