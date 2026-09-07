"""Tests for concurrent repair performance optimization and cancel safety."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairRoundContext,
    _RepairRoundKernel,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_issue(category: str = "", fix_mode: str = "", issue_type: str = "") -> dict[str, Any]:
    """Create a mock issue dict for testing."""
    return {
        "category": category,
        "fix_mode": fix_mode,
        "issue_type": issue_type,
        "description": "test issue",
        "severity": "warning",
    }


class TestConcurrentRepairClassification:
    """Test repair classification logic."""

    def test_text_only_issues_classified_as_concurrent(self) -> None:
        """Text-only issues should be classified as concurrent (not canon-modifying)."""
        from novel_forge.workspace.helpers.execution_helpers import _LOCALLY_SAFE_ISSUE_TYPES

        text_only_issues = [
            _make_issue(issue_type=it) for it in _LOCALLY_SAFE_ISSUE_TYPES
        ]

        for issue in text_only_issues:
            assert issue["issue_type"] in _LOCALLY_SAFE_ISSUE_TYPES

    def test_canon_modifying_categories(self) -> None:
        """Issues with canon-modifying categories should be identified."""
        canon_categories = ["character_state", "timeline"]

        for cat in canon_categories:
            issue = _make_issue(category=cat)
            assert issue["category"] in canon_categories

    def test_state_inherit_issue_types(self) -> None:
        """State-inheriting issue types should be identified."""
        from novel_forge.workspace.helpers.execution_helpers import _STATE_INHERIT_ISSUE_TYPES

        state_types = [
            "carry_forward_missing",
            "knowledge_contradiction",
            "causal_contradiction",
            "question_ignored",
        ]

        for st in state_types:
            assert st in _STATE_INHERIT_ISSUE_TYPES


class TestConcurrentRepairPerformance:
    """Test concurrent repair performance improvement."""

    @pytest.mark.asyncio
    async def test_performance(self) -> None:
        """10 repairs: verify concurrent runs in ≤70% of serial time."""

        async def mock_process_chapter(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
            order = args[0] if args else kwargs.get("order", 0)
            await asyncio.sleep(0.1)
            return order, {"chapter_number": order + 1, "status": "applied", "applied": True}

        serial_groups = [(i, i + 1, [_make_issue()]) for i in range(10)]
        concurrent_groups = [(i, i + 1, [_make_issue()]) for i in range(10)]

        start_serial = time.monotonic()
        for order, chapter_num, chapter_issues in serial_groups:
            await mock_process_chapter(order, chapter_num, chapter_issues)
        elapsed_serial = time.monotonic() - start_serial

        start_concurrent = time.monotonic()
        tasks = [
            asyncio.create_task(mock_process_chapter(order, chapter_num, chapter_issues))
            for order, chapter_num, chapter_issues in concurrent_groups
        ]
        await asyncio.gather(*tasks)
        elapsed_concurrent = time.monotonic() - start_concurrent

        ratio = elapsed_concurrent / elapsed_serial if elapsed_serial > 0 else 1.0
        assert ratio <= 0.7, f"Concurrent ({elapsed_concurrent:.3f}s) should be ≤70% of serial ({elapsed_serial:.3f}s), ratio={ratio:.2f}"

    @pytest.mark.asyncio
    async def test_canon_repairs_serial(self) -> None:
        """Canon-modifying repairs should run sequentially."""
        execution_order: list[int] = []
        execution_lock = asyncio.Lock()

        async def mock_process_canon(order: int, chapter_num: int, issues: list) -> tuple[int, dict]:
            async with execution_lock:
                execution_order.append(chapter_num)
                await asyncio.sleep(0.05)
            return order, {"chapter_number": chapter_num, "status": "applied"}

        serial_groups = [
            (0, 3, [_make_issue(category="character_state")]),
            (1, 1, [_make_issue(category="timeline")]),
            (2, 5, [_make_issue(issue_type="knowledge_contradiction")]),
        ]

        for order, chapter_num, chapter_issues in serial_groups:
            await mock_process_canon(order, chapter_num, chapter_issues)

        assert execution_order == [3, 1, 5], "Canon repairs should execute in provided order"

    @pytest.mark.asyncio
    async def test_text_repairs_concurrent(self) -> None:
        """Text-only repairs should run concurrently."""
        start_times: list[float] = []
        end_times: list[float] = []

        async def mock_process_text(order: int, chapter_num: int, issues: list) -> tuple[int, dict]:
            start_times.append(time.monotonic())
            await asyncio.sleep(0.1)
            end_times.append(time.monotonic())
            return order, {"chapter_number": chapter_num, "status": "applied"}

        concurrent_groups = [
            (i, i + 1, [_make_issue(issue_type="opening_gap")]) for i in range(5)
        ]

        tasks = [
            asyncio.create_task(mock_process_text(order, chapter_num, chapter_issues))
            for order, chapter_num, chapter_issues in concurrent_groups
        ]
        await asyncio.gather(*tasks)

        if start_times and end_times:
            overlap = max(start_times) - min(start_times)
            assert overlap < 0.05, f"Text repairs should start nearly simultaneously, overlap={overlap:.3f}s"


class TestRepairOrdering:
    """Test serial repair ordering by chapter number."""

    def test_serial_repairs_ordered_by_chapter(self) -> None:
        """Serial repairs should be ordered by chapter number (lower first)."""
        serial_groups = [
            (0, 5, [_make_issue(category="character_state")]),
            (1, 2, [_make_issue(category="timeline")]),
            (2, 8, [_make_issue(issue_type="knowledge_contradiction")]),
            (3, 1, [_make_issue(category="character_state")]),
        ]

        serial_groups.sort(key=lambda x: x[1])

        chapter_nums = [g[1] for g in serial_groups]
        assert chapter_nums == sorted(chapter_nums), "Serial repairs should be ordered by chapter number"
        assert chapter_nums == [1, 2, 5, 8]


# ── Cancel safety: mock _RepairRoundKernel subclass ────────────────────────────


@dataclass
class _MockIssue:
    issue_type: str = ""
    severity: str = ""
    summary: str = ""
    category: str = ""
    fix_mode: str = ""
    description: str = ""


@dataclass
class _MockReport:
    score: float = 5.0
    issues: list[_MockIssue] = field(default_factory=list)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {"score": self.score, "issues": [{"issue_type": i.issue_type, "severity": i.severity} for i in self.issues]}


def _critical_issue() -> _MockIssue:
    return _MockIssue(
        issue_type="continuity_gap",
        severity="critical",
        summary="角色在第三段突然消失",
    )


def _medium_issue() -> _MockIssue:
    return _MockIssue(
        issue_type="redundancy",
        severity="medium",
        summary="重复描写",
    )


def _unique_base_text() -> str:
    return "".join(f"这是第{i}段独特的测试文本，用于验证修复循环的行为。" for i in range(20))


class _TestRepairRunner(_RepairRoundKernel[_MockReport]):
    """Concrete repair runner for unit testing cancel behavior."""

    def __init__(
        self,
        config: RepairLoopConfig,
        on_step: Any,
        chapter_number: int = 1,
        *,
        execute_fn: Any = None,
        evaluate_fn: Any = None,
        delay: float = 0.0,
    ) -> None:
        super().__init__(config, on_step, chapter_number)
        self._execute_fn = execute_fn
        self._evaluate_fn = evaluate_fn
        self._delay = delay
        self.executed_rounds: list[int] = []

    async def execute_repair(self, ctx: RepairRoundContext[_MockReport]) -> str:
        self.executed_rounds.append(ctx.round_number)
        if self._execute_fn is not None:
            return await self._execute_fn(ctx)
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return f"repaired_round_{ctx.round_number}"

    async def evaluate(self, text: str) -> _MockReport:
        if self._evaluate_fn is not None:
            return await self._evaluate_fn(text)
        return _MockReport(score=5.0, issues=[_critical_issue()])

    def extract_issues(self, report: _MockReport) -> list[_MockIssue]:
        return list(report.issues)

    def compute_score(self, report: _MockReport) -> float:
        return report.score

    def issue_signature(self, issue: _MockIssue | Any) -> str:
        return f"{getattr(issue, 'issue_type', '')}:{getattr(issue, 'summary', '')}"


class TestRepairCancelSafety:
    """Cancel-during-repair safety: verify _RepairRoundKernel handles task.cancel() correctly.

    The real cancel mechanism (desktop/jobs.py request_cancel) calls
    loop.call_soon_threadsafe(task.cancel), which raises CancelledError at the
    next await point.  This propagates through ``except Exception`` in run()
    because CancelledError is a BaseException subclass (Python 3.9+).

    Raising CancelledError *directly* inside execute_repair/evaluate is NOT
    equivalent — asyncio.wait_for swallows it and returns None.  All tests
    below use ``asyncio.create_task`` + ``task.cancel()`` to match production.
    """

    @pytest.mark.asyncio
    async def test_task_cancel_propagates_from_execute_repair(self) -> None:
        """task.cancel() during execute_repair propagates CancelledError out of run()."""

        async def _slow_execute(ctx: RepairRoundContext[_MockReport]) -> str:
            await asyncio.sleep(10)
            return "never"

        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _TestRepairRunner(config, lambda *_: None, execute_fn=_slow_execute)

        task = asyncio.create_task(runner.run("original", _MockReport(score=3.0, issues=[_critical_issue()])))
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_task_cancel_propagates_from_evaluate(self) -> None:
        """task.cancel() during evaluate (recheck) propagates CancelledError."""
        execute_done = asyncio.Event()

        _TXT = _unique_base_text()

        async def _fast_execute(ctx: RepairRoundContext[_MockReport]) -> str:
            execute_done.set()
            return _TXT + "修补了一小段。"

        async def _slow_evaluate(text: str) -> _MockReport:
            await asyncio.sleep(10)
            return _MockReport(score=5.0, issues=[_critical_issue()])

        config = RepairLoopConfig(
            max_rounds=3,
            score_threshold=8.5,
            must_fix_severity="critical",
            recheck_enabled=True,
            change_budget=1.0,
        )
        runner = _TestRepairRunner(
            config, lambda *_: None, execute_fn=_fast_execute, evaluate_fn=_slow_evaluate
        )

        task = asyncio.create_task(runner.run(_TXT, _MockReport(score=3.0, issues=[_critical_issue()])))
        await execute_done.wait()
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_cancel_preserves_checkpoint_from_previous_round(self) -> None:
        """Cancel during round 1: checkpoint preserves round 0's text (pre-round snapshot)."""
        round_0_done = asyncio.Event()

        _TXT = _unique_base_text()

        async def _tracked_execute(ctx: RepairRoundContext[_MockReport]) -> str:
            if ctx.round_number == 0:
                result = _TXT + "round_0_repair"
                round_0_done.set()
                return result
            await asyncio.sleep(10)
            return "never"

        config = RepairLoopConfig(
            max_rounds=3, score_threshold=8.5, must_fix_severity="critical", change_budget=1.0
        )
        runner = _TestRepairRunner(config, lambda *_: None, execute_fn=_tracked_execute)

        task = asyncio.create_task(runner.run(_TXT, _MockReport(score=3.0, issues=[_critical_issue()])))
        await round_0_done.wait()
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner._checkpoint is not None
        assert runner._checkpoint.round_number == 1

    @pytest.mark.asyncio
    async def test_cleanup_partial_state_returns_pre_round_text(self) -> None:
        """cleanup_partial_state returns pre_round_text when available."""
        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _TestRepairRunner(config, lambda *_: None)

        ctx = RepairRoundContext[_MockReport](
            current_text="interrupted_text",
            pre_round_text="safe_pre_round_text",
            report=_MockReport(score=5.0, issues=[_critical_issue()]),
            issues=[_critical_issue()],
        )
        result = runner.cleanup_partial_state(ctx)
        assert result == "safe_pre_round_text"

    @pytest.mark.asyncio
    async def test_cleanup_partial_state_falls_back_to_checkpoint(self) -> None:
        """cleanup_partial_state uses checkpoint text when pre_round_text is empty."""
        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _TestRepairRunner(config, lambda *_: None)
        runner._checkpoint = MagicMock()
        runner._checkpoint.current_text = "checkpoint_text"

        ctx = RepairRoundContext[_MockReport](
            current_text="interrupted_text",
            pre_round_text="",
            report=_MockReport(score=5.0, issues=[_critical_issue()]),
            issues=[_critical_issue()],
        )
        result = runner.cleanup_partial_state(ctx)
        assert result == "checkpoint_text"

    @pytest.mark.asyncio
    async def test_cancel_job_status_is_failed_when_cancelled(self) -> None:
        """cancel_job sets status to FAILED with current_step='cancelled', not SUCCEEDED."""
        from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobRecord, DesktopJobState

        manager = DesktopJobManager.__new__(DesktopJobManager)
        manager._jobs = {}
        manager._workers = {}
        manager._cancelling = {}
        manager._lock = threading.Lock()
        manager._waiting_jobs = {}

        record = DesktopJobRecord(
            job_id="test-job-1",
            kind="chapter",
            label="测试章节",
            project_id="test_project",
            status=DesktopJobState.RUNNING,
        )
        manager._jobs["test-job-1"] = record

        mock_worker = MagicMock()
        mock_worker._cleanup_notify = None
        manager._workers["test-job-1"] = mock_worker

        mock_signal = MagicMock()
        manager.jobs_changed = mock_signal
        manager.job_completed = MagicMock()
        manager._update_sleep_inhibitor = MagicMock()
        manager._persist_terminal_job = MagicMock()

        manager.cancel_job("test-job-1", reason="用户已取消")

        assert record.status == DesktopJobState.FAILED
        assert record.current_step == "cancelled"
        assert record.error == "用户已取消"
        mock_worker.request_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_job_idempotent_on_already_failed(self) -> None:
        """Calling cancel_job on an already-failed job is a no-op."""
        from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobRecord, DesktopJobState

        manager = DesktopJobManager.__new__(DesktopJobManager)
        manager._jobs = {}
        manager._workers = {}
        manager._cancelling = {}
        manager._lock = threading.Lock()
        manager._waiting_jobs = {}

        record = DesktopJobRecord(
            job_id="test-job-2",
            kind="chapter",
            label="测试章节",
            project_id="test_project",
            status=DesktopJobState.FAILED,
        )
        manager._jobs["test-job-2"] = record

        mock_signal = MagicMock()
        manager.jobs_changed = mock_signal
        manager.job_completed = MagicMock()
        manager._update_sleep_inhibitor = MagicMock()
        manager._persist_terminal_job = MagicMock()

        manager.cancel_job("test-job-2", reason="重复取消")

        assert record.status == DesktopJobState.FAILED
        assert record.current_step == ""

    @pytest.mark.asyncio
    async def test_cancel_during_round1_leaves_round0_checkpoint(self) -> None:
        """Cancel during round 1 leaves checkpoint from round 0 intact."""
        round_0_done = asyncio.Event()

        _TXT = _unique_base_text()

        async def _execute(ctx: RepairRoundContext[_MockReport]) -> str:
            if ctx.round_number == 0:
                round_0_done.set()
                return _TXT + "round0"
            await asyncio.sleep(10)
            return "never"

        config = RepairLoopConfig(
            max_rounds=3, score_threshold=8.5, must_fix_severity="critical", change_budget=1.0
        )
        runner = _TestRepairRunner(config, lambda *_: None, execute_fn=_execute)

        task = asyncio.create_task(runner.run(_TXT, _MockReport(score=3.0, issues=[_critical_issue()])))
        await round_0_done.wait()
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner.executed_rounds == [0, 1]
        assert runner._checkpoint is not None

    @pytest.mark.asyncio
    async def test_cancel_immediately_checkpoint_shows_initial_text(self) -> None:
        """Cancel at round 0 start: checkpoint preserves initial text."""

        async def _slow_execute(ctx: RepairRoundContext[_MockReport]) -> str:
            await asyncio.sleep(10)
            return "never"

        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _TestRepairRunner(config, lambda *_: None, execute_fn=_slow_execute)

        task = asyncio.create_task(runner.run("initial_text", _MockReport(score=3.0, issues=[_critical_issue()])))
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner._checkpoint is not None
        assert runner._checkpoint.current_text == "initial_text"
        assert runner._checkpoint.round_number == 0
        serialized = json.dumps(
            {
                "stage": runner._checkpoint.stage,
                "round_number": runner._checkpoint.round_number,
                "current_text": runner._checkpoint.current_text,
            },
            ensure_ascii=False,
        )
        assert "initial_text" in serialized

    @pytest.mark.asyncio
    async def test_cancel_emits_attempt_guidance_before_cancel(self) -> None:
        """repair_attempt_guidance event is emitted before cancel interrupts the loop."""
        events: list[tuple[str, dict[str, Any]]] = []

        async def _slow_execute(ctx: RepairRoundContext[_MockReport]) -> str:
            await asyncio.sleep(10)
            return "never"

        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _TestRepairRunner(
            config, lambda s, d: events.append((s, d)), execute_fn=_slow_execute
        )

        task = asyncio.create_task(runner.run("text", _MockReport(score=3.0, issues=[_critical_issue()])))
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        guidance_events = [e for e in events if e[0] == "repair_attempt_guidance"]
        assert len(guidance_events) >= 1
        assert guidance_events[0][1]["round"] == 1
