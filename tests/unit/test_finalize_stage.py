"""Tests for long-pipeline finalize stage helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from novel_forge.core.exceptions import (
    BLOCK_KIND_ALIGNMENT_QUALITY,
    ConsistencyViolationError,
    FinalReportFreshnessError,
    ModelGatewayError,
    RecoveryTarget,
)
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterOutcome,
    ChapterRepairReport,
)
from novel_forge.core.schemas.continuity import ContinuityIssue, ContinuityReport
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.narrative_state.schemas import CandidateStateDelta
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import chapter_finalization_artifact
from novel_forge.pipeline.long import chapter_flow_finalize
from novel_forge.pipeline.long.execution_models import (
    ChapterExecutionContext,
    ChapterReviewArtifacts,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.stages import finalize
from novel_forge.pipeline.long.stages import finalize_persist as finalize_persist_impl
from novel_forge.pipeline.long.stages import finalize_report as finalize_report_impl
from novel_forge.pipeline.long.stages.report_freshness import (
    quality_report_evidence_binding,
    stamp_report_freshness,
)
from novel_forge.story_kernel.schemas import StoryKernel


def _save_bound_review_report(
    *,
    storage: Any,
    path: Any,
    report: Any,
    current_text: str,
    packet: Any,
    bridge: Any,
    plan: Any,
    bundle: Any,
) -> None:
    binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )
    payload = report.model_dump(mode="json")
    stamp_report_freshness(
        payload,
        current_hash=finalize._source_text_hash(current_text),
        context_hash=str(binding["context_hash"]),
        evidence_hashes=dict(binding["evidence_hashes"]),
    )
    storage.save_json(path, payload)


@pytest.mark.asyncio
async def test_archive_preflight_text_repair_helper_marks_stale_and_preserves_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    extract_inputs: list[str] = []
    outcome = ChapterOutcome(
        source_chapter=1,
        chapter_summary="旧摘要",
        creative_report=CreativeReport(structured_summary="旧报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1),
    )
    repaired_outcome = ChapterOutcome(
        source_chapter=1,
        chapter_summary="新摘要",
        creative_report=CreativeReport(structured_summary="新报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1),
    )

    async def _fake_kernel_composer(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _fake_run_continuity_repair(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            applied=True,
            revised_text="修后正文" * 20,
            repair_plan=SimpleNamespace(no_op=False),
        )

    async def _fake_extract_and_validate(
        _context: Any,
        _bundle: Any,
        _packet: Any,
        _bridge: Any,
        _plan: Any,
        current_text: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> ChapterOutcome:
        extract_inputs.append(current_text)
        return repaired_outcome

    from novel_forge.pipeline.long import repair as long_repair

    monkeypatch.setattr(chapter_flow_finalize, "load_story_kernel_composer", _fake_kernel_composer)
    monkeypatch.setattr(long_repair, "run_continuity_repair", _fake_run_continuity_repair)
    monkeypatch.setattr(
        chapter_flow_finalize,
        "extract_and_validate",
        _fake_extract_and_validate,
    )

    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(chapter_number=1),
        style_profile=None,
        editorial_contract=None,
        chapter_source_slice=None,
    )
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=bundle,
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="旧正文" * 20,
        performed_edits=1,
        outcome=outcome,
        alignment_report=AlignmentReport(alignment_score=9.0),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(continuity_score=8.0),
        repair_plan=SimpleNamespace(no_op=True),
        eval_report=EvalReport(overall_score=8.0, passed=True),
        repair_tickets=[],
        total_repair_rounds_used=2,
    )
    context = SimpleNamespace(
        router=object(),
        builder=object(),
        settings=SimpleNamespace(),
        on_step=lambda step, payload: events.append((step, payload)),
    )
    spec = chapter_flow_finalize.ArchivePreflightRepairSpec(
        issue=ContinuityIssue(issue_type="carry_forward_missing", summary="补承接"),
        continuity_summary="归档前承接硬门定向修复",
        escalation_note="只补写承接。",
        stale_reason="carry_forward_repair_before_archive",
        start_event="carry_forward_repair_start",
        skipped_event="carry_forward_repair_skipped",
        complete_event="carry_forward_repair_complete",
        start_payload={"unclosed_count": 1},
        complete_payload={"total_repair_rounds_used": 3},
        repair_tickets=("ticket",),
        total_round_increment=1,
    )

    result = await chapter_flow_finalize._run_archive_preflight_text_repair(
        context,
        review=review,
        trace=cast(PipelineTrace, SimpleNamespace()),
        spec=spec,
    )

    assert result is not None
    assert result.current_text == "修后正文" * 20
    assert result.outcome is repaired_outcome
    assert result.eval_report is None
    assert result.quality_reports_stale_after_text_change is True
    assert result.quality_reports_stale_reason == "carry_forward_repair_before_archive"
    assert result.total_repair_rounds_used == 3
    assert result.repair_tickets == ["ticket"]
    assert extract_inputs == ["修后正文" * 20]
    assert [step for step, _payload in events] == [
        "carry_forward_repair_start",
        "carry_forward_repair_complete",
    ]
    assert events[0][1]["unclosed_count"] == 1
    assert events[1][1]["total_repair_rounds_used"] == 3


@pytest.mark.asyncio
async def test_evaluate_chapter_text_uses_outline_word_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeEvaluateStep:
        def __init__(
            self, router, builder, *, settings, trace, extra_context=None, kernel_context=None
        ) -> None:
            captured["extra_context"] = extra_context

        async def run(self, current_text: str) -> EvalReport:
            captured["text"] = current_text
            return EvalReport(overall_score=8.0, passed=True)

    monkeypatch.setattr(finalize_report_impl, "EvaluateStep", _FakeEvaluateStep)

    context = cast(
        ChapterExecutionContext,
        SimpleNamespace(router=object(), builder=object(), settings=object()),
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(expected_word_count=900, goal="推进主线"),
        story_bible=SimpleNamespace(tone="悬疑"),
        style_profile={"modules": []},
    )

    report = await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
    )

    assert report.overall_score == 8.0
    assert captured["text"] == "正文"
    assert captured["extra_context"]["target_word_count"] == 900
    assert captured["extra_context"]["chapter_goal"] == "推进主线"


@pytest.mark.asyncio
async def test_evaluate_chapter_text_omits_word_target_when_gate_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeEvaluateStep:
        def __init__(
            self, router, builder, *, settings, trace, extra_context=None, kernel_context=None
        ) -> None:
            captured["extra_context"] = extra_context

        async def run(self, current_text: str) -> EvalReport:
            captured["text"] = current_text
            return EvalReport(overall_score=8.0, passed=True)

    monkeypatch.setattr(finalize_report_impl, "EvaluateStep", _FakeEvaluateStep)

    context = cast(
        ChapterExecutionContext,
        SimpleNamespace(
            router=object(),
            builder=object(),
            settings=SimpleNamespace(long_word_count_archive_gate_enabled=False),
        ),
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(expected_word_count=900, goal="推进主线"),
        story_bible=SimpleNamespace(tone="悬疑"),
        style_profile={"modules": []},
    )

    await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
    )

    assert "target_word_count" not in captured["extra_context"]
    assert captured["extra_context"]["word_count_gate_enabled"] is False


@pytest.mark.asyncio
async def test_evaluate_chapter_text_reuses_matching_context_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls = 0
    saved: dict[str, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []

    class _FakeEvaluateStep:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _current_text: str) -> EvalReport:
            nonlocal calls
            calls += 1
            return EvalReport(
                overall_score=8.0,
                passed=True,
                score_diagnostics={"existing": "kept"},
            )

    async def _no_kernel(*_args, **_kwargs):
        return None

    monkeypatch.setattr(finalize_report_impl, "EvaluateStep", _FakeEvaluateStep)
    monkeypatch.setattr(finalize_report_impl, "load_story_kernel_composer", _no_kernel)

    eval_path = tmp_path / "eval.json"
    context = cast(
        ChapterExecutionContext,
        SimpleNamespace(
            router=object(),
            builder=object(),
            settings=SimpleNamespace(
                long_word_count_archive_gate_enabled=False,
                long_eval_reuse_enabled=True,
            ),
            storage=SimpleNamespace(
                save_json=lambda path, payload: saved.update({str(path): payload})
            ),
            on_step=lambda step, payload: events.append((step, payload)),
        ),
    )
    bundle = SimpleNamespace(
        layout=SimpleNamespace(eval_report_path=lambda _chapter: eval_path),
        chapter_outline=SimpleNamespace(expected_word_count=900, goal="推进主线"),
        story_bible=SimpleNamespace(tone="悬疑"),
        style_profile={"modules": []},
    )

    first = await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
        extra_context={"causal_link": "A"},
    )
    second = await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=True,
        persist=True,
        extra_context={"causal_link": "A"},
        reuse_report=first,
        reuse_label="unit",
    )

    assert calls == 1
    assert second.score_diagnostics["existing"] == "kept"
    assert second.score_diagnostics["eval_context_hash"]
    assert saved[str(eval_path)]["source_text_hash"] == second.source_text_hash
    assert any(step == "evaluate_reused" for step, _payload in events)
    assert any(step == "evaluate" for step, _payload in events)


@pytest.mark.asyncio
async def test_evaluate_chapter_text_does_not_reuse_when_context_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _FakeEvaluateStep:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _current_text: str) -> EvalReport:
            nonlocal calls
            calls += 1
            return EvalReport(overall_score=8.0, passed=True)

    async def _no_kernel(*_args, **_kwargs):
        return None

    monkeypatch.setattr(finalize_report_impl, "EvaluateStep", _FakeEvaluateStep)
    monkeypatch.setattr(finalize_report_impl, "load_story_kernel_composer", _no_kernel)

    context = cast(
        ChapterExecutionContext,
        SimpleNamespace(
            router=object(),
            builder=object(),
            settings=SimpleNamespace(
                long_word_count_archive_gate_enabled=False,
                long_eval_reuse_enabled=True,
            ),
        ),
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(expected_word_count=900, goal="推进主线"),
        story_bible=SimpleNamespace(tone="悬疑"),
        style_profile={"modules": []},
    )

    first = await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
        extra_context={"causal_link": "A"},
    )
    await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
        extra_context={"causal_link": "B"},
        reuse_report=first,
    )

    assert calls == 2


@pytest.mark.asyncio
async def test_evaluate_chapter_text_respects_reuse_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _FakeEvaluateStep:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _current_text: str) -> EvalReport:
            nonlocal calls
            calls += 1
            return EvalReport(overall_score=8.0, passed=True)

    async def _no_kernel(*_args, **_kwargs):
        return None

    monkeypatch.setattr(finalize_report_impl, "EvaluateStep", _FakeEvaluateStep)
    monkeypatch.setattr(finalize_report_impl, "load_story_kernel_composer", _no_kernel)

    context = cast(
        ChapterExecutionContext,
        SimpleNamespace(
            router=object(),
            builder=object(),
            settings=SimpleNamespace(
                long_word_count_archive_gate_enabled=False,
                long_eval_reuse_enabled=False,
            ),
        ),
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(expected_word_count=900, goal="推进主线"),
        story_bible=SimpleNamespace(tone="悬疑"),
        style_profile={"modules": []},
    )

    first = await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
    )
    await finalize.evaluate_chapter_text(
        context,
        bundle=bundle,
        chapter_number=1,
        current_text="正文",
        trace=cast(PipelineTrace, SimpleNamespace()),
        emit_step=False,
        persist=False,
        reuse_report=first,
    )

    assert calls == 2


class _MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def save_json(self, path: Any, payload: Any) -> None:
        self.data[str(path)] = payload

    def load_json(self, path: Any) -> Any:
        return self.data[str(path)]

    def exists(self, path: Any) -> bool:
        return str(path) in self.data


def _archive_preflight_runner(storage: _MemoryStorage, events: list[tuple[str, Any]]) -> Any:
    return SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(
            long_kb_audit_reuse_enabled=True,
            narrative_state_enabled=False,
            long_contract_audit_enabled=False,
            long_word_count_archive_gate_enabled=False,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )


@pytest.mark.asyncio
async def test_archive_preflight_reuses_fresh_knowledge_boundary_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    text = "正文" * 700
    kb_path = tmp_path / "kb.json"
    storage = _MemoryStorage()
    storage.save_json(
        kb_path,
        {
            "chapter": 2,
            "stage": "review_finalize",
            "source_text_hash": finalize.source_text_hash(text),
            "hidden_candidate_count": 3,
            "prescreen_hit_count": 0,
            "findings": [],
            "issues": [],
            "repair_tickets": [],
            "audit_skipped_reason": "no_prescreen_hits",
        },
    )
    events: list[tuple[str, Any]] = []

    async def _should_not_run_audit(*_args, **_kwargs):
        raise AssertionError("KB audit should have been reused")

    monkeypatch.setattr(
        finalize_persist_impl, "run_knowledge_boundary_audit", _should_not_run_audit
    )

    result = await finalize.run_archive_preflight_repairs(
        runner=_archive_preflight_runner(storage, events),
        bundle=SimpleNamespace(
            layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: kb_path),
            chapter_outline=SimpleNamespace(expected_word_count=0),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        outcome=SimpleNamespace(),
        current_text=text,
        chapter_number=2,
        trace=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        eval_report=None,
        chapter_repair_report=None,
        memory_hints=None,
        allow_word_count_archive_bypass=False,
        allow_semantic_pre_archive_repairs=False,
        terminal_humanize=None,
        decision=finalize.ReportRefreshDecision.empty(),
    )

    assert result.knowledge_boundary_findings == []
    assert storage.load_json(kb_path)["stage"] == "archive_pre_persist"
    assert storage.load_json(kb_path)["source_text_hash"] == finalize.source_text_hash(text)
    assert any(step == "knowledge_boundary_verification_skipped" for step, _payload in events)


@pytest.mark.asyncio
async def test_archive_preflight_reruns_knowledge_boundary_on_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    text = "正文" * 700
    kb_path = tmp_path / "kb.json"
    storage = _MemoryStorage()
    storage.save_json(
        kb_path,
        {
            "chapter": 2,
            "stage": "review_finalize",
            "source_text_hash": finalize.source_text_hash("旧正文"),
            "prescreen_hit_count": 0,
            "findings": [],
            "issues": [],
            "repair_tickets": [],
        },
    )
    finding = SimpleNamespace(blocks_finalize=False, severity="low", issue_type="x", summary="x")

    async def _fake_audit(*_args, **_kwargs):
        return [finding]

    monkeypatch.setattr(finalize_persist_impl, "run_knowledge_boundary_audit", _fake_audit)

    result = await finalize.run_archive_preflight_repairs(
        runner=_archive_preflight_runner(storage, []),
        bundle=SimpleNamespace(
            layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: kb_path),
            chapter_outline=SimpleNamespace(expected_word_count=0),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        outcome=SimpleNamespace(),
        current_text=text,
        chapter_number=2,
        trace=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        eval_report=None,
        chapter_repair_report=None,
        memory_hints=None,
        allow_word_count_archive_bypass=False,
        allow_semantic_pre_archive_repairs=False,
        terminal_humanize=None,
        decision=finalize.ReportRefreshDecision.empty(),
    )

    assert result.knowledge_boundary_findings == [finding]


@pytest.mark.asyncio
async def test_archive_preflight_reruns_knowledge_boundary_for_unsafe_cached_reports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    text = "正文" * 700
    current_hash = finalize.source_text_hash(text)
    unsafe_payloads: list[dict[str, Any] | None] = [
        {
            "chapter": 2,
            "stage": "review_finalize",
            "source_text_hash": current_hash,
            "prescreen_hit_count": 1,
            "findings": [],
            "issues": [],
            "repair_tickets": [],
        },
        {
            "chapter": 2,
            "stage": "review_finalize",
            "source_text_hash": current_hash,
            "prescreen_hit_count": 0,
            "findings": [{"summary": "已有发现"}],
            "issues": [],
            "repair_tickets": [],
        },
        {
            "chapter": 2,
            "stage": "review_finalize",
            "source_text_hash": current_hash,
            "prescreen_hit_count": 0,
            "findings": [],
            "issues": [],
            "blockers": ["已有阻断"],
            "repair_tickets": [],
        },
        None,
    ]
    calls = 0
    finding = SimpleNamespace(blocks_finalize=False, severity="low", issue_type="x", summary="x")

    async def _fake_audit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [finding]

    monkeypatch.setattr(finalize_persist_impl, "run_knowledge_boundary_audit", _fake_audit)

    for idx, payload in enumerate(unsafe_payloads):
        kb_path = tmp_path / f"kb_{idx}.json"
        storage = _MemoryStorage()
        if payload is not None:
            storage.save_json(kb_path, payload)
        events: list[tuple[str, Any]] = []

        result = await finalize.run_archive_preflight_repairs(
            runner=_archive_preflight_runner(storage, events),
            bundle=SimpleNamespace(
                layout=SimpleNamespace(
                    knowledge_boundary_report_path=lambda _chapter, path=kb_path: path
                ),
                chapter_outline=SimpleNamespace(expected_word_count=0),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
            outcome=SimpleNamespace(),
            current_text=text,
            chapter_number=2,
            trace=SimpleNamespace(),
            continuity_report=SimpleNamespace(),
            eval_report=None,
            chapter_repair_report=None,
            memory_hints=None,
            allow_word_count_archive_bypass=False,
            allow_semantic_pre_archive_repairs=False,
            terminal_humanize=None,
            decision=finalize.ReportRefreshDecision.empty(),
        )

        assert result.knowledge_boundary_findings == [finding]
        assert not any(step == "knowledge_boundary_verification_skipped" for step, _ in events)

    assert calls == len(unsafe_payloads)


@pytest.mark.asyncio
async def test_archive_preflight_finishes_semantic_repairs_before_terminal_humanize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    text = "正文" * 700
    storage = _MemoryStorage()
    events: list[tuple[str, Any]] = []
    order: list[str] = []
    runner = _archive_preflight_runner(storage, events)
    runner._settings.narrative_state_enabled = True
    runner._settings.narrative_state_required = True
    runner._settings.long_kb_audit_reuse_enabled = False
    blocker = SimpleNamespace(
        blocks_finalize=True,
        severity="high",
        confidence=0.95,
        issue_type="knowledge_boundary_leak",
        summary="人物提前知道秘密",
    )
    state_report = SimpleNamespace()
    state_calls = 0

    async def _fake_state_adjudication(*args: Any, allow_repair: bool = True, **_kwargs: Any):
        nonlocal state_calls
        state_calls += 1
        current_text = args[6]
        order.append("state_repair" if allow_repair else "state_verify")
        if allow_repair:
            current_text += "状态修复。"
        return current_text, args[5], args[10], state_report

    async def _fake_kb_audit(*_args: Any, stage: str, **_kwargs: Any) -> list[Any]:
        order.append(f"kb_audit:{stage}")
        return [blocker] if stage == "archive_pre_persist" else []

    async def _fake_kb_repair(*_args: Any, current_text: str, **_kwargs: Any) -> Any:
        order.append("kb_repair")
        return SimpleNamespace(
            current_text=current_text + "知识修复。",
            findings_after=[],
            repair_exhausted=False,
            rounds_used=1,
        )

    async def _terminal_humanize(current_text: str) -> str:
        order.append("humanize")
        assert current_text.endswith("状态修复。知识修复。")
        return current_text + "拟人化收束。"

    monkeypatch.setattr(
        finalize_persist_impl, "_adjudicate_state_before_archive", _fake_state_adjudication
    )
    monkeypatch.setattr(finalize_persist_impl, "run_knowledge_boundary_audit", _fake_kb_audit)
    monkeypatch.setattr(
        finalize_persist_impl, "run_knowledge_boundary_repair_loop", _fake_kb_repair
    )

    result = await finalize.run_archive_preflight_repairs(
        runner=runner,
        bundle=SimpleNamespace(
            layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: tmp_path / "kb"),
            chapter_outline=SimpleNamespace(expected_word_count=0),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        outcome=SimpleNamespace(),
        current_text=text,
        chapter_number=2,
        trace=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        eval_report=None,
        chapter_repair_report=None,
        memory_hints=None,
        allow_word_count_archive_bypass=False,
        allow_prompt_leak_patch_repair=False,
        terminal_humanize=_terminal_humanize,
        decision=finalize.ReportRefreshDecision.empty(),
    )

    assert state_calls == 2
    assert order == [
        "state_repair",
        "kb_audit:archive_pre_persist",
        "kb_repair",
        "humanize",
        "state_verify",
        "kb_audit:archive_post_humanize_verify",
    ]
    assert result.current_text.endswith("状态修复。知识修复。拟人化收束。")
    assert result.terminal_humanize_metadata["post_humanize_verification"] == "actual"
    verify_event = next(
        payload for step, payload in events if step == "post_humanize_semantic_verification"
    )
    assert verify_event["text_mutated"] is False


@pytest.mark.asyncio
async def test_archive_preflight_post_humanize_verification_never_repairs_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    text = "正文" * 700
    storage = _MemoryStorage()
    events: list[tuple[str, Any]] = []
    runner = _archive_preflight_runner(storage, events)
    runner._settings.long_kb_audit_reuse_enabled = False
    blocker = SimpleNamespace(
        blocks_finalize=True,
        severity="critical",
        confidence=0.99,
        issue_type="knowledge_boundary_leak",
        summary="Humanize 引入了越界知识",
    )

    async def _fake_contract_audit(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _fake_kb_audit(*_args: Any, stage: str, **_kwargs: Any) -> list[Any]:
        return [] if stage == "archive_pre_persist" else [blocker]

    async def _repair_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("post-Humanize verification must never repair prose")

    monkeypatch.setattr(
        finalize_persist_impl,
        "_run_contract_execution_audit_without_state_adjudication",
        _fake_contract_audit,
    )
    monkeypatch.setattr(finalize_persist_impl, "run_knowledge_boundary_audit", _fake_kb_audit)
    monkeypatch.setattr(
        finalize_persist_impl, "run_knowledge_boundary_repair_loop", _repair_must_not_run
    )

    with pytest.raises(
        ConsistencyViolationError,
        match="Humanize 引入了越界知识",
    ):
        await finalize.run_archive_preflight_repairs(
            runner=runner,
            bundle=SimpleNamespace(
                layout=SimpleNamespace(
                    knowledge_boundary_report_path=lambda _chapter: tmp_path / "kb"
                ),
                chapter_outline=SimpleNamespace(expected_word_count=0),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
            outcome=SimpleNamespace(),
            current_text=text,
            chapter_number=2,
            trace=SimpleNamespace(),
            continuity_report=SimpleNamespace(),
            eval_report=None,
            chapter_repair_report=None,
            memory_hints=None,
            allow_word_count_archive_bypass=False,
            allow_prompt_leak_patch_repair=False,
            terminal_humanize=lambda value: _async_text(value + "拟人化收束。"),
            decision=finalize.ReportRefreshDecision.empty(),
        )

    blocked = next(
        payload for step, payload in events if step == "post_humanize_semantic_verification_blocked"
    )
    assert blocked["action"] == "reject_without_post_humanize_repair"


async def _async_text(value: str) -> str:
    return value


def test_clean_validate_blocks_structural_word_count_when_gate_enabled() -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=True),
        _on_step=lambda *_args, **_kwargs: None,
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=4500))

    with pytest.raises(RuntimeError, match="outside archive range"):
        finalize._clean_and_validate_chapter_text(
            runner,
            bundle,
            1,
            "字" * 1500,
        )


def test_clean_validate_bypasses_structural_word_count_after_rejection_limit() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=True),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=4500))
    text = "字" * 1500

    assert (
        finalize._clean_and_validate_chapter_text(
            runner,
            bundle,
            1,
            text,
            allow_word_count_archive_bypass=True,
        )
        == text
    )
    assert events[-1][0] == "word_count_archive_gate_bypassed"


def test_clean_validate_allows_structural_word_count_when_gate_disabled() -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=False),
        _on_step=lambda *_args, **_kwargs: None,
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=4500))
    text = "字" * 1500

    assert finalize._clean_and_validate_chapter_text(runner, bundle, 1, text) == text


def test_clean_validate_keeps_in_world_bracketed_title() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=False),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=1200))
    text = (
        "邮件标题赫然写着：【紧急通知】关于贵司订单交付计划的调整说明。"
        + "后续正文继续推进。" * 120
    )

    cleaned = finalize._clean_and_validate_chapter_text(
        runner,
        bundle,
        1,
        text,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【紧急通知】"]),
    )

    assert "【紧急通知】" in cleaned
    assert not any(step == "prompt_leak_deterministic_fallback" for step, _ in events)


def test_clean_validate_uses_deterministic_fallback_for_true_prompt_leak() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=False),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=1200))
    text = "她刚踏进门，正文里却混进【交接】这样的规划标记。" + "后续正文继续推进。" * 120

    cleaned = finalize._clean_and_validate_chapter_text(
        runner,
        bundle,
        1,
        text,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【交接】"]),
    )

    assert "【交接】" not in cleaned
    assert any(step == "prompt_leak_deterministic_fallback" for step, _ in events)


def test_clean_validate_keeps_critical_short_guard_when_gate_disabled() -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(long_word_count_archive_gate_enabled=False),
        _on_step=lambda *_args, **_kwargs: None,
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(expected_word_count=4500))

    with pytest.raises(RuntimeError, match="too short"):
        finalize._clean_and_validate_chapter_text(
            runner,
            bundle,
            1,
            "字" * 1000,
        )


def test_archive_hard_quality_blocks_low_causal_score() -> None:
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )

    with pytest.raises(ConsistencyViolationError, match="因果分 0.9 低于硬阻断线 4.0"):
        finalize._enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=2,
            eval_report=EvalReport(overall_score=9.0, passed=True),
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=0.9, issues=[]),
            chapter_repair_report=None,
            current_text=None,  # no stale-hash check exercised
        )

    assert events
    assert events[0][0] == "archive_hard_quality_block"


def test_archive_hard_quality_skips_untrusted_eval_score_but_keeps_other_gates() -> None:
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=6.0,
            long_alignment_threshold=7.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )

    finalize._enforce_archive_hard_quality_blocks(
        runner,
        chapter_number=2,
        eval_report=EvalReport(
            overall_score=0.0,
            passed=False,
            evaluation_status="fallback",
            is_fallback=True,
            fallback_reason="model_gateway_unavailable",
            score_confidence="fallback",
        ),
        alignment_report=AlignmentReport(alignment_score=9.0),
        continuity_report=ContinuityReport(continuity_score=9.0),
        causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
        chapter_repair_report=None,
        current_text="正文" * 200,
    )

    steps = [step for step, _payload in events]
    assert "archive_eval_unavailable_degraded" in steps
    assert "archive_hard_quality_block" not in steps


def test_archive_hard_quality_blocks_low_fresh_alignment_score_routes_execution_repair() -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_alignment_threshold=7.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda *_args: None,
    )

    with pytest.raises(ConsistencyViolationError, match="对齐分 6.4 低于归档阈值 7.0") as exc_info:
        finalize._enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=2,
            eval_report=EvalReport(overall_score=9.0, passed=True),
            alignment_report=AlignmentReport(alignment_score=6.4),
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            chapter_repair_report=None,
            current_text="正文" * 200,
        )

    assert exc_info.value.block_kind == BLOCK_KIND_ALIGNMENT_QUALITY
    assert exc_info.value.violation_kind == "execution_fixable"
    assert exc_info.value.failed_stage == "archive_quality_gate"
    assert exc_info.value.replan_target is RecoveryTarget.DRAFT


def test_archive_quality_exception_contract_normalizes_legacy_defaults() -> None:
    """A future archive producer cannot emit the old MANUAL routing by mistake."""
    exc = ConsistencyViolationError(
        ["章节 2 对齐分 2.6 低于归档阈值 8.0，拒绝保存并强制重新规划。"],
        block_kind=BLOCK_KIND_ALIGNMENT_QUALITY,
    )

    assert exc.violation_kind == "execution_fixable"
    assert exc.failed_stage == "archive_quality_gate"
    assert exc.replan_target is RecoveryTarget.DRAFT


def test_archive_hard_quality_skips_stale_low_alignment_score() -> None:
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_alignment_threshold=7.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )

    finalize._enforce_archive_hard_quality_blocks(
        runner,
        chapter_number=2,
        eval_report=EvalReport(overall_score=9.0, passed=True),
        alignment_report=AlignmentReport(
            alignment_score=2.0,
            source_text_hash="stale-alignment-report",
        ),
        continuity_report=ContinuityReport(continuity_score=9.0),
        causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
        chapter_repair_report=None,
        current_text="正文" * 200,
    )

    assert [step for step, _payload in events] == ["alignment_report_stale_skipped"]


def test_archive_hard_quality_blocks_chapter_quality_hard_fail() -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda *_args: None,
    )
    chapter_repair_report = ChapterRepairReport(
        risk_level="high",
        summary="存在硬错误",
        factual_errors=["事实错误：称谓与身份规则冲突"],
    )

    with pytest.raises(ConsistencyViolationError, match="章节质量仍存在阻断级问题"):
        finalize._enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=2,
            eval_report=EvalReport(overall_score=9.0, passed=True),
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            chapter_repair_report=chapter_repair_report,
            current_text=None,  # report has no source_text_hash → strict path
        )


def test_archive_hard_quality_blocks_skips_stale_chapter_repair_report() -> None:
    """Stale reports (mismatched source_text_hash) must not block the archive.

    Reproduces the 2026-06-09 弈心锁玉 / 第1章 failure: a chapter_repair_report
    was loaded from a previous failed run's checkpoint while the text had
    since been cleaned by other repair stages. The gate must skip the block,
    emit a structured 'chapter_repair_report_stale_skipped' event, and not
    raise.
    """
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    current_text = "当前已修正章节正文" * 200
    stale_hash = finalize._source_text_hash("早期未修正正文" * 200)
    chapter_repair_report = ChapterRepairReport(
        risk_level="medium",
        summary="已修复",
        expression_errors=[
            "{'text': '那是一个pov越权片段', 'issue': 'pov_knowledge_breach', 'severity': 'high'}"
        ],
        source_text_hash=stale_hash,
    )

    finalize._enforce_archive_hard_quality_blocks(
        runner,
        chapter_number=1,
        eval_report=EvalReport(overall_score=9.0, passed=True),
        continuity_report=ContinuityReport(continuity_score=9.0),
        causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
        chapter_repair_report=chapter_repair_report,
        current_text=current_text,
    )

    assert events
    steps = [step for step, _ in events]
    assert "chapter_repair_report_stale_skipped" in steps
    assert "archive_hard_quality_block" not in steps


def test_archive_hard_quality_blocks_fresh_chapter_repair_report_includes_evidence() -> None:
    """Fresh hard-fail must include concrete issue evidence in the error message.

    Operators must see the actual issue text (not just a count) so they can
    decide whether to repair, force-archive, or refresh the report.
    """
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda *_args: None,
    )
    current_text = "未修正章节正文" * 200
    current_hash = finalize._source_text_hash(current_text)
    chapter_repair_report = ChapterRepairReport(
        risk_level="high",
        summary="存在 POV 越权",
        expression_errors=[
            "{'text': '叙述者直接解释密语含义（pov_knowledge_breach）', 'location': '沈清漪听到笛声后', 'issue': 'pov_knowledge_breach', 'severity': 'high'}"
        ],
        source_text_hash=current_hash,
    )

    with pytest.raises(ConsistencyViolationError) as excinfo:
        finalize._enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=1,
            eval_report=EvalReport(overall_score=9.0, passed=True),
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            chapter_repair_report=chapter_repair_report,
            current_text=current_text,
        )

    # Operators need the actual issue text, not a bare count.
    message = str(excinfo.value)
    assert "pov_knowledge_breach" in message
    assert "证据" in message
    assert "1 个阻断级问题" not in message or "证据" in message


def test_archive_hard_quality_blocks_no_current_text_falls_back_to_strict_check() -> None:
    """Without current_text the staleness check cannot run; behave strictly.

    This preserves the original behavior when the gate is called without
    enough context to evaluate staleness (e.g. legacy callers that don't yet
    pass current_text). Better to over-block than to silently skip a real
    hard failure.
    """
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    chapter_repair_report = ChapterRepairReport(
        risk_level="high",
        summary="pov 越权",
        expression_errors=["pov_knowledge_breach — 叙述者直入非POV内心"],
        source_text_hash="legacy-hash-no-current-text",
    )

    with pytest.raises(ConsistencyViolationError, match="章节质量仍存在阻断级问题"):
        finalize._enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=1,
            eval_report=EvalReport(overall_score=9.0, passed=True),
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            chapter_repair_report=chapter_repair_report,
            current_text=None,
        )

    steps = [step for step, _ in events]
    assert "archive_hard_quality_block" in steps
    assert "chapter_repair_report_stale_skipped" not in steps


def test_marked_stale_report_hashes_do_not_get_restamped(tmp_path) -> None:
    class _Storage:
        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

    old_hash = finalize._source_text_hash("旧正文" * 120)
    new_hash = finalize._source_text_hash("精修正文" * 120)
    reports = tmp_path / "reports"
    layout = SimpleNamespace(
        alignment_report_path=lambda chapter: reports / f"alignment_{chapter}.json",
        eval_report_path=lambda chapter: reports / f"eval_{chapter}.json",
        continuity_report_path=lambda chapter: reports / f"continuity_{chapter}.json",
        chapter_causal_report_path=lambda chapter: reports / f"causal_{chapter}.json",
        guard_report_path=lambda chapter: reports / f"guard_{chapter}.json",
    )
    storage = _Storage()
    for path in (
        layout.alignment_report_path(1),
        layout.eval_report_path(1),
        layout.continuity_report_path(1),
        layout.chapter_causal_report_path(1),
        layout.guard_report_path(1),
    ):
        storage.save_json(path, {"source_text_hash": old_hash})

    finalize._sync_or_mark_report_hashes(
        SimpleNamespace(_storage=storage),
        layout,
        1,
        new_hash,
        allow_restamp=False,
        stale_reason="post_polish_text_changed",
    )

    for path in (
        layout.alignment_report_path(1),
        layout.eval_report_path(1),
        layout.continuity_report_path(1),
        layout.chapter_causal_report_path(1),
        layout.guard_report_path(1),
    ):
        payload = storage.load_json(path)
        assert payload["source_text_hash"] == old_hash
        assert payload["stale_after_text_change"] is True
        assert payload["stale_expected_text_hash"] == new_hash
        assert payload["stale_reason"] == "post_polish_text_changed"

    finalize._validate_report_hashes(storage, layout, 1, new_hash)


def test_finalize_report_hash_transaction_marks_then_validates(tmp_path) -> None:
    class _Storage:
        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

    old_hash = finalize._source_text_hash("旧正文" * 80)
    new_hash = finalize._source_text_hash("归档正文" * 80)
    reports = tmp_path / "reports"
    layout = SimpleNamespace(
        alignment_report_path=lambda chapter: reports / f"alignment_{chapter}.json",
        eval_report_path=lambda chapter: reports / f"eval_{chapter}.json",
        continuity_report_path=lambda chapter: reports / f"continuity_{chapter}.json",
        chapter_causal_report_path=lambda chapter: reports / f"causal_{chapter}.json",
        guard_report_path=lambda chapter: reports / f"guard_{chapter}.json",
    )
    storage = _Storage()
    for path in (
        layout.alignment_report_path(1),
        layout.continuity_report_path(1),
        layout.chapter_causal_report_path(1),
    ):
        storage.save_json(path, {"source_text_hash": old_hash})

    finalize._finalize_report_hash_transaction(
        SimpleNamespace(_storage=storage),
        layout,
        1,
        new_hash,
        allow_restamp=False,
        stale_reason="transaction_test",
    )

    alignment_payload = storage.load_json(layout.alignment_report_path(1))
    assert alignment_payload["source_text_hash"] == old_hash
    assert alignment_payload["stale_after_text_change"] is True
    assert alignment_payload["stale_expected_text_hash"] == new_hash
    assert alignment_payload["stale_reason"] == "transaction_test"


@pytest.mark.asyncio
async def test_extract_retry_persists_retry_outcome_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _Storage:
        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

        def exists(self, path: Any) -> bool:
            return path.exists()

    first = ChapterOutcome(
        source_chapter=1,
        chapter_summary="首次摘要",
        creative_report=CreativeReport(structured_summary="首次创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1, location="旧地点"),
    )
    retry = ChapterOutcome(
        source_chapter=1,
        chapter_summary="重试摘要",
        creative_report=CreativeReport(structured_summary="重试创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1, location="新地点"),
    )
    calls = 0

    class _FakeExtractStep:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def run(self, _payload: Any) -> ChapterOutcome:
            nonlocal calls
            calls += 1
            return first if calls == 1 else retry

    class _Rules:
        def __init__(self) -> None:
            self.calls = 0

        def validate(self, *_args: Any, **_kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(violations=["旧抽取不一致"], warnings=[])
            return SimpleNamespace(violations=[], warnings=[])

    class _ContinuityRules:
        def validate(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(violations=[], warnings=[])

    monkeypatch.setattr(finalize_report_impl, "ExtractCanonDeltaStep", _FakeExtractStep)
    monkeypatch.setattr(
        finalize_report_impl,
        "ContinuityRules",
        _ContinuityRules,
    )

    storage = _Storage()
    layout = SimpleNamespace(
        creative_report_path=lambda chapter: tmp_path / f"creative_{chapter}.json",
        chapter_exit_state_path=lambda chapter: tmp_path / f"exit_{chapter}.json",
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(narrative_state_enabled=False),
        _rules=_Rules(),
        _storage=storage,
        _on_step=lambda *_args: None,
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(title="第一章", goal="推进主线"),
        canon_state=StoryKernel(project_id="retry_demo"),
    )
    packet = SimpleNamespace(
        known_characters=[],
        active_relationships=[],
        character_profiles=[],
        previous_exit_state=None,
    )

    outcome = await finalize.extract_and_validate(
        runner,
        bundle,
        packet,
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="正文" * 300,
        chapter_number=1,
        trace=object(),
        continuity_report=ContinuityReport(continuity_score=9.0),
    )

    assert outcome.creative_report.structured_summary == "重试创作报告"
    assert storage.load_json(layout.creative_report_path(1))["structured_summary"] == "重试创作报告"
    assert storage.load_json(layout.chapter_exit_state_path(1))["location"] == "新地点"


@pytest.mark.asyncio
async def test_persist_results_refreshes_reports_after_semantic_archive_patch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class _Storage:
        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

        def save_text(self, path: Any, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        def load_text(self, path: Any) -> str:
            return path.read_text(encoding="utf-8")

        def exists(self, path: Any) -> bool:
            return path.exists()

    old_text = "旧正文" * 240
    patched_text = "补丁后正文" * 180
    old_hash = finalize._source_text_hash(old_text)
    patched_hash = finalize._source_text_hash(patched_text)

    storage = _Storage()
    reports = tmp_path / "reports"
    states = tmp_path / "states"
    chapters = tmp_path / "chapters"
    layout = SimpleNamespace(
        root=tmp_path,
        states_dir=states,
        chapter_path=lambda chapter: chapters / f"chapter_{chapter}.md",
        chapter_artifact_path=lambda chapter, artifact_type: (
            states / f"chapter_{chapter:03d}_artifacts" / f"{artifact_type}.json"
        ),
        alignment_report_path=lambda chapter: reports / f"alignment_{chapter}.json",
        eval_report_path=lambda chapter: reports / f"eval_{chapter}.json",
        continuity_report_path=lambda chapter: reports / f"continuity_{chapter}.json",
        chapter_causal_report_path=lambda chapter: reports / f"causal_{chapter}.json",
        guard_report_path=lambda chapter: reports / f"guard_{chapter}.json",
        reading_power_report_path=lambda chapter: reports / f"reading_power_{chapter}.json",
    )
    states.mkdir()
    for path, payload in (
        (layout.alignment_report_path(1), {"source_text_hash": old_hash, "alignment_score": 7.0}),
        (layout.continuity_report_path(1), {"source_text_hash": old_hash, "continuity_score": 7.0}),
        (layout.chapter_causal_report_path(1), {"source_text_hash": old_hash, "causal_score": 7.0}),
        (layout.eval_report_path(1), {"source_text_hash": old_hash, "overall_score": 7.0}),
        (
            layout.guard_report_path(1),
            {"source_text_hash": old_hash, "overall_compliance_rate": 1.0},
        ),
    ):
        storage.save_json(path, payload)

    async def _fake_prompt_repair(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            text=patched_text,
            chapter_repair_report=None,
            applied=True,
            used_deterministic_fallback=False,
            report_updated=False,
        )

    async def _fake_extract_and_validate(*_args: Any, **_kwargs: Any) -> ChapterOutcome:
        return ChapterOutcome(
            source_chapter=1,
            chapter_summary="刷新摘要",
            creative_report=CreativeReport(structured_summary="刷新创作报告"),
            chapter_exit_state=ChapterExitState(chapter_number=1),
        )

    async def _fake_refresh_reports(**kwargs: Any) -> Any:
        alignment = AlignmentReport(
            alignment_score=9.1,
            risk_level="low",
            conflict_level="low",
            summary="新对齐",
        )
        continuity = ContinuityReport(continuity_score=9.2, summary="新连贯")
        causal = CausalValidationReport(
            causal_score=9.3,
            summary="新因果",
            issues=[],
            causal_link_verified=True,
        )
        for path, report in (
            (layout.alignment_report_path(1), alignment),
            (layout.continuity_report_path(1), continuity),
            (layout.chapter_causal_report_path(1), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=patched_text,
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=patched_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=None,
            chapter_repair_report=kwargs.get("chapter_repair_report"),
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason="prompt_leak_llm_patch",
        )

    async def _fake_eval(context: Any, **kwargs: Any) -> EvalReport:
        report = EvalReport(overall_score=8.9, passed=True, summary="新质量")
        current_text = str(kwargs["current_text"])
        chapter_number = int(kwargs["chapter_number"])
        eval_bundle = kwargs["bundle"]
        context.storage.save_json(
            eval_bundle.layout.eval_report_path(chapter_number),
            {
                **report.model_dump(mode="json"),
                "source_text_hash": finalize._source_text_hash(current_text),
            },
        )
        return report

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _empty_memory_context(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(
        finalize_persist_impl, "repair_confirmed_prompt_leaks_with_patch", _fake_prompt_repair
    )
    monkeypatch.setattr(finalize_persist_impl, "extract_and_validate", _fake_extract_and_validate)
    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh_reports,
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "_run_contract_execution_audit_without_state_adjudication",
        _noop_async,
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "invalidate_downstream_generated_artifacts",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        finalize_persist_impl, "build_finalize_eval_memory_context", _empty_memory_context
    )
    monkeypatch.setattr(finalize_persist_impl, "evaluate_chapter_text", _fake_eval)

    runner = SimpleNamespace(
        _router=object(),
        _builder=SimpleNamespace(render=lambda *_args, **_kwargs: ""),
        _settings=SimpleNamespace(
            narrative_state_enabled=False,
            long_word_count_archive_gate_enabled=False,
        ),
        _storage=storage,
        _merger=object(),
        _rules=object(),
        _config=object(),
        _on_step=lambda *_args: None,
        _select_character_profiles=lambda *_args, **_kwargs: [],
        _compact_previous_creative_report=lambda report: report,
        _compress_prompt_context=lambda *_args, **_kwargs: {},
        _remove_opening_echo_from_previous=lambda text, _prev: (text, None),
        _apply_chapter_compaction=lambda **_kwargs: (_kwargs["state"], None),
        _finalize_volume_if_needed=_noop_async,
        _is_outline_option_enabled_for_task=lambda *_args, **_kwargs: False,
        has_memory_context=lambda: False,
        memory_context=None,
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=1, expected_word_count=600, goal="推进"),
        story_bible=SimpleNamespace(),
        style_profile=None,
        outline=SimpleNamespace(),
        character_bible=SimpleNamespace(),
        canon_state=StoryKernel(project_id="semantic_patch_demo"),
        canon_store=SimpleNamespace(save=lambda *_args, **_kwargs: None),
    )
    outcome = ChapterOutcome(
        source_chapter=1,
        chapter_summary="旧摘要",
        creative_report=CreativeReport(structured_summary="旧创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1),
    )

    result = await finalize.persist_results(
        runner,
        bundle,
        packet=SimpleNamespace(previous_chapter_ending=""),
        bridge=SimpleNamespace(causal_link=None),
        plan=SimpleNamespace(),
        outcome=outcome,
        current_text=old_text,
        performed_edits=0,
        alignment_report=AlignmentReport(
            alignment_score=7.0,
            risk_level="medium",
            conflict_level="low",
        ),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(continuity_score=7.0),
        causal_report=CausalValidationReport(causal_score=7.0, issues=[]),
        repair_plan=None,
        trace=SimpleNamespace(total_tokens=0, total_cost=0),
        chapter_number=1,
        eval_report=EvalReport(overall_score=7.0, passed=True),
        emit_evaluate_step=False,
    )

    assert result.current_text == patched_text
    assert result.eval_report.overall_score == pytest.approx(8.9)
    assert storage.load_json(layout.alignment_report_path(1))["source_text_hash"] == patched_hash
    assert storage.load_json(layout.continuity_report_path(1))["source_text_hash"] == patched_hash
    assert (
        storage.load_json(layout.chapter_causal_report_path(1))["source_text_hash"] == patched_hash
    )
    assert storage.load_json(layout.eval_report_path(1))["source_text_hash"] == patched_hash
    guard_payload = storage.load_json(layout.guard_report_path(1))
    assert guard_payload["source_text_hash"] == old_hash
    assert guard_payload["stale_after_text_change"] is True
    assert guard_payload["stale_expected_text_hash"] == patched_hash


def test_progression_ledger_uses_only_llm_accepted_candidate_ids() -> None:
    accepted = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=3,
        delta_type="knowledge",
        summary="沈念卿确认怀表纹样异常",
        entity_ids=["沈念卿"],
    )
    rejected = CandidateStateDelta(
        candidate_id="c2",
        chapter_number=3,
        delta_type="event",
        summary="提前完成第十章高潮",
        entity_ids=["主线"],
    )
    report = SimpleNamespace(
        candidates=[accepted, rejected],
        final_adjudication=SimpleNamespace(accepted_candidate_ids=["c1"]),
    )
    audit = SimpleNamespace(missing_required_progressions=[], unexpected_progressions=[])

    progressions = finalize._progression_texts_for_ledger(
        contract={"required_progressions": []},
        audit_report=audit,
        state_adjudication_report=report,
    )

    assert progressions == ["沈念卿确认怀表纹样异常"]


def test_progression_ledger_does_not_store_unadjudicated_candidates() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=3,
        delta_type="knowledge",
        summary="候选但未经最终状态裁判确认",
        entity_ids=["主线"],
    )
    report = SimpleNamespace(candidates=[candidate], final_adjudication=None)
    audit = SimpleNamespace(missing_required_progressions=[], unexpected_progressions=[])

    progressions = finalize._progression_texts_for_ledger(
        contract={"required_progressions": []},
        audit_report=audit,
        state_adjudication_report=report,
    )

    assert progressions == []


@pytest.mark.asyncio
async def test_contract_audit_runs_without_narrative_state_adjudication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeCandidateExtractor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, input_data: Any) -> list[CandidateStateDelta]:
            captured["extract_input"] = input_data
            return [
                CandidateStateDelta(
                    candidate_id="c1",
                    chapter_number=input_data.chapter_number,
                    delta_type="knowledge",
                    summary="发现怀表纹样异常",
                    entity_ids=["沈念卿"],
                )
            ]

    async def _fake_run_contract_audit(*_args, **kwargs):
        captured["audit_report"] = kwargs["state_adjudication_report"]
        return SimpleNamespace(verdict="accept")

    monkeypatch.setattr(
        finalize_report_impl, "CandidateStateDeltaExtractionStep", _FakeCandidateExtractor
    )
    monkeypatch.setattr(
        finalize_report_impl, "_run_contract_execution_audit", _fake_run_contract_audit
    )

    events: list[tuple[str, dict[str, Any]]] = []
    layout = SimpleNamespace(root=tmp_path, plans_dir=tmp_path / "plans")
    layout.plans_dir.mkdir()
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(
            chapter_number=3,
            title="第三章",
            main_plot_points=["发现怀表纹样异常"],
            beats_summary=[],
        ),
        canon_state=None,
    )
    packet = SimpleNamespace(
        known_characters=["沈念卿"],
        must_carry_forward=[],
        guard_constraints=[],
        previous_exit_state=None,
        active_relationships=[],
        active_plot_threads=[],
        milestone_window={},
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(long_contract_audit_enabled=True),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    plan = SimpleNamespace(
        required_state_transitions=[],
        relationship_evolution=[],
        closing_contract="完成怀表纹样异常的确认",
    )

    result = await finalize._run_contract_execution_audit_without_state_adjudication(
        runner,
        bundle,
        packet,
        plan,
        chapter_number=3,
        current_text="沈念卿发现怀表纹样异常。",
        trace=object(),
    )

    assert result is not None
    assert result.verdict == "accept"
    assert captured["extract_input"].chapter_contract["required_progressions"] == [
        "发现怀表纹样异常"
    ]
    assert captured["audit_report"].final_adjudication is None
    assert captured["audit_report"].candidates[0].summary == "发现怀表纹样异常"
    assert any(
        step == "candidate_state_deltas" and payload.get("source") == "contract_execution_audit"
        for step, payload in events
    )


@pytest.mark.asyncio
async def test_state_adjudication_non_blocking_repair_does_not_reject_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    final_adjudication = SimpleNamespace(
        verdict="needs_repair",
        severity="medium",
        confidence=0.9,
        repair_candidate_ids=["c7-1"],
        repair_issues=[{"candidate_id": "c7-1", "severity": "medium"}],
        should_block_archive=False,
        summary="仅 c7-1 需要修复，不阻断归档。",
    )
    report = SimpleNamespace(final_adjudication=final_adjudication)

    async def _fake_run_narrative_state_adjudication(**_kwargs):
        return report

    monkeypatch.setattr(
        finalize_report_impl,
        "run_narrative_state_adjudication",
        _fake_run_narrative_state_adjudication,
    )

    layout = SimpleNamespace(
        root=tmp_path,
        plans_dir=tmp_path / "plans",
        state_adjudication_report_path=lambda chapter: (
            tmp_path / f"chapter_{chapter:03d}_state_adjudication.json"
        ),
    )
    layout.plans_dir.mkdir()
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(narrative_state_max_repair_rounds=0),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=7, title="第七章"),
        canon_state=None,
    )
    packet = SimpleNamespace(
        known_characters=[],
        must_carry_forward=[],
        guard_constraints=[],
        previous_exit_state=None,
        active_relationships=[],
        active_plot_threads=[],
    )
    plan = SimpleNamespace(
        required_state_transitions=[],
        relationship_evolution=[],
        closing_contract="",
    )
    outcome = object()

    (
        text,
        returned_outcome,
        eval_report,
        returned_report,
    ) = await finalize._adjudicate_state_before_archive(
        runner,
        bundle,
        packet,
        bridge=object(),
        plan=plan,
        outcome=outcome,
        current_text="正文",
        chapter_number=7,
        trace=object(),
        continuity_report=object(),
        eval_report=None,
    )

    assert text == "正文"
    assert returned_outcome is outcome
    assert eval_report is None
    assert returned_report is report
    assert events[-1][0] == "state_adjudication_non_blocking_repair_remaining"
    assert events[-1][1]["repair_candidate_ids"] == ["c7-1"]


@pytest.mark.asyncio
async def test_state_adjudication_block_downgraded_after_repair_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When repair rounds are exhausted but should_block_archive is still True,
    the block must be downgraded to a warning instead of raising.

    This covers the scenario where critical not_covered contract targets
    cannot be resolved by text repair (they need new prose content, not
    text fixes), so the repair loop is doomed to fail.  Raising here
    causes an unrecoverable RuntimeError in the Desktop archive phase.
    """
    events: list[tuple[str, dict[str, Any]]] = []

    blocked_final = SimpleNamespace(
        verdict="needs_repair",
        severity="critical",
        confidence=0.85,
        repair_candidate_ids=["c3"],
        repair_issues=[{"candidate_id": "c3", "severity": "high"}],
        should_block_archive=True,
        summary="3个 critical not_covered 目标无候选支持",
    )
    still_blocked_final = SimpleNamespace(
        verdict="reject",
        severity="critical",
        confidence=0.8,
        repair_candidate_ids=[],
        repair_issues=[],
        should_block_archive=True,
        summary="修复后仍有3个 critical not_covered",
    )
    report_first = SimpleNamespace(final_adjudication=blocked_final, decisions=[], candidates=[])
    report_second = SimpleNamespace(
        final_adjudication=still_blocked_final, decisions=[], candidates=[]
    )

    call_count = [0]

    async def _fake_run_narrative_state_adjudication(**_kwargs):
        call_count[0] += 1
        return report_first if call_count[0] == 1 else report_second

    monkeypatch.setattr(
        finalize_report_impl,
        "run_narrative_state_adjudication",
        _fake_run_narrative_state_adjudication,
    )

    class _FakeRepairStep:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, _input):
            return "修复后正文"

    monkeypatch.setattr(finalize_report_impl, "RepairAdjudicatedIssueStep", _FakeRepairStep)
    monkeypatch.setattr(
        finalize_report_impl,
        "_clean_and_validate_chapter_text",
        lambda *args, **kwargs: kwargs.get("current_text") or "修复后正文",
    )

    async def _fake_extract_and_validate(*args, **kwargs):
        return object()

    monkeypatch.setattr(finalize_report_impl, "extract_and_validate", _fake_extract_and_validate)
    monkeypatch.setattr(finalize_report_impl, "_build_current_state_payload", lambda *a, **kw: {})
    monkeypatch.setattr(
        finalize_report_impl, "_build_chapter_contract_payload", lambda *a, **kw: {}
    )

    async def _fake_contract_audit(*args, **kwargs):
        pass

    monkeypatch.setattr(finalize_report_impl, "_run_contract_execution_audit", _fake_contract_audit)

    layout = SimpleNamespace(
        root=tmp_path,
        plans_dir=tmp_path / "plans",
        state_adjudication_report_path=lambda chapter: (
            tmp_path / f"chapter_{chapter:03d}_state_adjudication.json"
        ),
    )
    layout.plans_dir.mkdir()
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(narrative_state_max_repair_rounds=1),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=1, title="第一章"),
        canon_state=None,
    )
    packet = SimpleNamespace(
        known_characters=[],
        must_carry_forward=[],
        guard_constraints=[],
        previous_exit_state=None,
        active_relationships=[],
        active_plot_threads=[],
    )
    plan = SimpleNamespace(
        required_state_transitions=[],
        relationship_evolution=[],
        closing_contract="",
    )

    text, _outcome, _eval, _report = await finalize._adjudicate_state_before_archive(
        runner,
        bundle,
        packet,
        bridge=object(),
        plan=plan,
        outcome=object(),
        current_text="原始正文",
        chapter_number=1,
        trace=object(),
        continuity_report=object(),
        eval_report=None,
    )

    downgrade_events = [e for e in events if e[0] == "state_adjudication_downgraded_to_warning"]
    assert len(downgrade_events) == 1
    assert downgrade_events[0][1]["reason"] == "repair_rounds_exhausted"
    assert downgrade_events[0][1]["rounds_used"] == 1
    assert downgrade_events[0][1]["max_rounds"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# P0-3: _is_reading_power_stale — rejects stale report from cancelled run
# ═══════════════════════════════════════════════════════════════════════════


class TestIsReadingPowerStale:
    """P0-3 regression: a reading_power report with a mismatched
    source_text_hash must be rejected, otherwise stale data from a prior
    cancelled run leaks into the current chapter's score (弈局谋心
    chapter 1, file mtime 14:56 vs run end 14:11).
    """

    def test_none_rp_data_not_stale(self) -> None:
        from novel_forge.pipeline.long.stages.finalize import _is_reading_power_stale

        is_stale, stored, current = _is_reading_power_stale(None, "any text")
        assert is_stale is False
        assert stored is None
        assert current is None

    def test_empty_current_text_not_stale(self) -> None:
        from novel_forge.pipeline.long.stages.finalize import _is_reading_power_stale

        rp = {"source_text_hash": "abc123", "overall_score": 10.0}
        is_stale, stored, current = _is_reading_power_stale(rp, "")
        assert is_stale is False
        assert stored is None

    def test_missing_stored_hash_not_stale(self) -> None:
        from novel_forge.pipeline.long.stages.finalize import _is_reading_power_stale

        rp = {"overall_score": 10.0}  # no source_text_hash field
        is_stale, stored, current = _is_reading_power_stale(rp, "current text")
        assert is_stale is False
        assert stored is None

    def test_matching_hash_not_stale(self) -> None:
        from novel_forge.pipeline.long.stages.finalize import (
            _is_reading_power_stale,
            _source_text_hash,
        )

        text = "合卺酒送至面前,金杯在烛光下泛着暖色。"
        rp = {"source_text_hash": _source_text_hash(text), "overall_score": 10.0}
        is_stale, stored, current = _is_reading_power_stale(rp, text)
        assert is_stale is False
        assert stored == _source_text_hash(text)
        assert current == _source_text_hash(text)

    def test_mismatched_hash_is_stale(self) -> None:
        """The P0-3 production case: chapter 1 run saw a report from a
        cancelled prior run whose text hash did not match the finalized
        text — this must be detected and the report rejected."""
        from novel_forge.pipeline.long.stages.finalize import (
            _is_reading_power_stale,
            _source_text_hash,
        )

        # Pretend a prior run evaluated this draft and wrote a report.
        stale_text = "残玉背面「昱」字刻痕，由玄昱当面挑明"
        stale_report = {
            "source_text_hash": _source_text_hash(stale_text),
            "overall_score": 10.0,
            "is_fallback": False,
            "evaluation_status": "ok",
        }

        # Now the chapter was rewritten and persisted with different text.
        current_text = "广袖垂落时，薄刃的锋沿硌着腕骨。沈清漪在红烛光影里收拢指尖"

        is_stale, stored, current = _is_reading_power_stale(stale_report, current_text)
        assert is_stale is True
        assert stored == _source_text_hash(stale_text)
        assert current == _source_text_hash(current_text)
        assert stored != current


# ═══════════════════════════════════════════════════════════════════════════
# Quality block ordering: _enforce_archive_hard_quality_blocks must fire
# BEFORE canon state update and text save to prevent state desync.
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_finalize_quality_block_before_canon_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When _enforce_archive_hard_quality_blocks raises ConsistencyViolationError,
    canon state must NOT be updated and chapter text must NOT be saved.

    Regression test: the quality block check was previously called AFTER canon write,
    causing state desync — canon advanced but the chapter was rejected.
    """

    class _Storage:
        def __init__(self) -> None:
            self.save_text_called = False
            self.saved_texts: list[str] = []

        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

        def save_text(self, path: Any, text: str) -> None:
            self.save_text_called = True
            self.saved_texts.append(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        def load_text(self, path: Any) -> str:
            return path.read_text(encoding="utf-8")

        def exists(self, path: Any) -> bool:
            return path.exists()

    storage = _Storage()
    reports = tmp_path / "reports"
    states = tmp_path / "states"
    chapters = tmp_path / "chapters"
    layout = SimpleNamespace(
        root=tmp_path,
        states_dir=states,
        chapter_path=lambda chapter: chapters / f"chapter_{chapter}.md",
        chapter_artifact_path=lambda chapter, artifact_type: (
            states / f"chapter_{chapter:03d}_artifacts" / f"{artifact_type}.json"
        ),
        alignment_report_path=lambda chapter: reports / f"alignment_{chapter}.json",
        eval_report_path=lambda chapter: reports / f"eval_{chapter}.json",
        continuity_report_path=lambda chapter: reports / f"continuity_{chapter}.json",
        chapter_causal_report_path=lambda chapter: reports / f"causal_{chapter}.json",
        guard_report_path=lambda chapter: reports / f"guard_{chapter}.json",
        reading_power_report_path=lambda chapter: reports / f"reading_power_{chapter}.json",
    )
    states.mkdir()

    async def _fake_prompt_repair(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            text=_kwargs["current_text"],
            chapter_repair_report=None,
            applied=False,
            used_deterministic_fallback=False,
            report_updated=False,
        )

    async def _fake_knowledge_boundary_audit(**_kwargs: Any) -> list[Any]:
        return []

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    kernel_update_called = False

    async def _fake_write_kernel(*_args: Any, **_kwargs: Any) -> None:
        nonlocal kernel_update_called
        kernel_update_called = True

    async def _fake_refresh_current_reports(**kwargs: Any) -> Any:
        current_hash = finalize._source_text_hash(kwargs["current_text"])
        alignment = kwargs["alignment_report"].model_copy(
            update={"source_text_hash": current_hash}
        )
        continuity = kwargs["continuity_report"].model_copy(
            update={"source_text_hash": current_hash}
        )
        causal = kwargs["causal_report"].model_copy(update={"source_text_hash": current_hash})
        for path, report in (
            (kwargs["bundle"].layout.alignment_report_path(1), alignment),
            (kwargs["bundle"].layout.continuity_report_path(1), continuity),
            (kwargs["bundle"].layout.chapter_causal_report_path(1), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=kwargs["current_text"],
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=current_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=None,
            chapter_repair_report=kwargs.get("chapter_repair_report"),
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason=kwargs.get("stale_reason", ""),
        )

    monkeypatch.setattr(
        finalize_persist_impl, "repair_confirmed_prompt_leaks_with_patch", _fake_prompt_repair
    )
    monkeypatch.setattr(
        finalize_persist_impl, "run_knowledge_boundary_audit", _fake_knowledge_boundary_audit
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "_run_contract_execution_audit_without_state_adjudication",
        _noop_async,
    )
    monkeypatch.setattr(
        finalize_persist_impl, "_write_chapter_outcome_to_story_kernel", _fake_write_kernel
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh_current_reports,
    )

    runner = SimpleNamespace(
        _router=object(),
        _builder=SimpleNamespace(render=lambda *_args, **_kwargs: ""),
        _settings=SimpleNamespace(
            narrative_state_enabled=False,
            long_word_count_archive_gate_enabled=False,
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _storage=storage,
        _merger=object(),
        _rules=object(),
        _config=object(),
        _on_step=lambda *_args: None,
        _select_character_profiles=lambda *_args, **_kwargs: [],
        _compact_previous_creative_report=lambda report: report,
        _compress_prompt_context=lambda *_args, **_kwargs: {},
        _remove_opening_echo_from_previous=lambda text, _prev: (text, None),
        _apply_chapter_compaction=lambda **_kwargs: (_kwargs["state"], None),
        _finalize_volume_if_needed=_noop_async,
        _is_outline_option_enabled_for_task=lambda *_args, **_kwargs: False,
        has_memory_context=lambda: False,
        memory_context=None,
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=1, expected_word_count=600, goal="推进"),
        story_bible=SimpleNamespace(),
        style_profile=None,
        outline=SimpleNamespace(),
        character_bible=SimpleNamespace(),
        canon_state=StoryKernel(project_id="quality_block_demo"),
        canon_store=SimpleNamespace(save=lambda *_args, **_kwargs: None),
    )
    outcome = ChapterOutcome(
        source_chapter=1,
        chapter_summary="测试摘要",
        creative_report=CreativeReport(structured_summary="测试创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1),
    )

    with pytest.raises(ConsistencyViolationError, match="因果分"):
        await finalize.persist_results(
            runner,
            bundle,
            packet=SimpleNamespace(previous_chapter_ending=""),
            bridge=SimpleNamespace(causal_link=None),
            plan=SimpleNamespace(),
            outcome=outcome,
            current_text="正文" * 300,
            performed_edits=0,
            alignment_report=AlignmentReport(
                alignment_score=9.0,
                risk_level="low",
                conflict_level="low",
                summary="对齐通过",
            ),
            chapter_repair_report=None,
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=0.9, issues=[]),
            repair_plan=None,
            trace=SimpleNamespace(total_tokens=0, total_cost=0),
            chapter_number=1,
            eval_report=EvalReport(overall_score=8.0, passed=True),
            emit_evaluate_step=False,
        )

    assert storage.save_text_called is False, (
        "save_text must NOT be called when quality block fires"
    )
    assert kernel_update_called is False, (
        "_write_chapter_outcome_to_story_kernel must NOT be called when quality block fires"
    )


def _build_persist_results_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    inline_eval_report: EvalReport | None = None,
) -> tuple[Any, Any, Any, Any]:
    class _Storage:
        def __init__(self) -> None:
            self.save_text_called = False
            self.saved_texts: list[str] = []

        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

        def save_text(self, path: Any, text: str) -> None:
            self.save_text_called = True
            self.saved_texts.append(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        def load_text(self, path: Any) -> str:
            return path.read_text(encoding="utf-8")

        def exists(self, path: Any) -> bool:
            return path.exists()

    storage = _Storage()
    reports = tmp_path / "reports"
    states = tmp_path / "states"
    chapters = tmp_path / "chapters"
    layout = SimpleNamespace(
        root=tmp_path,
        states_dir=states,
        chapter_path=lambda chapter: chapters / f"chapter_{chapter}.md",
        chapter_artifact_path=lambda chapter, artifact_type: (
            states / f"chapter_{chapter:03d}_artifacts" / f"{artifact_type}.json"
        ),
        alignment_report_path=lambda chapter: reports / f"alignment_{chapter}.json",
        eval_report_path=lambda chapter: reports / f"eval_{chapter}.json",
        continuity_report_path=lambda chapter: reports / f"continuity_{chapter}.json",
        chapter_causal_report_path=lambda chapter: reports / f"causal_{chapter}.json",
        guard_report_path=lambda chapter: reports / f"guard_{chapter}.json",
        reading_power_report_path=lambda chapter: reports / f"reading_power_{chapter}.json",
    )
    states.mkdir()

    async def _fake_prompt_repair(**kwargs: Any) -> Any:
        return SimpleNamespace(
            text=kwargs["current_text"],
            chapter_repair_report=None,
            applied=False,
            used_deterministic_fallback=False,
            report_updated=False,
        )

    async def _fake_knowledge_boundary_audit(**_kwargs: Any) -> list[Any]:
        return []

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _fake_write_kernel(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _fake_build_memory_context(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def _fake_evaluate_chapter_text(*_args: Any, **_kwargs: Any) -> EvalReport:
        if inline_eval_report is None:
            return EvalReport(overall_score=8.0, passed=True)
        return inline_eval_report

    async def _fake_refresh_current_reports(**kwargs: Any) -> Any:
        current_text = str(kwargs["current_text"])
        current_hash = finalize._source_text_hash(current_text)

        def current_copy(report: Any, fallback: Any) -> Any:
            selected = report if report is not None else fallback
            return selected.model_copy(update={"source_text_hash": current_hash})

        alignment = current_copy(
            kwargs.get("alignment_report"),
            AlignmentReport(alignment_score=9.0, risk_level="low", conflict_level="low"),
        )
        continuity = current_copy(
            kwargs.get("continuity_report"),
            ContinuityReport(continuity_score=9.0),
        )
        causal = current_copy(
            kwargs.get("causal_report"),
            CausalValidationReport(causal_score=9.0, issues=[]),
        )
        for path, report in (
            (kwargs["bundle"].layout.alignment_report_path(kwargs["chapter_number"]), alignment),
            (kwargs["bundle"].layout.continuity_report_path(kwargs["chapter_number"]), continuity),
            (kwargs["bundle"].layout.chapter_causal_report_path(kwargs["chapter_number"]), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=current_text,
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=current_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=kwargs.get("reading_power_report"),
            chapter_repair_report=kwargs.get("chapter_repair_report"),
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason=kwargs.get("stale_reason", ""),
        )

    monkeypatch.setattr(
        finalize_persist_impl, "repair_confirmed_prompt_leaks_with_patch", _fake_prompt_repair
    )
    monkeypatch.setattr(
        finalize_persist_impl, "run_knowledge_boundary_audit", _fake_knowledge_boundary_audit
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "_run_contract_execution_audit_without_state_adjudication",
        _noop_async,
    )
    monkeypatch.setattr(
        finalize_persist_impl, "_write_chapter_outcome_to_story_kernel", _fake_write_kernel
    )
    monkeypatch.setattr(
        finalize_persist_impl, "build_finalize_eval_memory_context", _fake_build_memory_context
    )
    monkeypatch.setattr(finalize_persist_impl, "evaluate_chapter_text", _fake_evaluate_chapter_text)
    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh_current_reports,
    )

    runner = SimpleNamespace(
        _router=object(),
        _builder=SimpleNamespace(render=lambda *_args, **_kwargs: ""),
        _settings=SimpleNamespace(
            narrative_state_enabled=False,
            long_word_count_archive_gate_enabled=False,
            long_min_accept_score=5.0,
            long_continuity_hard_block_threshold=4.0,
            long_causal_hard_block_threshold=4.0,
        ),
        _storage=storage,
        _merger=object(),
        _rules=object(),
        _config=object(),
        _on_step=lambda *_args: None,
        _select_character_profiles=lambda *_args, **_kwargs: [],
        _compact_previous_creative_report=lambda report: report,
        _compress_prompt_context=lambda *_args, **_kwargs: {},
        _remove_opening_echo_from_previous=lambda text, _prev: (text, None),
        _apply_chapter_compaction=lambda **_kwargs: (_kwargs["state"], None),
        _finalize_volume_if_needed=_noop_async,
        _is_outline_option_enabled_for_task=lambda *_args, **_kwargs: False,
        has_memory_context=lambda: False,
        memory_context=None,
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=1, expected_word_count=600, goal="推进"),
        story_bible=SimpleNamespace(),
        style_profile=None,
        outline=SimpleNamespace(),
        character_bible=SimpleNamespace(),
        canon_state=StoryKernel(project_id="eval_floor_demo"),
        canon_store=SimpleNamespace(save=lambda *_args, **_kwargs: None),
    )
    outcome = ChapterOutcome(
        source_chapter=1,
        chapter_summary="测试摘要",
        creative_report=CreativeReport(structured_summary="测试创作报告"),
        chapter_exit_state=ChapterExitState(chapter_number=1),
    )
    return storage, layout, runner, (bundle, outcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_gateway", [True, False])
async def test_final_report_refresh_failure_preserves_draft_and_blocks_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    transient_gateway: bool,
) -> None:
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch,
        tmp_path,
    )
    layout.chapter_review_draft_path = lambda chapter: tmp_path / "drafts" / f"review_{chapter}.md"
    received_policy: list[Any] = []

    async def _failed_refresh(**kwargs: Any) -> Any:
        received_policy.append(kwargs.get("failure_policy"))
        if transient_gateway:
            raise ModelGatewayError("all routes open", is_transient=True)
        raise RuntimeError("refresh parser invariant failed")

    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _failed_refresh,
    )
    current_text = "待归档终稿" * 200
    expected_error = ModelGatewayError if transient_gateway else FinalReportFreshnessError

    with pytest.raises(expected_error) as raised:
        await finalize.persist_results(
            runner,
            bundle,
            packet=SimpleNamespace(previous_chapter_ending=""),
            bridge=SimpleNamespace(causal_link=None),
            plan=SimpleNamespace(),
            outcome=outcome,
            current_text=current_text,
            performed_edits=1,
            alignment_report=AlignmentReport(alignment_score=9.0),
            chapter_repair_report=None,
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            repair_plan=None,
            trace=SimpleNamespace(total_tokens=0, total_cost=0),
            chapter_number=1,
            eval_report=EvalReport(overall_score=8.0, passed=True),
            emit_evaluate_step=False,
            force_mark_quality_reports_stale=True,
            quality_reports_stale_reason="terminal_humanize",
        )

    assert received_policy == [finalize.ReportRefreshFailurePolicy.FAIL_CLOSED]
    assert layout.chapter_review_draft_path(1).read_text(encoding="utf-8") == current_text
    assert not layout.chapter_path(1).exists()
    if transient_gateway:
        assert raised.value.is_transient_error is True
        assert raised.value.context["stage"] == "finalize_report_refresh"
        assert raised.value.context["retryable"] is True


@pytest.mark.asyncio
async def test_finalize_quality_block_enforces_eval_floor_when_eval_report_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When pre-archive text mutation nulls eval_report, the gate must re-run
    evaluation inline before checking the eval-score floor. Without this hoist,
    `if eval_report is not None` would silently skip the floor and a 2.0-score
    chapter would slip through.
    """
    low_score_report = EvalReport(overall_score=2.0, passed=False)
    storage, _layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path, inline_eval_report=low_score_report
    )

    with pytest.raises(ConsistencyViolationError, match="评估分 2.0 低于最低可接受线"):
        await finalize.persist_results(
            runner,
            bundle,
            packet=SimpleNamespace(previous_chapter_ending=""),
            bridge=SimpleNamespace(causal_link=None),
            plan=SimpleNamespace(),
            outcome=outcome,
            current_text="正文" * 300,
            performed_edits=0,
            alignment_report=AlignmentReport(
                alignment_score=9.0,
                risk_level="low",
                conflict_level="low",
                summary="对齐通过",
            ),
            chapter_repair_report=None,
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
            repair_plan=None,
            trace=SimpleNamespace(total_tokens=0, total_cost=0),
            chapter_number=1,
            eval_report=None,
            emit_evaluate_step=False,
        )

    assert storage.save_text_called is False, (
        "save_text must NOT be called when re-evaluated score falls below floor"
    )


@pytest.mark.asyncio
async def test_persist_results_refreshes_targeted_reports_before_archive_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path
    )
    events: list[tuple[str, Any]] = []
    runner._on_step = lambda step, payload=None: events.append((step, payload))
    refresh_calls: list[dict[str, Any]] = []

    async def _fake_refresh_reports(**kwargs: Any) -> Any:
        refresh_calls.append(kwargs)
        refreshed_hash = finalize._source_text_hash(kwargs["current_text"])
        alignment = AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="刷新对齐通过",
            source_text_hash=refreshed_hash,
        )
        continuity = ContinuityReport(
            review_mode="full_review",
            continuity_score=9.1,
            summary="刷新连贯通过",
            source_text_hash=refreshed_hash,
        )
        causal = CausalValidationReport(
            review_mode="full_review",
            causal_score=9.2,
            summary="刷新因果通过",
            issues=[],
            causal_link_verified=True,
            source_text_hash=refreshed_hash,
        )
        for path, report in (
            (layout.alignment_report_path(1), alignment),
            (layout.continuity_report_path(1), continuity),
            (layout.chapter_causal_report_path(1), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=kwargs["current_text"],
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=refreshed_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=None,
            chapter_repair_report=kwargs.get("chapter_repair_report"),
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason=kwargs.get("stale_reason", ""),
        )

    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh_reports,
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "invalidate_downstream_generated_artifacts",
        lambda *_args, **_kwargs: [],
    )

    current_text = "正文" * 300
    current_hash = finalize._source_text_hash(current_text)
    result = await finalize.persist_results(
        runner,
        bundle,
        packet=SimpleNamespace(previous_chapter_ending=""),
        bridge=SimpleNamespace(causal_link=None),
        plan=SimpleNamespace(),
        outcome=outcome,
        current_text=current_text,
        performed_edits=0,
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="对齐通过",
        ),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(
            review_mode="targeted_recheck",
            continuity_score=0.9,
            summary="点验连贯低分但无问题",
            issues=[],
            source_text_hash=current_hash,
        ),
        causal_report=CausalValidationReport(
            review_mode="targeted_recheck",
            causal_score=0.7,
            summary="点验因果仍有问题",
            issues=[],
            source_text_hash=current_hash,
        ),
        repair_plan=None,
        trace=SimpleNamespace(total_tokens=0, total_cost=0),
        chapter_number=1,
        eval_report=EvalReport(overall_score=8.0, passed=True),
        emit_evaluate_step=False,
    )

    assert result.continuity_report.continuity_score == pytest.approx(9.1)
    assert result.causal_report is not None
    assert result.causal_report.causal_score == pytest.approx(9.2)
    assert storage.save_text_called is True
    assert refresh_calls
    assert refresh_calls[0]["causal_recheck_mode"] is False
    assert refresh_calls[0]["causal_strict_review"] is False
    refresh_event = next(
        payload for step, payload in events if step == "archive_quality_reports_refresh_required"
    )
    assert "continuity_review_mode_targeted_recheck_before_archive" in refresh_event["reasons"]
    assert "causal_review_mode_targeted_recheck_before_archive" in refresh_event["reasons"]


@pytest.mark.asyncio
async def test_persist_results_consumes_word_count_stale_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path
    )
    refresh_calls: list[dict[str, Any]] = []
    current_text = "字数精修后正文" * 300
    current_hash = finalize._source_text_hash(current_text)

    async def _fake_refresh_reports(**kwargs: Any) -> Any:
        refresh_calls.append(kwargs)
        alignment = AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="字数精修后对齐通过",
            source_text_hash=current_hash,
        )
        continuity = ContinuityReport(
            review_mode="full_review",
            continuity_score=9.1,
            summary="字数精修后连续通过",
            source_text_hash=current_hash,
        )
        causal = CausalValidationReport(
            review_mode="full_review",
            causal_score=9.2,
            summary="字数精修后因果通过",
            issues=[],
            source_text_hash=current_hash,
        )
        for path, report in (
            (layout.alignment_report_path(1), alignment),
            (layout.continuity_report_path(1), continuity),
            (layout.chapter_causal_report_path(1), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=kwargs["current_text"],
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=current_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=None,
            chapter_repair_report=None,
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason=kwargs.get("stale_reason", ""),
        )

    async def _fake_extract_after_refresh(*_args: Any, **_kwargs: Any) -> Any:
        return outcome

    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh_reports,
    )
    monkeypatch.setattr(finalize_persist_impl, "extract_and_validate", _fake_extract_after_refresh)
    monkeypatch.setattr(
        finalize_persist_impl,
        "invalidate_downstream_generated_artifacts",
        lambda *_args, **_kwargs: [],
    )

    result = await finalize.persist_results(
        runner,
        bundle,
        packet=SimpleNamespace(previous_chapter_ending=""),
        bridge=SimpleNamespace(causal_link=None),
        plan=SimpleNamespace(),
        outcome=outcome,
        current_text=current_text,
        performed_edits=1,
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
            source_text_hash="old",
        ),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(
            review_mode="full_review",
            continuity_score=9.0,
            summary="旧连续",
            source_text_hash="old",
        ),
        causal_report=CausalValidationReport(
            review_mode="full_review",
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            source_text_hash="old",
        ),
        repair_plan=None,
        trace=SimpleNamespace(total_tokens=0, total_cost=0),
        chapter_number=1,
        eval_report=EvalReport(overall_score=8.0, passed=True),
        emit_evaluate_step=False,
        force_mark_quality_reports_stale=True,
        quality_reports_stale_reason="word_count_polish_before_archive",
    )

    assert refresh_calls
    assert "word_count_polish_before_archive" in refresh_calls[0]["stale_reason"]
    assert result.alignment_report.source_text_hash == current_hash
    assert result.continuity_report.source_text_hash == current_hash
    assert result.causal_report is not None
    assert result.causal_report.source_text_hash == current_hash
    assert storage.save_text_called is True


@pytest.mark.asyncio
async def test_finalize_quality_block_saves_orphan_draft_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When the hard quality gate raises ConsistencyViolationError, the rejected
    chapter text must be preserved as an orphan draft for debugging — mirroring
    the upstream-revision orphan path.
    """
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path
    )
    orphan_calls: list[dict[str, Any]] = []

    def _fake_save_orphaned(layout_arg, chapter_number, text, *, reason, expected_fingerprint=None):
        orphan_calls.append(
            {
                "chapter_number": chapter_number,
                "text": text,
                "reason": reason,
            }
        )
        return tmp_path / f"orphan_{chapter_number}.md"

    monkeypatch.setattr(finalize_persist_impl, "save_orphaned_chapter_draft", _fake_save_orphaned)

    rejected_text = "正文" * 300
    with pytest.raises(ConsistencyViolationError, match="因果分"):
        await finalize.persist_results(
            runner,
            bundle,
            packet=SimpleNamespace(previous_chapter_ending=""),
            bridge=SimpleNamespace(causal_link=None),
            plan=SimpleNamespace(),
            outcome=outcome,
            current_text=rejected_text,
            performed_edits=0,
            alignment_report=AlignmentReport(
                alignment_score=9.0,
                risk_level="low",
                conflict_level="low",
                summary="对齐通过",
            ),
            chapter_repair_report=None,
            continuity_report=ContinuityReport(continuity_score=9.0),
            causal_report=CausalValidationReport(causal_score=0.9, issues=[]),
            repair_plan=None,
            trace=SimpleNamespace(total_tokens=0, total_cost=0),
            chapter_number=1,
            eval_report=EvalReport(overall_score=8.0, passed=True),
            emit_evaluate_step=False,
        )

    assert storage.save_text_called is False
    assert len(orphan_calls) == 1, "orphan draft must be persisted exactly once on rejection"
    assert orphan_calls[0]["reason"] == "archive_hard_quality_block"
    assert orphan_calls[0]["chapter_number"] == 1
    assert orphan_calls[0]["text"] == rejected_text


@pytest.mark.asyncio
@pytest.mark.parametrize("coauthor_acceptance", [False, True])
async def test_terminal_humanize_runs_before_hard_gate_and_refreshes_reports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    coauthor_acceptance,
) -> None:
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path
    )
    order: list[str] = []
    events: list[tuple[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    original_hard_gate = finalize._enforce_archive_hard_quality_blocks
    runner._on_step = lambda step, payload=None: events.append((step, payload))

    def _record_hard_gate(*args: Any, **kwargs: Any) -> None:
        order.append("hard_gate")
        original_hard_gate(*args, **kwargs)

    async def _terminal_humanize(text: str) -> str:
        order.append("terminal_humanize")
        return text + "拟人化收束。"

    async def _refresh_reports_after_humanize(**kwargs: Any) -> Any:
        order.append("refresh_quality")
        fresh_hash = finalize._source_text_hash(kwargs["current_text"])
        alignment = AlignmentReport(
                alignment_score=9.0,
                risk_level="low",
                conflict_level="low",
                summary="刷新对齐通过",
                source_text_hash=fresh_hash,
            )
        continuity = ContinuityReport(
                review_mode="full_review",
                continuity_score=9.0,
                summary="刷新连贯通过",
                source_text_hash=fresh_hash,
            )
        causal = CausalValidationReport(
                review_mode="full_review",
                causal_score=9.0,
                summary="刷新因果通过",
                issues=[],
                causal_link_verified=True,
                source_text_hash=fresh_hash,
            )
        for path, report in (
            (layout.alignment_report_path(1), alignment),
            (layout.continuity_report_path(1), continuity),
            (layout.chapter_causal_report_path(1), causal),
        ):
            _save_bound_review_report(
                storage=storage,
                path=path,
                report=report,
                current_text=kwargs["current_text"],
                packet=kwargs["packet"],
                bridge=kwargs["bridge"],
                plan=kwargs["plan"],
                bundle=kwargs["bundle"],
            )
        return SimpleNamespace(
            current_text_hash=fresh_hash,
            alignment_report=alignment,
            continuity_report=continuity,
            causal_report=causal,
            reading_power_report=None,
            chapter_repair_report=kwargs.get("chapter_repair_report"),
            guard_compliance_report=None,
            guard_findings=[],
            guard_tickets=[],
            stale_reason=kwargs.get("stale_reason", ""),
        )

    async def _extract_after_humanize(*_args: Any, **_kwargs: Any) -> Any:
        order.append("extract")
        return outcome

    async def _eval_after_humanize(*_args: Any, **kwargs: Any) -> EvalReport:
        order.append("evaluate")
        return EvalReport(
            overall_score=8.0,
            passed=True,
            source_text_hash=finalize._source_text_hash(kwargs["current_text"]),
        )

    async def _guard_after_humanize(**kwargs: Any) -> Any:
        return SimpleNamespace(
            guard_compliance_report={
                "overall_compliance_rate": 1.0,
                "source_text_hash": finalize._source_text_hash(kwargs["current_text"]),
            },
            guard_findings=[],
            guard_tickets=[],
        )

    def _capture_artifact(*_args: Any, **kwargs: Any) -> None:
        artifacts.append(kwargs)

    monkeypatch.setattr(
        finalize_persist_impl, "_enforce_archive_hard_quality_blocks", _record_hard_gate
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "refresh_quality_reports_after_semantic_text_change",
        _refresh_reports_after_humanize,
    )
    monkeypatch.setattr(finalize_persist_impl, "extract_and_validate", _extract_after_humanize)
    monkeypatch.setattr(finalize_persist_impl, "evaluate_chapter_text", _eval_after_humanize)
    monkeypatch.setattr(
        finalize_persist_impl, "run_guard_compliance_for_final_text", _guard_after_humanize
    )
    monkeypatch.setattr(
        finalize_persist_impl, "_persist_finalize_stage_artifact", _capture_artifact
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "invalidate_downstream_generated_artifacts",
        lambda *_args, **_kwargs: [],
    )

    original_text = "正文" * 300
    original_hash = finalize._source_text_hash(original_text)
    for path in (
        layout.alignment_report_path(1),
        layout.continuity_report_path(1),
        layout.chapter_causal_report_path(1),
        layout.eval_report_path(1),
    ):
        storage.save_json(path, {"source_text_hash": original_hash})

    persist_args = dict(
        packet=SimpleNamespace(previous_chapter_ending=""),
        bridge=SimpleNamespace(causal_link=None),
        plan=SimpleNamespace(),
        outcome=outcome,
        current_text=original_text,
        performed_edits=0,
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="对齐通过",
        ),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(continuity_score=9.0),
        causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
        repair_plan=None,
        trace=SimpleNamespace(total_tokens=0, total_cost=0),
        chapter_number=1,
        eval_report=EvalReport(overall_score=8.0, passed=True),
        emit_evaluate_step=False,
        terminal_humanize=_terminal_humanize,
    )

    if coauthor_acceptance:
        from novel_forge.core.authoring import AuthoringPolicy
        from novel_forge.persistence.authoring_store import (
            AuthoringAcceptanceRequired,
            AuthoringExecution,
            AuthoringStore,
            active_authoring,
            story_input_version,
        )

        store = AuthoringStore(tmp_path)
        policy = store.set_policy(AuthoringPolicy(mode="coauthor"), expected_version=0)
        policy = store.start(
            expected_version=policy.version, input_version=story_input_version(tmp_path)
        )

        def accept(text: str, candidate: str):
            approval = store.approve(
                action="archive",
                chapter=1,
                candidate_version=candidate,
                input_version=story_input_version(tmp_path),
                policy_version=policy.version,
            )
            return active_authoring.set(
                AuthoringExecution(
                    tmp_path, policy.version, 1, approval, candidate,
                    finalize._source_text_hash(text),
                )
            )

        token = accept(original_text, "before-terminal-refinement")
        try:
            with pytest.raises(AuthoringAcceptanceRequired) as waiting:
                await finalize.persist_results(runner, bundle, **persist_args)
            assert not storage.saved_texts
            assert not layout.chapter_path(1).exists()
            assert bundle.canon_state.current_chapter == 0
            final_text = waiting.value.text
            assert final_text == original_text + "拟人化收束。"
            assert waiting.value.review_state["eval_report"].source_text_hash == (
                finalize._source_text_hash(final_text)
            )
        finally:
            active_authoring.reset(token)

        # The resumed handler marks this exact text as already refined; do not
        # run optional refinement again after the author's second acceptance.
        persist_args.update(
            current_text=final_text,
            terminal_humanize=None,
            **{
                key: waiting.value.review_state[key]
                for key in (
                    "outcome", "alignment_report", "continuity_report", "causal_report", "eval_report"
                )
            },
        )
        token = accept(final_text, "accepted-final-candidate")
        try:
            result = await finalize.persist_results(runner, bundle, **persist_args)
        finally:
            active_authoring.reset(token)
        assert len(storage.saved_texts) == 1
        assert order.count("terminal_humanize") == 1
        assert order.count("hard_gate") == 2
    else:
        result = await finalize.persist_results(runner, bundle, **persist_args)
        assert order == ["terminal_humanize", "refresh_quality", "extract", "evaluate", "hard_gate"]
    text_change_event = next(
        payload for step, payload in events if step == "text_changed_before_archive"
    )
    assert text_change_event["reason"] == "terminal_humanize_before_archive_gate"
    assert text_change_event["refresh_quality"] is True
    assert text_change_event["refresh_eval"] is True
    assert text_change_event["refresh_outcome"] is True
    assert storage.saved_texts[-1] == original_text + "拟人化收束。"
    assert result.current_text == original_text + "拟人化收束。"
    final_payload = artifacts[-1]["payload"]
    metadata = final_payload["terminal_humanize"]
    if not coauthor_acceptance:
        assert metadata["applied"] is True
        assert metadata["reextract_after_humanize"] is True
        assert metadata["reevaluate_after_humanize"] is True
        assert metadata["before_text_hash"] == finalize._source_text_hash(original_text)
        assert metadata["after_text_hash"] == finalize._source_text_hash(storage.saved_texts[-1])
    final_hash = finalize._source_text_hash(storage.saved_texts[-1])
    for path in (
        layout.alignment_report_path(1),
        layout.continuity_report_path(1),
        layout.chapter_causal_report_path(1),
        layout.eval_report_path(1),
    ):
        report = storage.load_json(path)
        assert report["source_text_hash"] == final_hash
        assert not report.get("stale_after_text_change")


@pytest.mark.asyncio
async def test_optional_story_kernel_failure_records_pending_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    storage, layout, runner, (bundle, outcome) = _build_persist_results_fixture(
        monkeypatch, tmp_path
    )
    events: list[tuple[str, dict[str, Any]]] = []
    runner._settings.narrative_state_required = False
    runner._on_step = lambda step, payload: events.append((step, payload))

    async def _fail_kernel_write(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(
        finalize_persist_impl, "_write_chapter_outcome_to_story_kernel", _fail_kernel_write
    )
    monkeypatch.setattr(
        finalize_persist_impl,
        "invalidate_downstream_generated_artifacts",
        lambda *_args, **_kwargs: [],
    )

    text = "正文" * 300
    await finalize.persist_results(
        runner,
        bundle,
        packet=SimpleNamespace(previous_chapter_ending=""),
        bridge=SimpleNamespace(causal_link=None),
        plan=SimpleNamespace(),
        outcome=outcome,
        current_text=text,
        performed_edits=0,
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="对齐通过",
        ),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(continuity_score=9.0),
        causal_report=CausalValidationReport(causal_score=9.0, issues=[]),
        repair_plan=None,
        trace=SimpleNamespace(total_tokens=0, total_cost=0),
        chapter_number=1,
        eval_report=EvalReport(overall_score=8.0, passed=True),
        emit_evaluate_step=False,
    )

    manifest = ArtifactManifest(storage, layout)
    record = manifest.get(chapter_finalization_artifact(1, "story_kernel"))
    assert record is not None
    assert record.status == "needs_repair"
    assert record.input_hashes["text"] == finalize._source_text_hash(text)
    assert record.metadata["error_type"] == "RuntimeError"
    assert not (layout.states_dir / "kernel_persist_pending_ch1.json").exists()
    assert any(step == "kernel_persist_deferred" for step, _payload in events)


# ── _format_chapter_quality_evidence ─────────────────────────────


class TestFormatChapterQualityEvidence:
    def test_empty_details_returns_empty(self) -> None:
        result = finalize._format_chapter_quality_evidence({})
        assert result == ""

    def test_prompt_leaks_formatted(self) -> None:
        details = {"prompt_leaks": ["leak1", "leak2"]}
        result = finalize._format_chapter_quality_evidence(details)
        assert "prompt_leaks=leak1" in result
        assert "prompt_leaks=leak2" in result

    def test_all_standard_dimensions_included(self) -> None:
        details = {
            "prompt_leaks": ["leak1"],
            "factual_errors": ["fact1"],
            "expression_errors": ["expr1"],
            "continuity_errors": ["cont1"],
        }
        result = finalize._format_chapter_quality_evidence(details)
        assert "prompt_leaks=leak1" in result
        assert "factual_errors=fact1" in result
        assert "expression_errors=expr1" in result
        assert "continuity_errors=cont1" in result

    def test_forbidden_element_findings_included_in_output(self) -> None:
        details = {
            "prompt_leaks": [],
            "factual_errors": [],
            "expression_errors": [],
            "continuity_errors": [],
            "forbidden_element_findings": [
                {
                    "verdict": "violation",
                    "forbidden": "现代手机",
                    "matched": "手机",
                    "reason": "古代背景出现现代物品",
                }
            ],
        }
        result = finalize._format_chapter_quality_evidence(details)
        assert "forbidden[violation]" in result
        assert "现代手机→手机" in result
        assert "古代背景出现现代物品" in result

    def test_forbidden_element_findings_with_only_forbidden_no_matched(self) -> None:
        details = {
            "forbidden_element_findings": [
                {
                    "verdict": "violation",
                    "forbidden": "魔法",
                    "matched": "",
                    "reason": "",
                }
            ]
        }
        result = finalize._format_chapter_quality_evidence(details)
        assert "forbidden[violation]=魔法" in result

    def test_forbidden_element_findings_skips_non_dict_entries(self) -> None:
        details = {"forbidden_element_findings": ["not_a_dict", None, 42]}
        result = finalize._format_chapter_quality_evidence(details)
        assert "forbidden" not in result

    def test_forbidden_element_findings_truncated_to_three(self) -> None:
        findings = [
            {"verdict": "violation", "forbidden": f"词{i}", "matched": "", "reason": ""}
            for i in range(5)
        ]
        details = {"forbidden_element_findings": findings}
        result = finalize._format_chapter_quality_evidence(details)
        # Only first 3 should appear
        assert "词0" in result
        assert "词1" in result
        assert "词2" in result
        assert "词3" not in result
        assert "词4" not in result

    def test_forbidden_element_findings_combined_with_other_dimensions(self) -> None:
        details = {
            "prompt_leaks": ["leak1"],
            "factual_errors": [],
            "expression_errors": [],
            "continuity_errors": [],
            "forbidden_element_findings": [
                {"verdict": "violation", "forbidden": "禁忌词", "matched": "", "reason": ""}
            ],
        }
        result = finalize._format_chapter_quality_evidence(details)
        assert "prompt_leaks=leak1" in result
        assert "forbidden[violation]=禁忌词" in result
        # Both should be joined by ；
        assert "；" in result
