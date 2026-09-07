"""Integration tests for staged chapter-studio execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import novel_forge.workspace.sessions.chapter_session_handlers as chapter_session_handlers
from novel_forge.core.config import Settings
from novel_forge.core.exceptions import ChapterSessionStaleError
from novel_forge.core.schemas.chapter import AlignmentReport, PlotGuardDecision
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.long.stages import causal_repair, finalize_checks
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import (
    PrepareChapterRequest,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_sessions import (
    prepare_chapter_session,
    resolve_chapter_session,
)


class TestChapterSessionFlow:
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

    async def test_direct_run_and_chapter_session_finalize_same_artifacts(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
    ) -> None:
        direct_project_id = "direct_run"
        session_project_id = "session_run"

        for project_id in (direct_project_id, session_project_id):
            await runner.init_long(
                premise="一个关于时间回环的悬疑故事",
                project_id=project_id,
                genre="mystery",
                tone="suspenseful",
                total_chapters=3,
                words_per_chapter=1800,
        )

        direct_result = await runner.run_chapter(direct_project_id, chapter_number=1)
        assert direct_result.eval_report is not None
        assert direct_result.causal_report is not None
        assert direct_result.continuity_report is not None
        assert direct_result.bridge is not None

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=session_project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        assert prepare.checkpoint is not None
        assert prepare.checkpoint.checkpoint_type == "plan_checkpoint"

        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=session_project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )
        assert review.checkpoint is not None
        assert review.checkpoint.checkpoint_type == "guard_checkpoint"
        assert review.metadata["causal_score"] == direct_result.causal_report.causal_score
        assert review.metadata["causal_issue_count"] == len(direct_result.causal_report.issues)
        assert not any("因果链校验" in warning for warning in review.metadata["warnings"])

        final = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=session_project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="accept_and_finalize",
                max_edit_rounds=1,
                notes="",
            ),
        )

        session_layout = ProjectLayout(storage.existing_project_dir(session_project_id))
        session_text = storage.load_text(session_layout.chapter_path(1))

        assert final.status == "completed"
        assert session_text == direct_result.text
        assert final.word_count == direct_result.meta.word_count
        assert final.overall_score == direct_result.eval_report.overall_score
        # continuity_score may differ: direct run (no memory context) uses
        # ContinuityEvalStep (auto-pass 10.0 for ch1), while session flow
        # (with memory context) uses CriticAgent.  Both must be above threshold.
        assert final.continuity_score is not None and final.continuity_score >= 8.0
        assert direct_result.continuity_report.continuity_score >= 8.0
        assert final.bridge_summary == direct_result.bridge.bridge_summary
        assert final.metadata["causal_score"] == direct_result.causal_report.causal_score
        assert final.metadata["causal_issue_count"] == len(direct_result.causal_report.issues)
        assert not any("因果链校验" in warning for warning in final.metadata["warnings"])
        assert storage.exists(session_layout.creative_report_path(1))
        assert storage.exists(session_layout.eval_report_path(1))
        assert storage.exists(session_layout.chapter_causal_report_path(1))
        assert not storage.exists(session_layout.chapter_checkpoint_path(1))

    async def test_coauthor_chapter_requires_plan_and_exact_final_acceptance(
        self, runner: ChapterRunner, runtime: RuntimeServices, storage: FileSystemStorage
    ) -> None:
        from novel_forge.core.authoring import (
            AuthoringPolicy,
            AuthoringProposalDecision,
            AuthoringProposalRequest,
        )
        from novel_forge.persistence.authoring_proposals import ProposalStore
        from novel_forge.persistence.authoring_store import (
            AuthoringDeniedError,
            AuthoringStore,
            story_input_version,
        )
        from novel_forge.pipeline.finalization_manifest import tracked_finalization_ready
        from novel_forge.workspace.authoring_proposals import create_proposal, decide_proposal
        from novel_forge.workspace.execution import (
            execute_prepare_chapter,
            execute_resolve_chapter_checkpoint,
        )

        project_id = "coauthor_session"
        await runner.init_long(
            premise="一个关于时间回环的悬疑故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        store = AuthoringStore(layout.root)
        policy = store.set_policy(
            AuthoringPolicy(mode="coauthor", end_chapter=3), expected_version=0
        )
        store.start(expected_version=policy.version, input_version=story_input_version(layout.root))

        async def approve(request):
            proposal = await create_proposal(
                runtime,
                project_id,
                AuthoringProposalRequest(
                    command="checkpoint",
                    chapter_number=1,
                    option_id=request.option_id,
                    notes=request.notes,
                    title="作者明确确认此版本",
                ),
            )
            decide_proposal(
                layout.root,
                proposal.id,
                AuthoringProposalDecision(
                    decision="accept",
                    candidate_version=proposal.candidate_version,
                    input_version=proposal.input_version,
                    policy_version=proposal.policy_version,
                ),
            )
            return request.model_copy(
                update={
                    "authoring_approval_id": ProposalStore(layout.root).read(proposal.id)[
                        "approval_id"
                    ]
                }
            )

        prepare = (
            await execute_prepare_chapter(
                runtime, PrepareChapterRequest(project_id=project_id, chapter_number=1)
            )
        ).result
        assert prepare.checkpoint is not None
        write = ResolveChapterCheckpointRequest(
            project_id=project_id,
            chapter_number=1,
            checkpoint_id=prepare.checkpoint.checkpoint_id,
            option_id="write_now",
        )
        with pytest.raises(AuthoringDeniedError):
            await execute_resolve_chapter_checkpoint(runtime, write)
        review = (await execute_resolve_chapter_checkpoint(runtime, await approve(write))).result
        assert review.checkpoint is not None
        assert review.checkpoint.checkpoint_type == "guard_checkpoint"
        assert not layout.chapter_path(1).exists()
        with pytest.raises(AuthoringDeniedError, match="第 1 章"):
            await execute_prepare_chapter(
                runtime, PrepareChapterRequest(project_id=project_id, chapter_number=2)
            )
        accept = ResolveChapterCheckpointRequest(
            project_id=project_id,
            chapter_number=1,
            checkpoint_id=review.checkpoint.checkpoint_id,
            option_id="accept_and_finalize",
        )
        with pytest.raises(AuthoringDeniedError):
            await execute_resolve_chapter_checkpoint(runtime, accept)
        accepted_request = await approve(accept)
        final = (await execute_resolve_chapter_checkpoint(runtime, accepted_request)).result
        if final.status == "needs_decision":
            assert final.metadata.get("authoring_final_acceptance") is True, (
                final.checkpoint.summary
            )
            assert final.preview != review.preview
            assert not layout.chapter_path(1).exists()
            with pytest.raises(AuthoringDeniedError):
                await execute_resolve_chapter_checkpoint(runtime, accepted_request)
            reaccept = accept.model_copy(update={"checkpoint_id": final.checkpoint.checkpoint_id})
            final = (
                await execute_resolve_chapter_checkpoint(runtime, await approve(reaccept))
            ).result
        assert final.status == "completed"
        assert layout.chapter_path(1).is_file() and not layout.chapter_path(2).exists()
        assert tracked_finalization_ready(storage, layout, 1, allow_legacy=False)
        next_plan = (
            await execute_prepare_chapter(
                runtime, PrepareChapterRequest(project_id=project_id, chapter_number=2)
            )
        ).result
        assert next_plan.checkpoint.checkpoint_type == "plan_checkpoint"
        assert not layout.chapter_path(2).exists()

    async def test_regenerate_plan_with_notes_rebuilds_plan_checkpoint(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
    ) -> None:
        project_id = "session_regen"
        await runner.init_long(
            premise="一个关于失踪档案的故事",
            project_id=project_id,
            genre="mystery",
            tone="dark",
            total_chapters=3,
            words_per_chapter=1800,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        assert prepare.checkpoint is not None

        regenerated = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="regenerate_plan_with_notes",
                max_edit_rounds=1,
                notes="把母女冲突写得更明显",
            ),
        )

        assert regenerated.status == "needs_decision"
        assert regenerated.checkpoint is not None
        assert regenerated.checkpoint.checkpoint_type == "plan_checkpoint"
        assert regenerated.checkpoint.checkpoint_id != prepare.checkpoint.checkpoint_id

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        session_payload = json.loads(layout.chapter_session_path(1).read_text(encoding="utf-8"))
        assert session_payload["notes"] == "把母女冲突写得更明显"

    async def test_pause_for_human_keeps_guard_checkpoint_resumable(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
    ) -> None:
        project_id = "session_pause"
        await runner.init_long(
            premise="一个关于密室和伪证的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        assert prepare.checkpoint is not None

        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )
        assert review.checkpoint is not None
        assert review.metadata["causal_score"] is not None
        assert review.metadata["causal_issue_count"] == 0
        assert not any("因果链校验" in warning for warning in review.metadata["warnings"])

        paused = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="pause_for_human",
                max_edit_rounds=1,
                notes="",
            ),
        )
        assert paused.status == "needs_decision"
        assert paused.metadata["paused"] is True
        assert paused.checkpoint is not None
        assert paused.checkpoint.checkpoint_id == review.checkpoint.checkpoint_id

        finalized = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="accept_and_finalize",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert finalized.status == "completed"
        assert finalized.word_count > 0
        assert finalized.metadata["causal_score"] is not None
        assert finalized.metadata["causal_issue_count"] == 0
        assert not any("因果链校验" in warning for warning in finalized.metadata["warnings"])

    async def test_high_risk_guard_decision_blocks_accept_in_chapter_session(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        monkeypatch,
    ) -> None:
        project_id = "session_guard_gate"
        await runner.init_long(
            premise="一个关于被误导线索的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        runtime.settings.long_plot_guard_mode = "ai_judge"

        async def _fake_guard_judge(**_kwargs):
            return PlotGuardDecision(
                decision="pause_for_human",
                risk_level="high",
                outline_action="none",
                reasoning_brief="需人工复核后再决定是否归档",
            )

        monkeypatch.setattr(
            chapter_session_handlers,
            "_run_plot_guard_judge",
            _fake_guard_judge,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert review.checkpoint is not None
        option_ids = {option.option_id for option in review.checkpoint.options}
        assert "accept_and_finalize" not in option_ids
        assert "pause_for_human" in option_ids
        assert [option.option_id for option in review.checkpoint.options if option.is_recommended] == [
            "pause_for_human"
        ]

        with pytest.raises(ValueError, match="当前 guard checkpoint 不允许操作：accept_and_finalize"):
            await resolve_chapter_session(
                runtime,
                ResolveChapterCheckpointRequest(
                    project_id=project_id,
                    chapter_number=1,
                    checkpoint_id=review.checkpoint.checkpoint_id,
                    option_id="accept_and_finalize",
                    max_edit_rounds=1,
                    notes="",
                ),
            )

    async def test_apply_repairs_and_finalize_consumes_guard_repair_tickets(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
        monkeypatch,
    ) -> None:
        runtime.settings.long_word_count_archive_gate_enabled = False

        project_id = "session_guard_ticket_repair"
        await runner.init_long(
            premise="一个关于旧悬念回收的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert review.checkpoint is not None
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        session_payload = storage.load_json(layout.chapter_session_path(1))
        pending_payload = session_payload["pending_result"]
        pending_payload["continuity_report"]["issues"] = []
        if pending_payload.get("causal_report"):
            pending_payload["causal_report"]["issues"] = []
            pending_payload["causal_report"]["causal_score"] = 8.8
        pending_payload["guard_compliance_report"] = {
            "constraints": ["必须回应上一章的悬念"],
            "compliance_results": [
                {
                    "constraint": "必须回应上一章的悬念",
                    "status": "non_compliant",
                    "confidence": 0.92,
                    "evidence": "章尾仍停留在旧信息，没有回应悬念。",
                    "notes": "建议在后半章补上明确回应。",
                }
            ],
            "overall_compliance_rate": 0.0,
            "summary": "共 1 条约束，0 条已遵守 (0%)",
        }
        pending_payload["review_findings"] = []
        pending_payload["repair_tickets"] = [
            {
                "ticket_id": "ticket_guard_constraint_ch1_1",
                "chapter_number": 1,
                "finding_ids": ["guard_constraint_ch1_1"],
                "source_module": "guard_constraint_compliance",
                "dimension": "guard_compliance",
                "issue_type": "guard_constraint_missing",
                "severity": "high",
                "target_summary": "AI护栏约束未兑现: 必须回应上一章的悬念",
                "repair_goal": "在不改变当前章节主线结果的前提下，明确回应上一章悬念。",
                "repair_mode": "window",
                "acceptance_criteria": ["修复后需明确回应约束：必须回应上一章的悬念"],
                "must_preserve": ["保持主线推进"],
                "forbidden_changes": ["不得改写已通过质量门的主要情节结果"],
                "max_change_ratio": 0.08,
                "max_attempts": 2,
                "blocking": True,
                "metadata": {
                    "constraint": "必须回应上一章的悬念",
                    "status": "non_compliant",
                    "notes": "建议在后半章补上明确回应。",
                    "evidence_quote": "章尾仍停留在旧信息，没有回应悬念。",
                },
            }
        ]
        pending_payload["warnings"] = ["AI护栏约束合规率过低: 共 1 条约束，0 条已遵守 (0%)"]
        storage.save_json(layout.chapter_session_path(1), session_payload)

        calls: dict[str, int] = {"guard_repair": 0}

        async def _fake_guard_ticket_repair(**kwargs):
            calls["guard_repair"] += 1
            assert len(kwargs["pending"].repair_tickets) == 1
            return kwargs["current_text"] + "\n\n她终于把上一章留下的关键疑点说破。", kwargs["pending"].repair_plan, True

        async def _fake_guard_check(**kwargs):
            assert "关键疑点说破" in kwargs["current_text"]
            return {
                "constraints": ["必须回应上一章的悬念"],
                "compliance_results": [
                    {
                        "constraint": "必须回应上一章的悬念",
                        "status": "compliant",
                        "confidence": 0.95,
                        "evidence": "她终于把上一章留下的关键疑点说破。",
                        "notes": "已落实。",
                    }
                ],
                "overall_compliance_rate": 1.0,
                "summary": "共 1 条约束，1 条已遵守 (100%)",
            }

        def _fake_attach_guard_repair_metadata(*_args, **_kwargs):
            return [], []

        monkeypatch.setattr(
            chapter_session_handlers,
            "_run_guard_ticket_repair",
            _fake_guard_ticket_repair,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "check_guard_constraint_compliance",
            _fake_guard_check,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "attach_guard_repair_metadata",
            _fake_attach_guard_repair_metadata,
        )

        finalized = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="apply_repairs_and_finalize",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert calls["guard_repair"] == 1
        assert finalized.status == "completed"
        assert finalized.metadata["guard_compliance_rate"] == 1.0
        assert finalized.metadata["remaining_guard_ticket_count"] == 0
        assert finalized.metadata["guard_finding_count"] == 0
        assert not any(
            str(item).startswith("AI护栏约束合规率过低:")
            for item in finalized.metadata["warnings"]
        )

        quality_gate_payload = storage.load_json(layout.quality_gate_report_path(1))
        assert quality_gate_payload["guard_compliance"]["overall_compliance_rate"] == 1.0
        guard_tickets = [
            ticket
            for ticket in quality_gate_payload["repair_tickets"]
            if ticket.get("dimension") == "guard_compliance"
            or ticket.get("source_module") == "guard_constraint_compliance"
        ]
        assert guard_tickets == []

    async def test_apply_repairs_runs_alignment_followup_after_guard_regression(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
        monkeypatch,
    ) -> None:
        runtime.settings.long_alignment_threshold = 8.0
        runtime.settings.guardrail_regression_threshold = 0.5
        runtime.settings.long_word_count_archive_gate_enabled = False

        project_id = "session_guard_alignment_followup"
        await runner.init_long(
            premise="一个关于旧照片与承诺的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert review.checkpoint is not None
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        session_payload = storage.load_json(layout.chapter_session_path(1))
        pending_payload = session_payload["pending_result"]
        pending_payload["alignment_report"]["alignment_score"] = 8.5
        pending_payload["alignment_report"]["missing_main_points"] = []
        pending_payload["alignment_report"]["repair_actions"] = []
        pending_payload["continuity_report"]["issues"] = []
        if pending_payload.get("causal_report"):
            pending_payload["causal_report"]["issues"] = []
            pending_payload["causal_report"]["causal_score"] = 8.8
        pending_payload["guard_compliance_report"] = {
            "constraints": ["必须兑现旧照片承诺"],
            "compliance_results": [
                {
                    "constraint": "必须兑现旧照片承诺",
                    "status": "non_compliant",
                    "confidence": 0.91,
                    "evidence": "正文没有把旧照片承诺落成动作。",
                    "notes": "建议补足旧照片动作。",
                }
            ],
            "overall_compliance_rate": 0.0,
            "summary": "共 1 条约束，0 条已遵守 (0%)",
        }
        pending_payload["review_findings"] = []
        pending_payload["repair_tickets"] = [
            {
                "ticket_id": "ticket_guard_constraint_ch1_photo",
                "chapter_number": 1,
                "finding_ids": ["guard_constraint_ch1_photo"],
                "source_module": "guard_constraint_compliance",
                "dimension": "guard_compliance",
                "issue_type": "guard_constraint_missing",
                "severity": "high",
                "target_summary": "AI护栏约束未兑现: 必须兑现旧照片承诺",
                "repair_goal": "在不改变当前章节主线结果的前提下，明确兑现旧照片承诺。",
                "repair_mode": "window",
                "acceptance_criteria": ["修复后需明确兑现旧照片承诺"],
                "must_preserve": ["保持主线推进"],
                "forbidden_changes": ["不得改写已通过质量门的主要情节结果"],
                "max_change_ratio": 0.08,
                "max_attempts": 2,
                "blocking": True,
                "metadata": {
                    "constraint": "必须兑现旧照片承诺",
                    "status": "non_compliant",
                    "notes": "建议补足旧照片动作。",
                    "evidence_quote": "正文没有把旧照片承诺落成动作。",
                },
            }
        ]
        pending_payload["warnings"] = ["AI护栏约束合规率过低: 共 1 条约束，0 条已遵守 (0%)"]
        storage.save_json(layout.chapter_session_path(1), session_payload)

        calls: dict[str, int] = {
            "guard_repair": 0,
            "alignment_repair": 0,
            "recheck_alignment": 0,
        }
        alignment_reports = [
            AlignmentReport(
                alignment_score=7.9,
                summary="关键锚点被稀释",
                missing_main_points=["旧照片承诺缺少独立动作"],
                repair_actions=["补足递出旧照片并说破承诺的动作"],
            ),
            AlignmentReport(alignment_score=8.3, summary="关键锚点已补足"),
        ]

        async def _fake_guard_ticket_repair(**kwargs):
            calls["guard_repair"] += 1
            return (
                kwargs["current_text"] + "\n\n她把旧照片递到灯下，终于说破那句承诺。",
                kwargs["pending"].repair_plan,
                True,
            )

        async def _fake_guard_check(**kwargs):
            assert "旧照片" in kwargs["current_text"]
            return {
                "constraints": ["必须兑现旧照片承诺"],
                "compliance_results": [
                    {
                        "constraint": "必须兑现旧照片承诺",
                        "status": "compliant",
                        "confidence": 0.95,
                        "evidence": "她把旧照片递到灯下，终于说破那句承诺。",
                        "notes": "已落实。",
                    }
                ],
                "overall_compliance_rate": 1.0,
                "summary": "共 1 条约束，1 条已遵守 (100%)",
            }

        def _fake_attach_guard_repair_metadata(*_args, **_kwargs):
            return [], []

        async def _fake_recheck_alignment(*_args, **_kwargs):
            calls["recheck_alignment"] += 1
            if alignment_reports:
                return alignment_reports.pop(0)
            return AlignmentReport(alignment_score=8.4, summary="稳定通过")

        async def _fake_alignment_repair_edit(*_args, **kwargs):
            calls["alignment_repair"] += 1
            assert kwargs["alignment_report"].alignment_score == 7.9
            return kwargs["current_text"] + "\n\n她又补上一句：这张照片就是那年承诺的凭据。"

        monkeypatch.setattr(
            chapter_session_handlers,
            "_run_guard_ticket_repair",
            _fake_guard_ticket_repair,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "check_guard_constraint_compliance",
            _fake_guard_check,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "attach_guard_repair_metadata",
            _fake_attach_guard_repair_metadata,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "recheck_alignment",
            _fake_recheck_alignment,
        )
        monkeypatch.setattr(
            causal_repair,
            "alignment_repair_edit",
            _fake_alignment_repair_edit,
        )

        finalized = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="apply_repairs_and_finalize",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert finalized.status == "completed"
        assert calls["guard_repair"] == 1
        assert calls["alignment_repair"] == 1
        assert calls["recheck_alignment"] >= 2
        assert finalized.metadata["alignment_score"] >= 8.0

    async def test_guard_alignment_regression_returns_retry_checkpoint_instead_of_failing(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
        monkeypatch,
    ) -> None:
        runtime.settings.long_alignment_threshold = 8.0
        runtime.settings.guardrail_regression_threshold = 0.5
        runtime.settings.guard_ticket_alignment_followup_max_attempts = 2
        runtime.settings.long_word_count_archive_gate_enabled = False

        project_id = "session_guard_alignment_retry_checkpoint"
        await runner.init_long(
            premise="一个关于旧照片与承诺的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=1,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        review = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=prepare.checkpoint.checkpoint_id,
                option_id="write_now",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert review.checkpoint is not None
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        session_payload = storage.load_json(layout.chapter_session_path(1))
        pending_payload = session_payload["pending_result"]
        pending_payload["alignment_report"]["alignment_score"] = 6.6
        pending_payload["alignment_report"]["missing_main_points"] = []
        pending_payload["alignment_report"]["repair_actions"] = []
        pending_payload["continuity_report"]["issues"] = []
        if pending_payload.get("causal_report"):
            pending_payload["causal_report"]["issues"] = []
            pending_payload["causal_report"]["causal_score"] = 8.8
        pending_payload["guard_compliance_report"] = {
            "constraints": ["必须兑现旧照片承诺"],
            "compliance_results": [
                {
                    "constraint": "必须兑现旧照片承诺",
                    "status": "non_compliant",
                    "confidence": 0.91,
                    "evidence": "正文没有把旧照片承诺落成动作。",
                    "notes": "建议补足旧照片动作。",
                }
            ],
            "overall_compliance_rate": 0.0,
            "summary": "共 1 条约束，0 条已遵守 (0%)",
        }
        pending_payload["review_findings"] = []
        pending_payload["repair_tickets"] = [
            {
                "ticket_id": "ticket_guard_constraint_ch1_photo",
                "chapter_number": 1,
                "finding_ids": ["guard_constraint_ch1_photo"],
                "source_module": "guard_constraint_compliance",
                "dimension": "guard_compliance",
                "issue_type": "guard_constraint_missing",
                "severity": "high",
                "target_summary": "AI护栏约束未兑现: 必须兑现旧照片承诺",
                "repair_goal": "在不改变当前章节主线结果的前提下，明确兑现旧照片承诺。",
                "repair_mode": "window",
                "acceptance_criteria": ["修复后需明确兑现旧照片承诺"],
                "must_preserve": ["保持主线推进"],
                "forbidden_changes": ["不得改写已通过质量门的主要情节结果"],
                "max_change_ratio": 0.08,
                "max_attempts": 2,
                "blocking": True,
                "metadata": {
                    "constraint": "必须兑现旧照片承诺",
                    "status": "non_compliant",
                    "notes": "建议补足旧照片动作。",
                    "evidence_quote": "正文没有把旧照片承诺落成动作。",
                },
            }
        ]
        pending_payload["warnings"] = ["AI护栏约束合规率过低: 共 1 条约束，0 条已遵守 (0%)"]
        original_text = pending_payload["current_text"]
        storage.save_json(layout.chapter_session_path(1), session_payload)

        calls: dict[str, int] = {
            "guard_repair": 0,
            "alignment_repair": 0,
            "recheck_alignment": 0,
        }
        alignment_reports = [
            AlignmentReport(
                alignment_score=2.6,
                summary="护栏补丁破坏主线锚点",
                missing_main_points=["旧照片承诺偏离本章主线"],
                repair_actions=["恢复本章主线动作，再局部补足旧照片承诺"],
            ),
            AlignmentReport(
                alignment_score=6.2,
                summary="补救后仍未回到修复前",
                missing_main_points=["主线动作仍不完整"],
                repair_actions=["补足主线动作"],
            ),
        ]

        async def _fake_guard_ticket_repair(**kwargs):
            calls["guard_repair"] += 1
            return (
                kwargs["current_text"] + "\n\n她把旧照片递到灯下，却把本章主线拐向了旁枝。",
                kwargs["pending"].repair_plan,
                True,
            )

        async def _fake_guard_check(**kwargs):
            return {
                "constraints": ["必须兑现旧照片承诺"],
                "compliance_results": [
                    {
                        "constraint": "必须兑现旧照片承诺",
                        "status": "compliant",
                        "confidence": 0.95,
                        "evidence": "她把旧照片递到灯下。",
                        "notes": "已落实。",
                    }
                ],
                "overall_compliance_rate": 1.0,
                "summary": "共 1 条约束，1 条已遵守 (100%)",
            }

        def _fake_attach_guard_repair_metadata(*_args, **_kwargs):
            return [], []

        async def _fake_recheck_alignment(*_args, **_kwargs):
            calls["recheck_alignment"] += 1
            if alignment_reports:
                return alignment_reports.pop(0)
            return AlignmentReport(alignment_score=6.1, summary="仍未恢复")

        async def _fake_alignment_repair_edit(*_args, **kwargs):
            calls["alignment_repair"] += 1
            return kwargs["current_text"] + "\n\n她试图把话题拉回本章主线。"

        monkeypatch.setattr(
            chapter_session_handlers,
            "_run_guard_ticket_repair",
            _fake_guard_ticket_repair,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "check_guard_constraint_compliance",
            _fake_guard_check,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "attach_guard_repair_metadata",
            _fake_attach_guard_repair_metadata,
        )
        monkeypatch.setattr(
            chapter_session_handlers,
            "recheck_alignment",
            _fake_recheck_alignment,
        )
        monkeypatch.setattr(
            causal_repair,
            "alignment_repair_edit",
            _fake_alignment_repair_edit,
        )

        retry = await resolve_chapter_session(
            runtime,
            ResolveChapterCheckpointRequest(
                project_id=project_id,
                chapter_number=1,
                checkpoint_id=review.checkpoint.checkpoint_id,
                option_id="apply_repairs_and_finalize",
                max_edit_rounds=1,
                notes="",
            ),
        )

        assert retry.status == "needs_decision"
        assert retry.checkpoint is not None
        assert retry.metadata["guard_alignment_retry"] is True
        assert retry.metadata["alignment_score"] == pytest.approx(6.6)
        assert calls["guard_repair"] == 1
        assert calls["alignment_repair"] == 1
        assert calls["recheck_alignment"] >= 2

        retry_session = storage.load_json(layout.chapter_session_path(1))
        retry_pending = retry_session["pending_result"]
        assert retry_pending["current_text"] == original_text
        assert retry_pending["alignment_report"]["alignment_score"] == pytest.approx(6.6)
        assert any("回到归档选择" in item for item in retry_pending["warnings"])

    async def test_rewriting_upstream_chapter_clears_downstream_pending_artifacts(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
    ) -> None:
        project_id = "session_invalidation"
        await runner.init_long(
            premise="一个关于时间回环的悬疑故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=4,
            words_per_chapter=1800,
        )

        await runner.run_chapter(project_id, 1)

        chapter_2_prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=2,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        assert chapter_2_prepare.checkpoint is not None

        chapter_3_prepare = await prepare_chapter_session(
            runtime,
            PrepareChapterRequest(
                project_id=project_id,
                chapter_number=3,
                max_edit_rounds=1,
                force=False,
                notes="",
            ),
        )
        assert chapter_3_prepare.checkpoint is not None

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        assert storage.exists(layout.chapter_checkpoint_path(2))
        assert storage.exists(layout.chapter_session_path(2))
        assert storage.exists(layout.chapter_plan_path(3))
        assert storage.exists(layout.chapter_session_path(3))

        await runner.run_chapter(project_id, 1, force_regenerate=True)

        assert not storage.exists(layout.chapter_checkpoint_path(2))
        assert not storage.exists(layout.chapter_session_path(2))
        assert not storage.exists(layout.chapter_checkpoint_path(3))
        assert not storage.exists(layout.chapter_session_path(3))
        assert not storage.exists(layout.chapter_plan_path(3))
        assert not storage.exists(layout.chapter_state_packet_path(3))

    @pytest.mark.parametrize(
        ("old_watermark", "expected_kind", "expected_message"),
        [
            (None, "watermark_missing", "缺少上游水位"),
            (2, "canon_watermark_drift", "创建时上游水位为第 2 章"),
        ],
    )
    async def test_resolve_rejects_stale_checkpoint_from_old_downstream_chain(
        self,
        runner: ChapterRunner,
        runtime: RuntimeServices,
        storage: FileSystemStorage,
        monkeypatch,
        old_watermark: int | None,
        expected_kind: str,
        expected_message: str,
    ) -> None:
        # This test targets stale checkpoint rejection; carry-forward archive
        # quality is covered elsewhere and can make mock chapter 2 setup flaky.
        monkeypatch.setattr(
            finalize_checks,
            "_unclosed_carry_forward_items",
            lambda *_args, **_kwargs: [],
        )
        project_id = "session_stale_guard"
        await runner.init_long(
            premise="一个关于被篡改证词的故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        await runner.run_chapter(project_id, 1)
        await runner.run_chapter(project_id, 2)
        await runner.run_chapter(project_id, 1, force_regenerate=True)

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        storage.save_json(
            layout.chapter_checkpoint_path(2),
            {
                "checkpoint_id": "guard-002-stale",
                "checkpoint_type": "guard_checkpoint",
                "summary": "旧 checkpoint",
                "prompt": "不应继续使用",
                "options": [
                    {
                        "option_id": "accept_and_finalize",
                        "label": "接受并归档",
                        "description": "",
                        "is_recommended": True,
                    }
                ],
                "related_artifacts": [],
            },
        )
        storage.save_json(
            layout.chapter_session_path(2),
            {
                "stage": "guard_checkpoint",
                "checkpoint_id": "guard-002-stale",
                "project_id": project_id,
                "chapter_number": 2,
                "pending_result": {},
                **({"canon_watermark": old_watermark} if old_watermark is not None else {}),
            },
        )

        # Force regeneration already removed downstream artifacts. The restored
        # old session must still fail its own watermark guard, not rely on a
        # leftover archived chapter to trigger the separate stale-cutoff guard.
        archived_before = {
            path.name: path.read_bytes() for path in layout.chapters_dir.glob("*.md")
        }
        with pytest.raises(ChapterSessionStaleError, match=expected_message) as caught:
            await resolve_chapter_session(
                runtime,
                ResolveChapterCheckpointRequest(
                    project_id=project_id,
                    chapter_number=2,
                    checkpoint_id="guard-002-stale",
                    option_id="accept_and_finalize",
                    max_edit_rounds=1,
                    notes="",
                ),
            )
        assert caught.value.kind == expected_kind
        assert {
            path.name: path.read_bytes() for path in layout.chapters_dir.glob("*.md")
        } == archived_before
