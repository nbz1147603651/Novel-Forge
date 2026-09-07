"""Tests for consistency-violation recovery in chapter session handlers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.pipeline.long.services.contract_execution_repair import (
    compile_contract_audit_repair_ticket,
)
from novel_forge.workspace.contracts import (
    DecisionCheckpoint,
    DecisionOption,
    PrepareChapterResponse,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.sessions.chapter_session_handlers import resolve_plan_checkpoint
from novel_forge.workspace.sessions.chapter_session_state import PlanCheckpointSessionState


def test_resolve_plan_checkpoint_replans_after_consistency_violation(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {"steps": []}

    class _FakeContext:
        def __init__(self, callback):
            self._callback = callback
            self.settings = Settings()

        def on_step(self, step: str, data: Any) -> None:
            captured["steps"].append(step)
            if callable(self._callback):
                self._callback(step, data)

    class _FakeRunner:
        def __init__(self, callback):
            self._callback = callback

        def create_execution_context(self) -> _FakeContext:
            return _FakeContext(self._callback)

    runtime = SimpleNamespace(
        chapter_runner=lambda **kwargs: _FakeRunner(kwargs.get("on_step_progress")),
    )
    bundle = SimpleNamespace()
    session_state = PlanCheckpointSessionState(
        checkpoint_id="plan-001",
        project_id="demo",
        chapter_number=2,
        canon_watermark=1,
        notes="旧备注",
        trace_summary={},
    )
    request = ResolveChapterCheckpointRequest(
        project_id="demo",
        chapter_number=2,
        checkpoint_id="plan-001",
        option_id="write_now",
        notes="保留这个角色冲突",
    )

    async def _load_prepared(*args, **kwargs):
        return object()

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.load_prepared_chapter_artifacts",
        _load_prepared,
    )

    async def _raise_consistency_violation(*args, **kwargs):
        raise ConsistencyViolationError(
            [
                "主角尚未得知线索却提前做出关键判断",
                "上一章伏笔未在本章兑现",
            ],
            replan_target=RecoveryTarget.PLAN,
        )

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.review_chapter_draft",
        _raise_consistency_violation,
    )

    async def _fake_prepare_plan_checkpoint(
        _runtime,
        prepare_request,
        *,
        on_step_progress=None,
        replan_history=None,
        replan_context=None,
    ):
        captured["notes"] = prepare_request.notes
        captured["replan_history"] = replan_history
        captured["replan_context"] = replan_context
        checkpoint = DecisionCheckpoint(
            checkpoint_id="plan-002",
            checkpoint_type="plan_checkpoint",
            summary="自动重生后的章节方案",
            prompt="请确认并继续。",
            options=[
                DecisionOption(
                    option_id="write_now",
                    label="确认方案并写作",
                    is_recommended=True,
                )
            ],
        )
        return PrepareChapterResponse(
            project_id=prepare_request.project_id,
            chapter_number=prepare_request.chapter_number,
            status="needs_decision",
            checkpoint=checkpoint,
        )

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.prepare_plan_checkpoint",
        _fake_prepare_plan_checkpoint,
    )

    result = asyncio.run(
        resolve_plan_checkpoint(
            runtime,
            request,
            bundle=bundle,
            session_state=session_state,
            on_step_progress=lambda step, _data: captured["steps"].append(f"cb:{step}"),
        )
    )

    assert result.status == "needs_decision"
    assert result.applied_option_id == "write_now"
    assert result.checkpoint is not None
    assert result.checkpoint.checkpoint_id == "plan-002"
    assert "保留这个角色冲突" in captured["notes"]
    assert "一致性校验未通过" in captured["notes"]
    assert "主角尚未得知线索却提前做出关键判断" in captured["notes"]
    assert "consistency_replan" in captured["steps"]
    # Checkpoint prompt should explain the replan reason to the user
    assert "重新规划" in result.checkpoint.prompt
    assert "第 1/2 次重试" in result.checkpoint.prompt
    assert captured["replan_context"].attempt_number == 1
    assert len(captured["replan_history"]) == 1
    assert captured["replan_history"][0].attempt_number == 1


def test_resolve_plan_checkpoint_never_replans_a_text_scoped_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeContext:
        settings = Settings()

        def on_step(self, _step: str, _data: Any) -> None:
            return None

    class _FakeRunner:
        def create_execution_context(self) -> _FakeContext:
            return _FakeContext()

    runtime = SimpleNamespace(chapter_runner=lambda **_kwargs: _FakeRunner())
    bundle = SimpleNamespace()
    session_state = PlanCheckpointSessionState(
        checkpoint_id="plan-001",
        project_id="demo",
        chapter_number=2,
        canon_watermark=1,
        notes="",
        trace_summary={},
    )
    request = ResolveChapterCheckpointRequest(
        project_id="demo",
        chapter_number=2,
        checkpoint_id="plan-001",
        option_id="write_now",
    )

    async def _load_prepared(*_args: Any, **_kwargs: Any) -> object:
        return object()

    async def _raise_text_failure(*_args: Any, **_kwargs: Any) -> None:
        raise ConsistencyViolationError(
            ["世界规则局部补丁未能通过定向复核"],
            violation_kind="world_rule_conflict",
            failed_stage="world_rule_patch",
            replan_target=RecoveryTarget.MANUAL,
        )

    async def _unexpected_prepare(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("text-scoped failure must not create a new plan")

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.load_prepared_chapter_artifacts",
        _load_prepared,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.review_chapter_draft",
        _raise_text_failure,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.prepare_plan_checkpoint",
        _unexpected_prepare,
    )

    with pytest.raises(ConsistencyViolationError) as raised:
        asyncio.run(
            resolve_plan_checkpoint(
                runtime,
                request,
                bundle=bundle,
                session_state=session_state,
            )
        )

    assert raised.value.replan_target is RecoveryTarget.MANUAL


def test_contract_audit_blocking_report_compiles_repair_ticket() -> None:
    text = "沈知微低声说：“如果后续要采集数据，他应该是最合适的人选。”"
    ticket = compile_contract_audit_repair_ticket(
        report_payload={
            "should_block_archive": True,
            "repair_or_replan_decision": "repair",
            "severity": "high",
            "contract_completion_score": 9.0,
            "future_leak_hits": ["沈知微后续数据合作意向"],
            "evidence_quotes": ['"如果后续要采集数据，他应该是最合适的人选。"'],
        },
        chapter_number=2,
        current_text=text,
    )

    assert ticket is not None
    assert ticket.source_module == "contract_execution_audit"
    assert ticket.issue_type == "future_leak"
    assert ticket.blocking is True
    assert ticket.metadata["evidence_exact"] is True
    assert ticket.metadata["constraint"] == "沈知微后续数据合作意向"


def test_contract_audit_cognitive_hit_takes_primary_ticket_type() -> None:
    text = "沈清漪终于确认，东宫上下无人见过小书童。"
    ticket = compile_contract_audit_repair_ticket(
        report_payload={
            "should_block_archive": True,
            "repair_or_replan_decision": "repair",
            "severity": "high",
            "contract_completion_score": 4.0,
            "cognitive_constraint_hits": ["小书童无人见过"],
            "future_leak_hits": ["后续身份揭露"],
            "evidence_quotes": ["东宫上下无人见过小书童"],
        },
        chapter_number=2,
        current_text=text,
    )

    assert ticket is not None
    assert ticket.issue_type == "cognitive_constraint"
    assert ticket.metadata["constraint"] == "小书童无人见过"
    assert ticket.metadata["cognitive_constraint_hits"] == ["小书童无人见过"]
    assert "观察、误判、怀疑、遮蔽" in ticket.repair_goal
    assert any("allowed_progressions" in item for item in ticket.forbidden_changes)


def test_contract_audit_missing_progression_takes_primary_ticket_type() -> None:
    ticket = compile_contract_audit_repair_ticket(
        report_payload={
            "should_block_archive": True,
            "repair_or_replan_decision": "repair_or_replan",
            "severity": "high",
            "contract_completion_score": 5.0,
            "missing_required_progressions": ["主角必须确认线索来源"],
            "missing_knowledge_ops": ["林远获知裂缝不是自然现象"],
            "evidence_quotes": [],
        },
        chapter_number=4,
        current_text="林远看着裂缝，没有继续追问。",
    )

    assert ticket is not None
    assert ticket.issue_type == "missing_required_progression"
    assert ticket.metadata["constraint"] == "主角必须确认线索来源"
    assert "补齐本章契约要求" in ticket.repair_goal


def test_contract_audit_missing_knowledge_compiles_repair_ticket() -> None:
    ticket = compile_contract_audit_repair_ticket(
        report_payload={
            "should_block_archive": True,
            "repair_or_replan_decision": "repair",
            "severity": "high",
            "contract_completion_score": 6.0,
            "missing_knowledge_ops": ["林远获知裂缝不是自然现象"],
            "evidence_quotes": [],
        },
        chapter_number=4,
        current_text="林远看着裂缝，没有继续追问。",
    )

    assert ticket is not None
    assert ticket.issue_type == "missing_knowledge_op"
    assert ticket.metadata["constraint"] == "林远获知裂缝不是自然现象"


def test_resolve_plan_checkpoint_replan_limit_raises(monkeypatch) -> None:
    """After exceeding the max replan count, ConsistencyViolationError must propagate."""
    captured: dict[str, Any] = {"steps": []}

    class _FakeContext:
        def __init__(self, callback):
            self._callback = callback
            self.settings = Settings()

        def on_step(self, step: str, data: Any) -> None:
            captured["steps"].append(step)
            if callable(self._callback):
                self._callback(step, data)

    class _FakeRunner:
        def __init__(self, callback):
            self._callback = callback

        def create_execution_context(self) -> _FakeContext:
            return _FakeContext(self._callback)

    runtime = SimpleNamespace(
        chapter_runner=lambda **kwargs: _FakeRunner(kwargs.get("on_step_progress")),
    )
    bundle = SimpleNamespace()

    # Simulate notes with 2 prior replan markers (== limit).
    replan_notes = (
        "【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n- 问题A\n\n"
        "【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n- 问题B"
    )
    session_state = PlanCheckpointSessionState(
        checkpoint_id="plan-003",
        project_id="demo",
        chapter_number=3,
        canon_watermark=2,
        notes=replan_notes,
        trace_summary={},
    )
    request = ResolveChapterCheckpointRequest(
        project_id="demo",
        chapter_number=3,
        checkpoint_id="plan-003",
        option_id="write_now",
        notes=replan_notes,
    )

    async def _load_prepared(*args, **kwargs):
        return object()

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.load_prepared_chapter_artifacts",
        _load_prepared,
    )

    async def _raise_consistency_violation(*args, **kwargs):
        raise ConsistencyViolationError(["仍然不通过"])

    monkeypatch.setattr(
        "novel_forge.workspace.sessions.chapter_session_handlers.review_chapter_draft",
        _raise_consistency_violation,
    )

    import pytest

    with pytest.raises(ConsistencyViolationError):
        asyncio.run(
            resolve_plan_checkpoint(
                runtime,
                request,
                bundle=bundle,
                session_state=session_state,
                on_step_progress=lambda step, _data: None,
            )
        )

    # consistency_replan should NOT appear — the limit prevented it.
    assert "consistency_replan" not in captured["steps"]
