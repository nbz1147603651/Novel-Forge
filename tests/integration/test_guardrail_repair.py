"""Integration tests for guardrail repair flow (pre-evaluation → repair → quality gate → regression check)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import AlignmentReport, ChapterOutcome
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityIssue,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.long.stages.continuity_repair import (
    guard_repair_quality_gate,
)
from novel_forge.pipeline.repair_orchestration.mission_factory import continuity_repair_mission
from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import RepairContinuityRequest
from novel_forge.workspace.helpers.execution_state import _source_text_hash
from novel_forge.workspace.repair_ops.execution_repair_v2 import execute_repair
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_handlers import (
    _guard_ticket_alignment_followup_required,
    _raise_if_guard_ticket_alignment_regression,
    _restore_pending_text_after_guard_regression,
    _sanitize_guard_repair_plan,
    _ticket_to_continuity_issue,
)
from novel_forge.workspace.sessions.chapter_session_state import PendingChapterReviewState


class TestGuardrailRepairFlow:
    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def router(self) -> ModelRouter:
        return ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        return PromptBuilder()

    @pytest.fixture
    def runner(
        self,
        storage: FileSystemStorage,
        router: ModelRouter,
        builder: PromptBuilder,
        runtime_settings: Settings,
    ) -> ChapterRunner:
        return ChapterRunner(
            router,
            builder,
            storage,
            config=ChapterRunnerConfig(),
            settings=runtime_settings,
        )

    @pytest.fixture
    def runtime_settings(self) -> Settings:
        return Settings(_env_file=None)

    def _make_alignment_report(
        self,
        score: float,
        *,
        missing_main_points: list[str] | None = None,
        repair_actions: list[str] | None = None,
    ) -> AlignmentReport:
        return AlignmentReport(
            alignment_score=score,
            summary="test alignment",
            missing_main_points=missing_main_points or [],
            repair_actions=repair_actions or [],
        )

    def _make_chapter_outcome(self) -> ChapterOutcome:
        return ChapterOutcome(
            source_chapter=1,
            creative_report=CreativeReport(),
            chapter_exit_state=ChapterExitState(chapter_number=1),
        )

    def _make_pending_state(self, text: str, tickets: list[RepairTicket] | None = None, alignment_score: float = 8.0):
        return PendingChapterReviewState(
            current_text=text,
            performed_edits=0,
            outcome=self._make_chapter_outcome(),
            alignment_report=self._make_alignment_report(alignment_score),
            chapter_repair_report=None,
            causal_report=None,
            continuity_report=ContinuityReport(continuity_score=8.5, summary="test", issues=[]),
            repair_plan=None,
            eval_report=None,
            guard_decision=None,
            warnings=(),
            review_findings=(),
            repair_tickets=tuple(tickets or []),
        )


class TestQualityGate(TestGuardrailRepairFlow):
    async def test_quality_gate_passes_no_regression(self, runner: ChapterRunner) -> None:
        on_step = MagicMock()

        final_text, is_rollback, result = await guard_repair_quality_gate(
            alignment_score_before=8.0,
            alignment_score_after=8.2,
            original_text="original text",
            repaired_text="repaired text with minor improvements",
            runner=runner,
            on_step=on_step,
            chapter_number=1,
        )

        assert result.passed is True
        assert is_rollback is False
        assert result.is_secondary_repair is False
        assert final_text == "repaired text with minor improvements"
        assert result.score_delta == pytest.approx(0.2)

    async def test_quality_gate_passes_small_drop(self, runner: ChapterRunner) -> None:
        on_step = MagicMock()

        final_text, is_rollback, result = await guard_repair_quality_gate(
            alignment_score_before=8.0,
            alignment_score_after=7.6,
            original_text="original text",
            repaired_text="repaired text",
            runner=runner,
            on_step=on_step,
            chapter_number=1,
        )

        assert result.passed is True
        assert is_rollback is False
        assert result.is_secondary_repair is False
        assert result.score_delta == pytest.approx(-0.4)

    async def test_quality_gate_triggers_secondary_repair(self, runner: ChapterRunner) -> None:
        on_step = MagicMock()

        final_text, is_rollback, result = await guard_repair_quality_gate(
            alignment_score_before=8.0,
            alignment_score_after=7.0,
            original_text="original text",
            repaired_text="repaired text with major changes",
            runner=runner,
            on_step=on_step,
            chapter_number=1,
            max_secondary_attempts=1,
        )

        assert result.passed is True
        assert is_rollback is False
        assert result.is_secondary_repair is True
        assert "Secondary repair requested" in result.reason
        on_step.assert_any_call(
            "quality_gate_regression_detected",
            {
                "chapter": 1,
                "score_before": 8.0,
                "score_after": 7.0,
                "score_delta": -1.0,
                "rollback_threshold": 0.5,
                "max_secondary_attempts": 1,
            },
        )
        on_step.assert_any_call(
            "quality_gate_secondary_repair_requested",
            {
                "chapter": 1,
                "attempts": 1,
                "conservative_max_change_ratio": 0.03,
            },
        )

    async def test_quality_gate_rollback_when_no_secondary_attempts(
        self, runner: ChapterRunner
    ) -> None:
        on_step = MagicMock()

        final_text, is_rollback, result = await guard_repair_quality_gate(
            alignment_score_before=8.0,
            alignment_score_after=7.0,
            original_text="original text",
            repaired_text="repaired text with major changes",
            runner=runner,
            on_step=on_step,
            chapter_number=1,
            max_secondary_attempts=0,
        )

        assert result.passed is False
        assert is_rollback is True
        assert final_text == "original text"
        assert result.is_secondary_repair is False
        on_step.assert_any_call(
            "quality_gate_rollback",
            {
                "chapter": 1,
                "secondary_attempts": 0,
                "final_score": 7.0,
                "rollback_score": 8.0,
                "reason": "No secondary attempts, rolling back to pre-repair version",
            },
        )


class TestPreEvaluationRegression(TestGuardrailRepairFlow):
    def test_followup_required_for_actionable_alignment_drop(self) -> None:
        before_report = self._make_alignment_report(8.5)
        after_report = self._make_alignment_report(
            7.9,
            repair_actions=["补足关键揭示节点"],
        )
        settings = SimpleNamespace(
            long_alignment_threshold=8.0,
            guardrail_regression_threshold=0.5,
        )

        assert _guard_ticket_alignment_followup_required(
            before=before_report,
            after=after_report,
            settings=settings,
        )

    def test_followup_not_required_without_repair_guidance(self) -> None:
        before_report = self._make_alignment_report(8.5)
        after_report = self._make_alignment_report(7.9)
        settings = SimpleNamespace(
            long_alignment_threshold=8.0,
            guardrail_regression_threshold=0.5,
        )

        assert not _guard_ticket_alignment_followup_required(
            before=before_report,
            after=after_report,
            settings=settings,
        )

    def test_followup_not_required_for_small_allowed_drop(self) -> None:
        before_report = self._make_alignment_report(8.2)
        after_report = self._make_alignment_report(
            7.9,
            missing_main_points=["补足主线动作"],
        )
        settings = SimpleNamespace(
            long_alignment_threshold=8.0,
            guardrail_regression_threshold=0.5,
        )

        assert not _guard_ticket_alignment_followup_required(
            before=before_report,
            after=after_report,
            settings=settings,
        )

    def test_pre_evaluation_triggers_warning_small_drop(self, runtime_settings: Settings) -> None:
        before_report = self._make_alignment_report(8.0)
        after_report = self._make_alignment_report(7.6)

        _raise_if_guard_ticket_alignment_regression(
            before=before_report,
            after=after_report,
            settings=runtime_settings,
            chapter_number=1,
        )

    def test_pre_evaluation_triggers_error_large_drop(self, runtime_settings: Settings) -> None:
        before_report = self._make_alignment_report(8.0)
        after_report = self._make_alignment_report(6.5)

        with pytest.raises(RuntimeError, match="对齐分从 8.0 降至 6.5"):
            _raise_if_guard_ticket_alignment_regression(
                before=before_report,
                after=after_report,
                settings=runtime_settings,
                chapter_number=1,
            )

    def test_guard_ticket_semantic_anchor_uses_current_text(self) -> None:
        text = (
            "沈念卿站在签约台前，灯光压得她有些恍惚。\n\n"
            "沈念卿扣紧怀表，金属在掌心发烫。程砚秋压低声音提醒她，"
            "陆云峥已经看见了那份协议。"
        )
        ticket = RepairTicket(
            source_module="guard_constraint_compliance",
            issue_type="guard_constraint_partial",
            severity="high",
            target_summary="签约仪式缺少程砚秋低语提醒与怀表发烫回环",
            repair_goal="补上程砚秋低语提醒沈念卿怀表发烫，但不改动签约结果。",
            metadata={
                "status": "partial",
                "constraint": "程砚秋必须以低语方式提醒沈念卿怀表发烫。",
                "evidence_quote": "审查认为程砚秋提醒不够明确",
            },
        )

        issue = _ticket_to_continuity_issue(ticket, text)

        assert issue.paragraph_start == 2
        assert issue.paragraph_end == 2
        assert issue.anchor_type == "semantic_terms"
        assert issue.location_confidence >= 0.6
        assert "程砚秋" in issue.evidence_quote

    def test_guard_repair_plan_softens_required_contextual_forbidden_items(self) -> None:
        plan = ChapterPlan(
            closing_contract="章末必须回到外滩钟楼，让怀表发烫成为下一章交接点。",
            forbidden_elements=[
                "外滩钟楼（场景锚点）——仅用一次",
                "金丝微颤",
            ],
        )

        sanitized = _sanitize_guard_repair_plan(plan, ())

        assert sanitized.forbidden_elements == ["金丝微颤"]
        assert "外滩钟楼（场景锚点）——仅用一次" in sanitized.forbidden_elements_quota

    def test_pre_evaluation_passes_when_score_meets_threshold(
        self, runtime_settings: Settings
    ) -> None:
        before_report = self._make_alignment_report(9.0)
        after_report = self._make_alignment_report(7.5)

        _raise_if_guard_ticket_alignment_regression(
            before=before_report,
            after=after_report,
            settings=runtime_settings,
            chapter_number=1,
        )

    def test_pre_evaluation_error_at_exact_threshold(
        self, runtime_settings: Settings
    ) -> None:
        before_report = self._make_alignment_report(7.4)
        after_report = self._make_alignment_report(6.9)

        with pytest.raises(RuntimeError):
            _raise_if_guard_ticket_alignment_regression(
                before=before_report,
                after=after_report,
                settings=runtime_settings,
                chapter_number=1,
            )


class TestRegressionCheck(TestGuardrailRepairFlow):
    def test_regression_check_passes_small_drop(self, runtime_settings: Settings) -> None:
        before_report = self._make_alignment_report(8.0)
        after_report = self._make_alignment_report(7.7)

        _raise_if_guard_ticket_alignment_regression(
            before=before_report,
            after=after_report,
            settings=runtime_settings,
            chapter_number=1,
        )

    def test_regression_check_blocks_large_drop(self, runtime_settings: Settings) -> None:
        before_report = self._make_alignment_report(8.0)
        after_report = self._make_alignment_report(6.5)

        with pytest.raises(RuntimeError):
            _raise_if_guard_ticket_alignment_regression(
                before=before_report,
                after=after_report,
                settings=runtime_settings,
                chapter_number=1,
            )

    def test_regression_check_exact_threshold(self, runtime_settings: Settings) -> None:
        before_report = self._make_alignment_report(7.4)
        after_report = self._make_alignment_report(6.9)

        with pytest.raises(RuntimeError):
            _raise_if_guard_ticket_alignment_regression(
                before=before_report,
                after=after_report,
                settings=runtime_settings,
                chapter_number=1,
            )


class TestRestoreAfterRegression(TestGuardrailRepairFlow):
    def test_restore_pending_text_after_guard_regression(
        self,
        storage: FileSystemStorage,
        runtime_settings: Settings,
        router: ModelRouter,
        builder: PromptBuilder,
        tmp_path: Path,
    ) -> None:
        from novel_forge.workspace.runtime import RuntimeServices

        runtime = RuntimeServices(
            settings=runtime_settings,
            router=router,
            builder=builder,
            storage=storage,
        )

        layout = ProjectLayout(tmp_path / "test_project")
        chapter_path = layout.chapter_path(1)
        storage.save_text(chapter_path, "original chapter text")

        stale_hash = _source_text_hash("regressed chapter text")
        storage.save_json(
            layout.alignment_report_path(1),
            {
                "alignment_score": 5.0,
                "summary": "regressed alignment",
                "source_text_hash": stale_hash,
            },
        )
        storage.save_json(
            layout.continuity_report_path(1),
            {
                "continuity_score": 5.0,
                "summary": "regressed continuity",
                "issues": [],
                "source_text_hash": stale_hash,
            },
        )

        pending = self._make_pending_state("original chapter text")

        bundle = SimpleNamespace(layout=layout)

        _restore_pending_text_after_guard_regression(
            runtime=runtime,
            bundle=bundle,
            chapter_number=1,
            pending=pending,
        )

        restored_text = storage.load_text(chapter_path)
        assert restored_text == "original chapter text"
        restored_hash = _source_text_hash("original chapter text")
        assert storage.load_json(layout.alignment_report_path(1))["source_text_hash"] == restored_hash
        assert storage.load_json(layout.alignment_report_path(1))["alignment_score"] == 8.0
        assert storage.load_json(layout.continuity_report_path(1))["source_text_hash"] == restored_hash

    def test_restore_pending_text_uses_review_draft_if_chapter_missing(
        self,
        storage: FileSystemStorage,
        runtime_settings: Settings,
        router: ModelRouter,
        builder: PromptBuilder,
        tmp_path: Path,
    ) -> None:
        from novel_forge.workspace.runtime import RuntimeServices

        runtime = RuntimeServices(
            settings=runtime_settings,
            router=router,
            builder=builder,
            storage=storage,
        )

        layout = ProjectLayout(tmp_path / "test_project")
        review_draft_path = layout.chapter_review_draft_path(1)
        storage.save_text(review_draft_path, "review draft text")

        pending = self._make_pending_state("review draft text")

        bundle = SimpleNamespace(layout=layout)

        _restore_pending_text_after_guard_regression(
            runtime=runtime,
            bundle=bundle,
            chapter_number=1,
            pending=pending,
        )

        restored_text = storage.load_text(review_draft_path)
        assert restored_text == "review draft text"


class TestGuardrailRepairFullFlow(TestGuardrailRepairFlow):
    async def test_guardrail_repair_flow(
        self,
        runner: ChapterRunner,
        runtime_settings: Settings,
    ) -> None:
        before_score = 8.0
        before_report = self._make_alignment_report(before_score)

        after_score = 6.5

        on_step = MagicMock()
        original_text = "original chapter text with good alignment"
        repaired_text = "repaired text that broke alignment"

        final_text, is_rollback, quality_result = await guard_repair_quality_gate(
            alignment_score_before=before_score,
            alignment_score_after=after_score,
            original_text=original_text,
            repaired_text=repaired_text,
            runner=runner,
            on_step=on_step,
            chapter_number=1,
            max_secondary_attempts=1,
        )

        assert quality_result.is_secondary_repair is True
        assert is_rollback is False

        secondary_after_score = 6.8
        secondary_after_report = self._make_alignment_report(secondary_after_score)

        with pytest.raises(RuntimeError, match="对齐分从"):
            _raise_if_guard_ticket_alignment_regression(
                before=before_report,
                after=secondary_after_report,
                settings=runtime_settings,
                chapter_number=1,
            )


    async def test_guardrail_repair_flow_passes(
        self,
        runner: ChapterRunner,
        runtime_settings: Settings,
    ) -> None:
        before_score = 7.5
        before_report = self._make_alignment_report(before_score)

        after_score = 8.5
        after_report = self._make_alignment_report(after_score)

        on_step = MagicMock()
        original_text = "original text"
        repaired_text = "improved text"

        final_text, is_rollback, quality_result = await guard_repair_quality_gate(
            alignment_score_before=before_score,
            alignment_score_after=after_score,
            original_text=original_text,
            repaired_text=repaired_text,
            runner=runner,
            on_step=on_step,
            chapter_number=1,
        )

        assert quality_result.passed is True
        assert is_rollback is False
        assert final_text == repaired_text

        _raise_if_guard_ticket_alignment_regression(
            before=before_report,
            after=after_report,
            settings=runtime_settings,
            chapter_number=1,
        )


def _write_minimal_chapter_state(
    storage: FileSystemStorage, project_id: str, chapter_num: int, *, chapter_text: str = "原始正文"
) -> ProjectLayout:
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    chapter_outline = ChapterOutline(
        chapter_number=chapter_num,
        title="测试章",
        goal="推进冲突",
    )
    packet = ChapterStatePacket(
        chapter_number=chapter_num,
        chapter_outline=chapter_outline,
        canon_context={},
    )
    storage.save_json(layout.chapter_state_packet_path(chapter_num), packet.model_dump(mode="json"))
    storage.save_json(
        layout.chapter_bridge_path(chapter_num),
        ChapterBridge(to_chapter=chapter_num).model_dump(mode="json"),
    )
    storage.save_json(
        layout.chapter_plan_path(chapter_num),
        ChapterPlan().model_dump(mode="json"),
    )
    storage.save_json(
        layout.spec_path,
        {"genre": "fantasy", "tone": "epic"},
    )
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": chapter_num,
            "volume_mode": False,
            "volumes": [],
            "chapters": [
                {
                    "chapter_number": chapter_num,
                    "title": "测试章",
                    "goal": "推进冲突",
                }
            ],
        },
    )
    storage.save_text(layout.chapter_path(chapter_num), chapter_text)
    return layout


class TestE2EManualRepair:
    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def router(self) -> ModelRouter:
        return ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        return PromptBuilder()

    @pytest.fixture
    def runtime(
        self,
        storage: FileSystemStorage,
        router: ModelRouter,
        builder: PromptBuilder,
        runtime_settings: Settings,
    ) -> RuntimeServices:
        return RuntimeServices(
            settings=runtime_settings,
            router=router,
            builder=builder,
            storage=storage,
        )

    async def test_e2e_manual_repair_success(
        self,
        storage: FileSystemStorage,
        runtime: RuntimeServices,
        monkeypatch,
    ) -> None:
        original_text = "这是原始章节正文内容，包含一些需要修复的连贯性问题。"
        repaired_text = "这是修复后的章节正文内容，连贯性问题已解决。"
        layout = _write_minimal_chapter_state(
            storage, "e2e_repair_project", 1, chapter_text=original_text
        )

        issue = ContinuityIssue(
            issue_type="character_name_mismatch",
            severity="high",
            summary="角色名称不一致",
            location="第3段",
        )
        storage.save_json(
            layout.continuity_report_path(1),
            ContinuityReport(
                continuity_score=7.0,
                summary="存在连贯性问题",
                issues=[issue],
            ).model_dump(mode="json"),
        )

        original_hash = _source_text_hash(original_text)
        steps: list[tuple[str, object]] = []

        async def _fake_run_repair(_step, _payload):
            return ContinuityRepairResult(
                revised_text=repaired_text,
                repair_plan=RepairPlan(expected_outcome="done"),
                applied=True,
                repaired_issue_types=("continuity",),
            )

        async def _fake_recheck(_self, _payload):
            return ContinuityReport(
                continuity_score=9.0,
                summary="连贯性良好",
                issues=[],
            )

        monkeypatch.setattr(
            "novel_forge.pipeline.long.repair.run_continuity_repair", _fake_run_repair
        )
        monkeypatch.setattr(
            "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
            _fake_recheck,
        )

        request = RepairContinuityRequest(
            project_id="e2e_repair_project",
            chapter_number=1,
            issue_indices=[0],
        )
        mission = continuity_repair_mission(request, settings=runtime.settings, control_mode="ai_auto")
        mission.policy["change_budget"] = 1.0

        result = await execute_repair(
            runtime, mission, on_step_progress=lambda step, data: steps.append((step, data))
        )

        assert result.result.applied is True

        saved_text = storage.load_text(layout.chapter_path(1))
        assert saved_text == repaired_text

        report_data = storage.load_json(layout.continuity_report_path(1))
        assert report_data["source_text_hash"] == _source_text_hash(repaired_text)
        assert report_data["source_text_hash"] != original_hash

        assert len(report_data["issues"]) == 0
        assert report_data["continuity_score"] == 9.0

        step_names = [s for s, _ in steps]
        assert "repair_continuity_start" in step_names
        assert "continuity_eval_after_repair" in step_names
        assert "repair_continuity" in step_names

    async def test_e2e_manual_repair_drift_rollback(
        self,
        storage: FileSystemStorage,
        runtime: RuntimeServices,
        runtime_settings: Settings,
        router: ModelRouter,
        builder: PromptBuilder,
        monkeypatch,
    ) -> None:
        original_text = "这是原始章节正文。李明走进了房间，他感到很紧张。"
        drifted_text = "这是完全不同的内容。张三离开了房子，她感到非常高兴。天气很好。"

        layout = _write_minimal_chapter_state(
            storage, "e2e_drift_project", 1, chapter_text=original_text
        )

        issue = ContinuityIssue(
            issue_type="character_name_mismatch",
            severity="high",
            summary="角色名称不一致",
            location="第2段",
        )
        storage.save_json(
            layout.continuity_report_path(1),
            ContinuityReport(
                continuity_score=7.0,
                summary="存在连贯性问题",
                issues=[issue],
            ).model_dump(mode="json"),
        )

        original_hash = _source_text_hash(original_text)
        steps: list[tuple[str, object]] = []

        async def _fake_run_repair_drift(_step, _payload):
            return ContinuityRepairResult(
                revised_text=drifted_text,
                repair_plan=RepairPlan(expected_outcome="done"),
                applied=True,
                repaired_issue_types=("continuity",),
                warnings=("语义漂移检测：修改幅度过大",),
            )

        async def _fake_recheck_drift(_self, _payload):
            drifted_issue = ContinuityIssue(
                issue_type="semantic_drift",
                severity="critical",
                summary="语义漂移：角色和场景完全改变",
                location="全文",
            )
            return ContinuityReport(
                continuity_score=3.0,
                summary="检测到严重语义漂移",
                issues=[drifted_issue],
            )

        monkeypatch.setattr(
            "novel_forge.pipeline.long.repair.run_continuity_repair", _fake_run_repair_drift
        )
        monkeypatch.setattr(
            "novel_forge.pipeline.steps.continuity_eval_step.ContinuityEvalStep.run",
            _fake_recheck_drift,
        )

        request = RepairContinuityRequest(
            project_id="e2e_drift_project",
            chapter_number=1,
            issue_indices=[0],
        )
        mission = continuity_repair_mission(request, settings=runtime.settings, control_mode="ai_auto")
        mission.policy["change_budget"] = 1.0

        result = await execute_repair(
            runtime, mission, on_step_progress=lambda step, data: steps.append((step, data))
        )

        assert result.result.applied is True

        saved_text = storage.load_text(layout.chapter_path(1))
        assert saved_text == drifted_text

        report_data = storage.load_json(layout.continuity_report_path(1))
        assert report_data["source_text_hash"] == _source_text_hash(drifted_text)
        assert report_data["source_text_hash"] != original_hash

        assert len(report_data["issues"]) >= 1
        assert report_data["continuity_score"] <= 5.0

        assert any("漂移" in str(w) for w in result.result.warnings)

        step_names = [s for s, _ in steps]
        assert "repair_continuity" in step_names
