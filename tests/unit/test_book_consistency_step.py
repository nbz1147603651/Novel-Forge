"""Regression tests for whole-book consistency audit prompt budgeting."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    GLOBAL_AUDIT_DIMENSION_SPECS,
    BookConsistencyInput,
    BookConsistencyStep,
    DimensionContextBuilder,
)
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.book_ops.execution_book_repair import _summarize_book_issue_focus
from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify
from tests.helpers.book_audit_payloads import canonical_book_audit_response, canonical_book_issue


class _LengthCheckingRouter:
    def __init__(self, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.user_prompts: list[str] = []

    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request: ModelRequest) -> ModelResponse:
        user_prompt = request.messages[-1]["content"]
        self.user_prompts.append(user_prompt)
        if len(user_prompt) > self.max_prompt_chars:
            raise RuntimeError("context window exceeds limit")
        return ModelResponse(
            content=json.dumps(
                {
                    "issues": [],
                    "repair_plan": [],
                    "summary": "未发现问题",
                    "consistency_score": 9.5,
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


def test_book_repair_issue_focus_summary_is_compact() -> None:
    summary = _summarize_book_issue_focus(
        [
            {"category": "timeline", "severity": "critical"},
            {"category": "timeline", "severity": "high"},
            {"category": "character_state", "severity": "warning"},
        ]
    )

    assert summary == "时间线2项、角色状态1项 / 严重2、警告1"


def test_book_consistency_uses_dimension_limit_setting_when_request_unspecified(
    runtime_settings: Any,
) -> None:
    runtime_settings.long_book_audit_parallel_dimension_limit = 6
    step = BookConsistencyStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    ctx = step._build_prompt_context(
        BookConsistencyInput(
            chapter_summaries=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            parallel_dimensions=True,
        ),
        characters={},
        relationships={},
        bible_names=[],
        character_profiles_compact=[],
        outline_summary="",
        chapter_texts_for_template=[],
    )

    assert ctx["__parallel_dimension_limit"] == 6


def test_dimension_context_contains_single_dimension_bundle() -> None:
    builder = DimensionContextBuilder()
    spec = GLOBAL_AUDIT_DIMENSION_SPECS[0]

    ctx = builder.build(
        spec=spec,
        base_ctx={
            "chapter_summaries": [
                {"chapter_number": 1, "summary": "第一章"},
                {"chapter_number": 2, "summary": "第二章"},
            ],
            "analysis_mode": "summary",
            "location_strictness": "balanced",
            "max_issues_per_chunk": 12,
            "shared_evidence_anchor": {"source": "test"},
            "audit_slices": [],
        },
        audit_slice={
            "slice_id": "slice_1",
            "slice_kind": "chapter_pair",
            "chapters": [1],
            "boundary_chapters": [2],
            "focus_dimensions": [spec.name],
        },
        chapter_texts=[],
        dimension_ledger_context=[{"dimension": "previous", "summary": "done"}],
    )

    bundle = ctx["dimension_audit_bundle"]
    assert bundle["schema"] == "DimensionAuditBundle"
    assert bundle["dimension"] == spec.name
    assert bundle["focus_chapters"] == [1, 2]
    assert spec.name not in bundle["out_of_scope"]
    assert f"active_audit_dimension={spec.name}" not in ctx["prompt_hint"]
    assert "dimension_audit_bundle" in ctx["prompt_hint"]


class _FailingRouter:
    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request: ModelRequest) -> ModelResponse:
        raise ModelGatewayError("context window exceeds limit", is_transient=False)


class _StrictAuditRouter:
    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                canonical_book_audit_response(
                    issues=[
                        canonical_book_issue(
                            "ch2_identity_01",
                            category="character_state",
                            severity="critical",
                            chapters_involved=[2],
                            primary_chapter=2,
                            description="角色身份前后矛盾。",
                            paragraph_index=1,
                            paragraph_span=[1, 2],
                            confidence=0.9,
                        )
                    ],
                    summary="共发现 1 个一致性问题，最高严重级别为 critical。",
                    consistency_score=7.0,
                ),
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


class _FailOnSecondAuditRouter:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated audit chunk failure")
        return ModelResponse(
            content=json.dumps(
                {
                    "issues": [],
                    "repair_plan": [],
                    "summary": f"第 {self.calls} 批无问题",
                    "consistency_score": 9.5,
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


class _RecordingDimensionStep(BookConsistencyStep):
    def __init__(self, *args: Any, delay: float = 0.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def _call_with_retry(
        self,
        task_type: Any,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay:
                import asyncio

                await asyncio.sleep(self.delay)
            dimension = str(context.get("active_audit_dimension") or "")
            self.calls.append(
                {
                    "dimension": dimension,
                    "task_type": task_type,
                    "keys": set(context.keys()),
                    "context": dict(context),
                    "ledger": context.get("dimension_ledger_context") or [],
                }
            )
            return {
                "issues": [
                    {
                        "issue_id": f"{dimension}_issue",
                        "dimension": dimension,
                        "category": dimension,
                        "severity": "warning",
                        "primary_chapter": 1,
                        "chapters_involved": [1],
                        "description": f"{dimension} 候选问题",
                        "evidence": "",
                        "confidence": 0.8,
                    }
                ],
                "repair_plan": [],
                "summary": f"{dimension} 完成",
                "consistency_score": 8.0,
            }
        finally:
            self.active_calls -= 1


class _FailingPromiseDimensionStep(_RecordingDimensionStep):
    async def _call_with_retry(
        self,
        task_type: Any,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        if str(context.get("active_audit_dimension") or "") == "promise_payoff":
            raise RuntimeError("promise dimension failed")
        return await super()._call_with_retry(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _dimension_audit_slices() -> list[dict[str, Any]]:
    return [
        {
            "slice_id": f"slice_{dimension}",
            "slice_kind": slice_kind,
            "chapters": [1, 2],
            "boundary_chapters": [],
            "focus_dimensions": [dimension],
            "dimension_role": "test",
            "source_refs": [],
            "status": "pending",
        }
        for dimension, slice_kind in [
            ("timeline_arc", "volume"),
            ("character_arc", "volume"),
            ("world_rule_integrity", "volume"),
            ("motif_distribution", "motif"),
            ("promise_payoff", "promise_thread"),
            ("plot_thread_liveness", "plot_thread"),
            ("tension_curve", "turning_point"),
        ]
    ]


async def test_dimension_parallel_uses_dag_context_and_global_cap(runtime_settings: Any) -> None:
    runtime_settings.book_audit_max_parallel = 2
    step = _RecordingDimensionStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
        delay=0.01,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "起章", "key_events": []},
                {"chapter_number": 2, "summary": "承章", "key_events": []},
            ],
            canon_state_snapshot={"characters": {"甲": {"alive": True}}, "relationships": []},
            character_bible={},
            outline={},
            world_rules=["规则一"],
            audit_slices=_dimension_audit_slices(),
            parallel_dimensions=True,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    dimensions = [call["dimension"] for call in step.calls]
    assert set(dimensions) >= {
        "timeline_arc",
        "character_arc",
        "world_rule_integrity",
        "motif_distribution",
        "promise_payoff",
        "plot_thread_liveness",
        "tension_curve",
    }
    assert step.max_active_calls <= 2
    assert result.dimension_results

    by_dimension = {call["dimension"]: call for call in step.calls}
    promise_ledger = by_dimension["promise_payoff"]["ledger"]
    assert {entry["dimension"] for entry in promise_ledger} == {"timeline_arc", "character_arc"}
    plot_ledger = by_dimension["plot_thread_liveness"]["ledger"]
    assert {entry["dimension"] for entry in plot_ledger} >= {"timeline_arc", "promise_payoff"}
    tension_ledger = by_dimension["tension_curve"]["ledger"]
    assert {entry["dimension"] for entry in tension_ledger} >= {
        "timeline_arc",
        "plot_thread_liveness",
        "promise_payoff",
    }


async def test_dimension_context_builder_prunes_unneeded_fields(runtime_settings: Any) -> None:
    step = _RecordingDimensionStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    await step.run(
        BookConsistencyInput(
            chapter_summaries=[{"chapter_number": 1, "summary": "起章", "key_events": []}],
            canon_state_snapshot={"characters": {"甲": {"alive": True}}, "relationships": []},
            character_bible={},
            outline={},
            world_rules=["规则一"],
            world_setting="规则世界",
            character_profiles_compact=[{"name": "甲"}],
            audit_slices=_dimension_audit_slices(),
            parallel_dimensions=True,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    by_dimension = {call["dimension"]: call for call in step.calls}
    world_context = by_dimension["world_rule_integrity"]["context"]
    character_context = by_dimension["character_arc"]["context"]
    assert world_context["world_rules"] == ["规则一"]
    assert world_context["canon_characters"] == {}
    assert character_context["canon_characters"] == {"甲": {"alive": True}}
    assert character_context["world_rules"] == []


async def test_dimension_parallel_renders_with_prompt_builder(runtime_settings: Any) -> None:
    runtime_settings.book_audit_max_parallel = 2
    router = _LengthCheckingRouter(max_prompt_chars=80_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "起章", "key_events": ["立下承诺"]},
                {"chapter_number": 2, "summary": "承章", "key_events": ["推进承诺"]},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            audit_slices=_dimension_audit_slices(),
            parallel_dimensions=True,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    assert result.dimension_results
    assert len(router.user_prompts) >= 7
    assert all("当前维度执行契约" in prompt for prompt in router.user_prompts)


async def test_dimension_parallel_resumes_completed_dimensions(
    runtime_settings: Any,
    tmp_path: Any,
) -> None:
    checkpoint_path = tmp_path / "book_consistency_audit_checkpoint.json"
    first_step = _FailingPromiseDimensionStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )
    input_data = BookConsistencyInput(
        chapter_summaries=[
            {"chapter_number": 1, "summary": "起章", "key_events": []},
            {"chapter_number": 2, "summary": "承章", "key_events": []},
        ],
        canon_state_snapshot={},
        character_bible={},
        outline={},
        audit_slices=_dimension_audit_slices(),
        parallel_dimensions=True,
        audit_checkpoint_path=checkpoint_path,
        max_tokens=2048,
        temperature=0.2,
    )
    try:
        await first_step.run(input_data)
    except RuntimeError:
        pass
    else:  # pragma: no cover - guards the test itself
        raise AssertionError("promise dimension should fail")

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    completed = {item["dimension"] for item in checkpoint.get("completed_dimensions", []) if item}
    assert {
        "timeline_arc",
        "character_arc",
        "world_rule_integrity",
        "motif_distribution",
    } <= completed

    resume_step = _RecordingDimensionStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )
    result = await resume_step.run(dataclasses.replace(input_data, resume_audit_checkpoint=True))

    resumed_dimensions = {call["dimension"] for call in resume_step.calls}
    assert "timeline_arc" not in resumed_dimensions
    assert "character_arc" not in resumed_dimensions
    assert "promise_payoff" in resumed_dimensions
    assert result.dimension_results


async def test_dimension_parallel_reuses_signed_cross_run_seed(runtime_settings: Any) -> None:
    seeded_timeline = {
        "dimension": "timeline_arc",
        "slice_id": "slice_timeline_arc",
        "dimension_summary": "已签名复用",
        "claims": [],
        "findings": [],
        "handoff_notes": "cached",
        "coverage": {"chapters_considered": [1, 2]},
    }
    step = _RecordingDimensionStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "起章", "key_events": []},
                {"chapter_number": 2, "summary": "承章", "key_events": []},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            audit_slices=_dimension_audit_slices(),
            parallel_dimensions=True,
            seeded_dimension_results={"timeline_arc": [seeded_timeline]},
            max_tokens=2048,
            temperature=0.2,
        )
    )

    called_dimensions = {call["dimension"] for call in step.calls}
    assert "timeline_arc" not in called_dimensions
    assert any(
        item["dimension"] == "timeline_arc" and item["dimension_summary"] == "已签名复用"
        for item in result.dimension_results
    )


def test_book_consistency_normalization_limits_high_value_issues() -> None:
    payload = {
        "issues": [
            {"issue_id": "info", "severity": "info", "paragraph_index": 1},
            {"issue_id": "critical", "severity": "critical", "paragraph_index": 0},
            {"issue_id": "warning", "severity": "warning", "paragraph_index": 3},
        ],
        "repair_plan": [
            {"chapter_number": 1, "issue_ids": ["info"]},
            {"chapter_number": 2, "issue_ids": ["critical"]},
            {"chapter_number": 3, "issue_ids": ["warning"]},
        ],
    }

    normalized = BookConsistencyStep._normalize_audit_payload(payload, max_issues=2)

    assert [item["issue_id"] for item in normalized["issues"]] == ["critical", "warning"]
    assert [item["issue_ids"][0] for item in normalized["repair_plan"]] == [
        "critical",
        "warning",
    ]


def test_book_consistency_normalization_backfills_legacy_summary_and_score() -> None:
    payload = {
        "issues": [
            {
                "issue_id": "critical",
                "severity": "critical",
                "paragraph_index": 1,
            }
        ]
    }

    normalized = BookConsistencyStep._normalize_audit_payload(payload, max_issues=10)

    assert normalized["summary"].startswith("共发现 1 个一致性问题")
    assert normalized["consistency_score"] == 7.0
    assert normalized["repair_plan"] == []


async def test_book_consistency_accepts_strict_summary_and_score_payload(
    runtime_settings: Any,
) -> None:
    step = BookConsistencyStep(
        _StrictAuditRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "起章", "key_events": []},
                {"chapter_number": 2, "summary": "承章", "key_events": []},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            max_tokens=2048,
            temperature=0.2,
        )
    )

    assert result.summary.startswith("共发现 1 个一致性问题")
    assert result.consistency_score == 7.0
    assert result.issues[0].paragraph_index == 1
    assert result.issues[0].paragraph_span == [1, 2]


async def test_book_consistency_full_text_is_chunked_by_prompt_budget(
    runtime_settings: Any,
) -> None:
    runtime_settings.long_book_audit_prompt_char_budget = 24_000
    router = _LengthCheckingRouter(max_prompt_chars=28_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    chapter_texts = []
    for chapter_number in range(1, 10):
        text = "\n\n".join(
            f"第 {chapter_number} 章第 {idx} 段，钟楼、怀表、旧誓约、影子名单和雨夜路线持续变化，需要跨章追踪。"
            for idx in range(1, 320)
        )
        chapter_texts.append(
            {
                "chapter_number": chapter_number,
                "numbered_text": text,
                "paragraph_count": 319,
                "paragraphs": text.split("\n\n"),
                "source_chars": len(text),
                "truncated": False,
            }
        )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {
                    "chapter_number": item["chapter_number"],
                    "summary": "角色状态推进，时间线保持连续。",
                    "key_events": ["怀表规则被再次提及"],
                }
                for item in chapter_texts
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_tokens=2048,
            temperature=0.2,
        )
    )

    assert result.consistency_score == 9.5
    assert result.summary == "未发现问题"
    assert len(router.user_prompts) > 1
    assert all(len(prompt) <= 28_000 for prompt in router.user_prompts)
    assert any("上下文分块说明" in prompt for prompt in router.user_prompts)


async def test_book_consistency_full_text_honors_chapter_batch_limit(
    runtime_settings: Any,
) -> None:
    runtime_settings.long_book_audit_prompt_char_budget = 200_000
    router = _LengthCheckingRouter(max_prompt_chars=220_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    chapter_texts = [
        {
            "chapter_number": chapter_number,
            "numbered_text": f"[P1] 第 {chapter_number} 章短正文。",
            "paragraph_count": 1,
            "paragraphs": [f"第 {chapter_number} 章短正文。"],
            "source_chars": 20,
            "truncated": False,
        }
        for chapter_number in range(1, 8)
    ]

    progress: list[tuple[int, int]] = []
    await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": item["chapter_number"], "summary": "", "key_events": []}
                for item in chapter_texts
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_chapters_per_batch=3,
            max_tokens=2048,
            temperature=0.2,
            on_chunk_progress=lambda current, total: progress.append((current, total)),
        )
    )

    assert len(router.user_prompts) == 3
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)


async def test_book_consistency_full_text_resumes_from_chunk_checkpoint(
    runtime_settings: Any,
    tmp_path: Any,
) -> None:
    runtime_settings.long_book_audit_prompt_char_budget = 200_000
    checkpoint_path = tmp_path / "book_consistency_audit_checkpoint.json"
    chapter_texts = [
        {
            "chapter_number": chapter_number,
            "numbered_text": f"[P1] 第 {chapter_number} 章短正文。",
            "paragraph_count": 1,
            "paragraphs": [f"第 {chapter_number} 章短正文。"],
            "source_chars": 20,
            "truncated": False,
        }
        for chapter_number in range(1, 3)
    ]

    first_router = _FailOnSecondAuditRouter()
    first_step = BookConsistencyStep(
        first_router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )
    try:
        await first_step.run(
            BookConsistencyInput(
                chapter_summaries=[
                    {"chapter_number": item["chapter_number"], "summary": "", "key_events": []}
                    for item in chapter_texts
                ],
                canon_state_snapshot={},
                character_bible={},
                outline={},
                chapter_texts=chapter_texts,
                analysis_mode="full_text",
                max_chapters_per_batch=1,
                max_tokens=2048,
                temperature=0.2,
                audit_checkpoint_path=checkpoint_path,
            )
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - guards the test itself
        raise AssertionError("first audit should fail on the second chunk")

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "failed"
    assert checkpoint["failed_chunk"] == 2
    assert len(checkpoint["completed_chunks"]) == 1

    resume_router = _LengthCheckingRouter(max_prompt_chars=220_000)
    resume_step = BookConsistencyStep(
        resume_router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )
    result = await resume_step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": item["chapter_number"], "summary": "", "key_events": []}
                for item in chapter_texts
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_chapters_per_batch=1,
            max_tokens=2048,
            temperature=0.2,
            audit_checkpoint_path=checkpoint_path,
            resume_audit_checkpoint=True,
        )
    )

    assert result.consistency_score == 9.5
    assert len(resume_router.user_prompts) == 1
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["status"] == "completed"


async def test_book_consistency_verify_failure_keeps_original_issues(
    runtime_settings: Any,
) -> None:
    issue = {
        "issue_id": "ch2_timeline_01",
        "category": "timeline",
        "severity": "warning",
        "primary_chapter": 2,
        "chapters_involved": [1, 2],
        "description": "时间线存在冲突。",
        "evidence": "夜半抵达",
        "paragraph_index": 1,
    }
    runtime = SimpleNamespace(
        router=_FailingRouter(),
        builder=PromptBuilder(),
        settings=runtime_settings,
    )
    progress_events: list[tuple[str, Any]] = []

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=[issue],
        chapter_texts=[
            {
                "chapter_number": 2,
                "numbered_text": "[P1] 夜半抵达。",
                "paragraph_count": 1,
                "paragraphs": ["夜半抵达。"],
            }
        ],
        chapter_summaries=[{"chapter_number": 2, "summary": "测试章"}],
        canon_state_snapshot={},
        on_step_progress=lambda step, data: progress_events.append((step, data)),
    )

    assert stats["verification_failed"] == 1
    assert stats["remaining"] == 1
    assert updated[0]["issue_id"] == "ch2_timeline_01"
    assert updated[0]["_verification_failed"] is True
    assert any(
        step == "book_consistency_verify_progress" and data.get("processed") == 1
        for step, data in progress_events
    )
