"""Integration tests: cancel-during-repair safety and checkpoint recovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairRoundContext,
    _RepairRoundKernel,
)
from novel_forge.workspace.sessions.chapter_session_state import (
    ReviewProgressState,
    clear_review_progress,
    load_review_progress,
    save_review_progress,
)


@dataclass
class _IntegIssue:
    issue_type: str = ""
    severity: str = ""
    summary: str = ""


@dataclass
class _IntegReport:
    score: float = 5.0
    issues: list[_IntegIssue] = field(default_factory=list)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {"score": self.score, "issues": [{"issue_type": i.issue_type, "severity": i.severity} for i in self.issues]}


def _crit() -> _IntegIssue:
    return _IntegIssue(issue_type="continuity_gap", severity="critical", summary="角色消失")


def _unique_text() -> str:
    return "".join(f"这是第{i}段独特的测试文本，用于验证修复循环的行为。" for i in range(20))


class _IntegRepairRunner(_RepairRoundKernel[_IntegReport]):
    def __init__(
        self,
        config: RepairLoopConfig,
        on_step: Any,
        chapter_number: int = 1,
        *,
        slow_round: int = -1,
        target_score: float = 5.0,
    ) -> None:
        super().__init__(config, on_step, chapter_number)
        self._slow_round = slow_round
        self._target_score = target_score
        self.executed_rounds: list[int] = []
        self.round_done_events: dict[int, asyncio.Event] = {}
        self._base_text = _unique_text()

    async def execute_repair(self, ctx: RepairRoundContext[_IntegReport]) -> str:
        self.executed_rounds.append(ctx.round_number)
        if ctx.round_number not in self.round_done_events:
            self.round_done_events[ctx.round_number] = asyncio.Event()
        self.round_done_events[ctx.round_number].set()
        if ctx.round_number == self._slow_round:
            await asyncio.sleep(10)
        return self._base_text + f"_round_{ctx.round_number}"

    async def evaluate(self, text: str) -> _IntegReport:
        return _IntegReport(score=self._target_score, issues=[_crit()])

    def extract_issues(self, report: _IntegReport) -> list[_IntegIssue]:
        return list(report.issues)

    def compute_score(self, report: _IntegReport) -> float:
        return report.score

    def issue_signature(self, issue: _IntegIssue | Any) -> str:
        return f"{getattr(issue, 'issue_type', '')}:{getattr(issue, 'summary', '')}"


@pytest.fixture
def project_layout(tmp_path: Path) -> ProjectLayout:
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "states").mkdir()
    (root / "drafts").mkdir()
    (root / "drafts" / "chapter_001").mkdir()
    return ProjectLayout(root)


@pytest.fixture
def storage(project_layout: ProjectLayout) -> FileSystemStorage:
    return FileSystemStorage(project_layout.root)


class TestRepairCancelDiskSafety:
    def test_review_progress_saved_and_loadable(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        progress = ReviewProgressState(
            completed_stage="draft_done",
            current_text="第一章内容",
            performed_edits=1,
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.completed_stage == "draft_done"
        assert loaded.current_text == "第一章内容"
        assert loaded.performed_edits == 1

    def test_review_progress_cleared_after_completion(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        progress = ReviewProgressState(
            completed_stage="canon_done",
            current_text="final",
            performed_edits=2,
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress
        )

        assert load_review_progress(storage, project_layout, 1) is not None
        clear_review_progress(storage, project_layout, 1)
        assert load_review_progress(storage, project_layout, 1) is None

    @pytest.mark.asyncio
    async def test_mid_repair_cancel_checkpoint_survives_on_disk(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        """Cancel during round 1: checkpoint from round 0 persists to disk."""
        config = RepairLoopConfig(
            max_rounds=3, score_threshold=8.5, must_fix_severity="critical", change_budget=1.0
        )
        events: list[tuple[str, dict[str, Any]]] = []
        runner = _IntegRepairRunner(
            config,
            lambda s, d: events.append((s, d)),
            slow_round=1,
        )

        task = asyncio.create_task(
            runner.run(runner._base_text, _IntegReport(score=3.0, issues=[_crit()]))
        )
        if 0 not in runner.round_done_events:
            runner.round_done_events[0] = asyncio.Event()
        await runner.round_done_events[0].wait()
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner._checkpoint is not None
        assert runner._checkpoint.current_text.endswith("_round_0")

        progress = ReviewProgressState(
            completed_stage="draft_done",
            current_text=runner._checkpoint.current_text,
            performed_edits=1,
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.current_text.endswith("_round_0")

    def test_checkpoint_recovery_after_cancel(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        progress = ReviewProgressState(
            completed_stage="quality_done",
            current_text="saved_after_quality_checks",
            performed_edits=1,
            alignment_report={"score": 8.0},
            continuity_report={"score": 7.5},
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.completed_stage == "quality_done"
        assert loaded.current_text == "saved_after_quality_checks"
        assert loaded.alignment_report == {"score": 8.0}

        STAGE_ORDER = ("draft_done", "quality_done", "repair_done", "canon_done")
        resume_stage = loaded.completed_stage
        assert resume_stage in STAGE_ORDER
        skip_draft = resume_stage in STAGE_ORDER
        skip_quality = resume_stage in STAGE_ORDER[1:]
        skip_repair = resume_stage in STAGE_ORDER[2:]
        assert skip_draft is True
        assert skip_quality is True
        assert skip_repair is False

    def test_resume_from_draft_done_skips_draft_stage(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        progress = ReviewProgressState(
            completed_stage="draft_done",
            current_text="draft_text_from_checkpoint",
            performed_edits=1,
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.completed_stage == "draft_done"

        STAGE_ORDER = ("draft_done", "quality_done", "repair_done", "canon_done")
        _skip_draft = loaded.completed_stage in STAGE_ORDER
        _skip_quality = loaded.completed_stage in STAGE_ORDER[1:]

        assert _skip_draft is True
        assert _skip_quality is False
        assert loaded.current_text == "draft_text_from_checkpoint"

    def test_cancel_then_resubmit_same_chapter_no_corruption(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        progress_v1 = ReviewProgressState(
            completed_stage="draft_done",
            current_text="version_1_text",
            performed_edits=1,
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress_v1
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.current_text == "version_1_text"

        progress_v2 = ReviewProgressState(
            completed_stage="quality_done",
            current_text="version_2_after_quality",
            performed_edits=2,
            alignment_report={"score": 9.0},
        )
        save_review_progress(
            storage=storage, layout=project_layout, chapter_number=1, progress=progress_v2
        )

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is not None
        assert loaded.completed_stage == "quality_done"
        assert loaded.current_text == "version_2_after_quality"

        path = project_layout.chapter_review_progress_path(1)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["completed_stage"] == "quality_done"
        assert data["current_text"] == "version_2_after_quality"

    def test_corrupt_review_progress_quarantined(
        self, storage: FileSystemStorage, project_layout: ProjectLayout
    ) -> None:
        path = project_layout.chapter_review_progress_path(1)
        path.write_text("NOT VALID JSON{{{", encoding="utf-8")

        loaded = load_review_progress(storage, project_layout, 1)
        assert loaded is None
        assert not path.exists()


class TestRepairLoopCancelPropagation:
    @pytest.mark.asyncio
    async def test_task_cancel_propagates_from_repair(self) -> None:
        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        runner = _IntegRepairRunner(config, lambda *_: None, slow_round=0)

        task = asyncio.create_task(
            runner.run(runner._base_text, _IntegReport(score=3.0, issues=[_crit()]))
        )
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runner._checkpoint is not None
        assert runner._checkpoint.current_text == runner._base_text

    @pytest.mark.asyncio
    async def test_cancel_after_round0_checkpoint_at_round1(self) -> None:
        config = RepairLoopConfig(
            max_rounds=3, score_threshold=8.5, must_fix_severity="critical", change_budget=1.0
        )
        runner = _IntegRepairRunner(config, lambda *_: None, slow_round=1)

        task = asyncio.create_task(
            runner.run(runner._base_text, _IntegReport(score=3.0, issues=[_crit()]))
        )
        if 0 not in runner.round_done_events:
            runner.round_done_events[0] = asyncio.Event()
        await runner.round_done_events[0].wait()
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert 0 in runner.executed_rounds
        assert runner._checkpoint is not None
        assert runner._checkpoint.round_number == 1
        assert runner._checkpoint.current_text.endswith("_round_0")

    @pytest.mark.asyncio
    async def test_cancel_emits_attempt_guidance_before_cancel(self) -> None:
        config = RepairLoopConfig(max_rounds=3, score_threshold=8.5, must_fix_severity="critical")
        events: list[tuple[str, dict[str, Any]]] = []
        runner = _IntegRepairRunner(
            config, lambda s, d: events.append((s, d)), slow_round=0
        )

        task = asyncio.create_task(
            runner.run(runner._base_text, _IntegReport(score=3.0, issues=[_crit()]))
        )
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        guidance_events = [e for e in events if e[0] == "repair_attempt_guidance"]
        assert len(guidance_events) >= 1
        assert guidance_events[0][1]["round"] == 1
