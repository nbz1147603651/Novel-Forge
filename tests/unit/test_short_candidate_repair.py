from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import RepairCaseStore
from novel_forge.pipeline.repair_orchestration.domains.short_story import (
    run_short_story_candidate_repair,
)
from novel_forge.pipeline.short.stages.adaptive_revision import diagnose_short_revision
from novel_forge.pipeline.short.stages.evaluate import run_final_evaluation
from novel_forge.pipeline.short_runner import ShortStoryRunner


def _text(label: str) -> str:
    return f"{label}\n\n" + "雨声落在窗上，主角在急诊室门口做出了选择。" * 22 + "他终于转身走进去。"


def _eval(
    *,
    continuity: float,
    text: str = "",
    intent: float = 10.0,
) -> EvalReport:
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
        passed=continuity >= 7.0 and intent >= 9.0,
        threshold=6.0,
        summary="评估完成。",
        source_text_hash=source_text_hash(text) if text else "",
    )


def _inputs(tmp_storage: Any, router: Any, builder: Any, runtime_settings: Any):
    runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=runtime_settings,
        max_edit_rounds=1,
    )
    layout = ProjectLayout(tmp_storage.ensure_project_dir("candidate_first_short"))
    layout.ensure_dirs()
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
    plan = {"completion_contract": {"resolution_target": "主角做出选择"}}
    return runner, layout, spec, beats, plan


def test_short_findings_have_exact_source_bound_locator(
    tmp_storage: Any,
    router: Any,
    builder: Any,
    runtime_settings: Any,
) -> None:
    runner, _layout, spec, _beats, plan = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    text = _text("原文")
    diagnosis = diagnose_short_revision(
        runner,
        spec,
        plan,
        text,
        _eval(continuity=5.0, text=text),
        attempted_repair=False,
    )

    assert diagnosis.structured_issues
    locator = diagnosis.structured_issues[0].repair_targets[0]
    assert locator.stable_node_id == "short-story:1"
    assert locator.field_path == "$text"
    assert (locator.char_start, locator.char_end) == (0, len(text))
    assert locator.text_hash == source_text_hash(text)
    assert locator.container_hash == source_text_hash(text)
    assert locator.comparator_id == "short_full_evaluation_v1"
    assert locator.expected_raw == "full_evaluation_passed"
    assert locator.actual_raw
    assert locator.expected_normalized is True
    assert locator.actual_normalized is False


@pytest.mark.asyncio
async def test_short_candidate_is_isolated_and_adopted_only_after_full_improvement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_storage: Any,
    router: Any,
    builder: Any,
    runtime_settings: Any,
) -> None:
    runner, layout, spec, beats, plan = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    baseline = _text("原文")
    candidate = _text("候选")
    baseline_eval = _eval(continuity=5.0, text=baseline)
    official_eval = layout.eval_report_path()
    tmp_storage.save_json(official_eval, baseline_eval.model_dump(mode="json"))

    async def _fake_repair(*args: Any, **kwargs: Any) -> str:
        assert args[6] == baseline
        assert kwargs["persist_draft"] is False
        return candidate

    async def _fake_eval(*args: Any, **kwargs: Any) -> EvalReport:
        assert args[6] == candidate
        assert kwargs["persist"] is False
        assert kwargs["require_intent_compliance"] is True
        return _eval(continuity=9.0, text=candidate)

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit.run_completion_repair", _fake_repair
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.evaluate.run_final_evaluation", _fake_eval
    )

    outcome = await run_short_story_candidate_repair(
        runner=runner,
        layout=layout,
        spec=spec,
        beats=beats,
        blueprint=None,
        execution_plan=plan,
        current_text=baseline,
        issues=["连贯性不足"],
        repair_kind="completion",
        pass_label="short_completion_repair",
        iteration=2,
        baseline_eval=baseline_eval,
    )

    assert outcome.applied is True
    assert outcome.text == candidate
    assert outcome.eval_report.source_text_hash == source_text_hash(candidate)
    assert not tmp_storage.exists(layout.short_draft_path(2))
    assert tmp_storage.load_json(official_eval)["source_text_hash"] == source_text_hash(
        baseline
    )
    case = RepairCaseStore(layout.root).load_case(outcome.case_id)
    assert case is not None and case.status == "verified"
    assert case.verification is not None and case.verification.passed
    assert {item.validator_id for item in case.verification.validators} >= {
        "short_full_evaluation_v1",
        "short_candidate_improvement_v1",
        "short_completeness_v1",
        "short_intent_compliance_v1",
    }


@pytest.mark.asyncio
async def test_nonempty_but_worse_short_candidate_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_storage: Any,
    router: Any,
    builder: Any,
    runtime_settings: Any,
) -> None:
    runner, layout, spec, beats, plan = _inputs(
        tmp_storage, router, builder, runtime_settings
    )
    baseline = _text("原文")
    candidate = _text("非空但退化的候选")
    baseline_eval = _eval(continuity=6.0, text=baseline)

    async def _fake_repair(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return candidate

    async def _fake_eval(*args: Any, **kwargs: Any) -> EvalReport:
        del args, kwargs
        return _eval(continuity=3.0, text=candidate)

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit.run_quality_repair", _fake_repair
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.evaluate.run_final_evaluation", _fake_eval
    )

    outcome = await run_short_story_candidate_repair(
        runner=runner,
        layout=layout,
        spec=spec,
        beats=beats,
        blueprint=None,
        execution_plan=plan,
        current_text=baseline,
        issues=["连贯性不足"],
        repair_kind="quality",
        pass_label="short_quality_repair_after_eval",
        iteration=3,
        baseline_eval=baseline_eval,
    )

    assert outcome.applied is False
    assert outcome.text == baseline
    assert outcome.eval_report == baseline_eval
    case = RepairCaseStore(layout.root).load_case(outcome.case_id)
    assert case is not None and case.verification is not None
    assert case.verification.passed is False
    assert "short_candidate_improvement_v1" in {
        item.validator_id
        for item in case.verification.validators
        if not item.passed
    }


@pytest.mark.asyncio
async def test_candidate_only_final_evaluation_never_overwrites_official_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "eval.json"
    report_path.write_text('{"official":true}', encoding="utf-8")
    candidate = _text("影子候选")
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(),
        _trace=SimpleNamespace(),
        _storage=SimpleNamespace(
            save_json=lambda path, payload: path.write_text(str(payload), encoding="utf-8")
        ),
        _on_step=lambda name, payload: events.append((name, payload)),
    )
    layout = SimpleNamespace(eval_report_path=lambda: report_path)

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit._build_short_eval_context",
        lambda *args, **kwargs: {},
    )

    async def _fake_run(self: Any, text: str) -> EvalReport:
        del self
        return _eval(continuity=9.0, text=text)

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.evaluate.EvaluateStep.run", _fake_run
    )

    report = await run_final_evaluation(
        runner,
        layout,
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        {},
        candidate,
        require_intent_compliance=True,
        persist=False,
        event_name="short_repair_candidate_verification",
    )

    assert report.source_text_hash == source_text_hash(candidate)
    assert report_path.read_text(encoding="utf-8") == '{"official":true}'
    assert events[-1][0] == "short_repair_candidate_verification"
    assert events[-1][1]["candidate_only"] is True
