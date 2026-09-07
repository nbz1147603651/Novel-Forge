"""Self-healing tests for the archive hard gate in book-auto mode.

Covers the root-cause fixes for "归档选择节点连跑中断":

- 修复1: when the alignment evidence is NOT bound to the rejected draft, the
  retry checkpoint schedules a refresh-then-repair pass instead of pausing.
- 修复2: ``AlignmentStep._execute`` stamps ``source_text_hash`` on its report.
- 修复3: the checkpoint summary decision_line stays consistent with the
  overridden ``is_recommended`` flag.
- 修复4: the auto-pilot falls back to a viable repair recorded in checkpoint
  metadata instead of stopping on a ``pause_for_human`` recommendation.
"""

from __future__ import annotations

import dataclasses
from contextlib import asynccontextmanager
from types import SimpleNamespace

import novel_forge.workspace.sessions.chapter_session_handlers as chapter_session_handlers
from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterOutcome,
)
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import (
    DecisionCheckpoint,
    DecisionOption,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_state import (
    GuardCheckpointSessionState,
    PendingChapterReviewState,
    build_guard_checkpoint,
    serialize_pending_result,
)


def _build_runtime(tmp_path, runtime_settings) -> RuntimeServices:
    return RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )


def _write_minimal_chapter_state(
    storage: FileSystemStorage, project_id: str, chapter_num: int
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
        layout.continuity_report_path(chapter_num),
        ContinuityReport().model_dump(mode="json"),
    )
    storage.save_json(layout.spec_path, {"genre": "fantasy", "tone": "epic"})
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
    storage.save_text(layout.chapter_path(chapter_num), "原始正文")
    return layout


def _build_pending_state(*, alignment_hash: str = "") -> PendingChapterReviewState:
    return PendingChapterReviewState(
        current_text="进入最终化前正文",
        performed_edits=0,
        outcome=ChapterOutcome(
            source_chapter=2,
            chapter_summary="旧摘要",
            creative_report=CreativeReport(structured_summary="旧创作报告"),
            chapter_exit_state=ChapterExitState(chapter_number=2),
        ),
        alignment_report=AlignmentReport(
            alignment_score=9.0,
            risk_level="low",
            conflict_level="low",
            summary="旧对齐",
            source_text_hash=alignment_hash,
        ),
        chapter_repair_report=None,
        causal_report=CausalValidationReport(
            causal_score=9.0,
            summary="旧因果",
            issues=[],
            causal_link_verified=True,
        ),
        continuity_report=ContinuityReport(
            continuity_score=9.0,
            summary="旧连贯",
            issues=[],
        ),
        repair_plan=RepairPlan(expected_outcome="old"),
        eval_report=EvalReport(
            overall_score=8.5,
            passed=True,
            threshold=6.0,
            summary="旧质量",
        ),
        guard_decision=None,
        warnings=(),
    )


async def _run_guard_checkpoint_with_alignment_block(
    *,
    tmp_path,
    runtime_settings,
    monkeypatch,
    report_hash: str,
    option_id: str = "accept_and_finalize",
    repair_control_mode: str = "ai_auto",
    prior_attempts: int = 0,
):
    """Drive resolve_guard_checkpoint into an alignment archive block.

    The fake finalize step persists an AlignmentReport whose score fails the
    archive threshold and whose ``source_text_hash`` is controlled by
    ``report_hash`` ("" = unbound evidence), then raises the archive gate
    exception.  Returns ``(result, steps, layout, runtime)``.
    """
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "long_demo", 2)
    pending_state = dataclasses.replace(
        _build_pending_state(),
        archive_quality_repair_attempts=prior_attempts,
    )
    bundle = SimpleNamespace(
        layout=layout,
        style_profile=None,
        canon_state=SimpleNamespace(current_chapter=0),
    )
    checkpoint = build_guard_checkpoint(
        bundle,
        2,
        current_text=pending_state.current_text,
        alignment_report=pending_state.alignment_report,
        continuity_report=pending_state.continuity_report,
        eval_report=pending_state.eval_report,
        causal_report=pending_state.causal_report,
        guard_decision=None,
    )
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint.checkpoint_id,
        project_id="long_demo",
        chapter_number=2,
        canon_watermark=0,
        pending_result=serialize_pending_result(pending_state),
    )
    orphan_dir = layout.states_dir / "orphan_chapter_drafts" / "chapter_002"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "20260619T195524000000Z.md").write_text("污染正文", encoding="utf-8")
    steps: list[tuple[str, object]] = []

    class _FakeRunner:
        _storage = runtime.storage
        _router = runtime.router
        _builder = runtime.builder
        _settings = runtime.settings

        def create_execution_context(self):
            return SimpleNamespace(
                storage=runtime.storage,
                router=runtime.router,
                builder=runtime.builder,
                settings=runtime.settings,
                config=SimpleNamespace(),
                on_step=lambda step, data: steps.append((step, data)),
            )

    @asynccontextmanager
    async def _fake_chapter_runner_memory_lifecycle(*_args, **_kwargs):
        yield _FakeRunner(), None

    async def _fake_prompt_leak_repair(**kwargs):
        return SimpleNamespace(
            applied=False,
            report_updated=False,
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            used_deterministic_fallback=False,
        )

    async def _fake_finalize(_context, *, review, trace, **_kwargs):
        del _context, review, trace
        blocked_alignment = AlignmentReport(
            alignment_score=2.6,
            risk_level="high",
            conflict_level="low",
            summary="终端润色后的正文缺少计划落点",
            missing_main_points=["江野确认编号后选择保密"],
            repair_actions=["补写江野确认编号并选择保密的明确决策"],
            source_text_hash=report_hash,
        )
        runtime.storage.save_json(
            layout.alignment_report_path(2),
            blocked_alignment.model_dump(mode="json"),
        )
        raise ConsistencyViolationError(
            ["章节 2 对齐分 2.6 低于归档阈值 7.0，拒绝保存并强制重新规划。"]
        )

    monkeypatch.setattr(
        chapter_session_handlers,
        "_chapter_runner_memory_lifecycle",
        _fake_chapter_runner_memory_lifecycle,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_leak_repair,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "word_count_archive_gate_enabled",
        lambda _settings: False,
    )
    monkeypatch.setattr(
        chapter_session_handlers,
        "finalize_chapter_result",
        _fake_finalize,
    )

    result = await chapter_session_handlers.resolve_guard_checkpoint(
        runtime,
        ResolveChapterCheckpointRequest(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=checkpoint.checkpoint_id,
            option_id=option_id,
            notes="",
            repair_control_mode=repair_control_mode,
        ),
        bundle=bundle,
        session_state=session_state,
        checkpoint=checkpoint,
        on_step_progress=lambda step, data: steps.append((step, data)),
    )
    return result, steps, layout, runtime


async def test_unbound_alignment_evidence_schedules_refresh_repair_in_ai_auto(
    tmp_path, runtime_settings, monkeypatch
) -> None:
    """修复1 core: empty report hash must NOT pause; it schedules refresh-first."""
    result, steps, _layout, _runtime = await _run_guard_checkpoint_with_alignment_block(
        tmp_path=tmp_path,
        runtime_settings=runtime_settings,
        monkeypatch=monkeypatch,
        report_hash="",  # unbound evidence
    )

    assert result.status == "needs_decision"
    assert result.metadata["archive_quality_block"] is True
    assert result.metadata["archive_quality_repair_evidence_bound"] is False
    assert result.metadata["archive_quality_refresh_before_repair"] is True
    assert result.metadata["archive_quality_proceed_with_repair"] is True
    assert result.metadata["archive_quality_auto_repair_scheduled"] is False
    assert result.checkpoint is not None
    assert any(
        option.option_id == "apply_repairs_and_finalize" and option.is_recommended
        for option in result.checkpoint.options
    )
    assert not any(
        option.option_id == "pause_for_human" and option.is_recommended
        for option in result.checkpoint.options
    )
    # 修复5: structured diagnosis event is emitted.
    diagnosis = [data for step, data in steps if step == "archive_quality_retry_diagnosis"]
    assert diagnosis, "expected archive_quality_retry_diagnosis step event"
    assert diagnosis[0]["refresh_before_repair"] is True
    assert diagnosis[0]["repair_evidence_bound"] is False
    assert diagnosis[0]["recommended_option"] == "apply_repairs_and_finalize"


async def test_bound_alignment_evidence_schedules_direct_repair(
    tmp_path, runtime_settings, monkeypatch
) -> None:
    """Bound evidence keeps the original direct-repair schedule."""
    result, _steps, _layout, _runtime = await _run_guard_checkpoint_with_alignment_block(
        tmp_path=tmp_path,
        runtime_settings=runtime_settings,
        monkeypatch=monkeypatch,
        report_hash=source_text_hash("污染正文"),  # bound to the orphan draft
    )

    assert result.metadata["archive_quality_repair_evidence_bound"] is True
    assert result.metadata["archive_quality_auto_repair_scheduled"] is True
    assert result.metadata["archive_quality_refresh_before_repair"] is False
    assert result.metadata["archive_quality_proceed_with_repair"] is True
    assert result.checkpoint is not None
    assert any(
        option.option_id == "apply_repairs_and_finalize" and option.is_recommended
        for option in result.checkpoint.options
    )


async def test_exhausted_budget_still_pauses_for_human(
    tmp_path, runtime_settings, monkeypatch
) -> None:
    """When the repair budget is exhausted the checkpoint must still pause."""
    result, _steps, _layout, _runtime = await _run_guard_checkpoint_with_alignment_block(
        tmp_path=tmp_path,
        runtime_settings=runtime_settings,
        monkeypatch=monkeypatch,
        report_hash="",  # unbound, but budget exhausted below
        option_id="apply_repairs_and_finalize",  # consumes one attempt
        prior_attempts=1,  # 1 + 1 == limit(2) → exhausted
    )

    assert result.metadata["archive_quality_auto_repair_exhausted"] is True
    assert result.metadata["archive_quality_proceed_with_repair"] is False
    assert result.checkpoint is not None
    assert any(
        option.option_id == "pause_for_human" and option.is_recommended
        for option in result.checkpoint.options
    )
    # 修复3 (strong case): the review matrix recommends apply_repairs (low
    # alignment score) but the override recommends pause_for_human.  The
    # decision_line must be rewritten so the UI does not claim "建议
    # apply_repairs_and_finalize" while the autopilot pauses.
    decision_line = next(
        line for line in result.checkpoint.summary.splitlines() if "AI 判定" in line
    )
    assert "pause_for_human" in decision_line
    assert "apply_repairs_and_finalize" not in decision_line


async def test_manual_mode_pauses_for_human(
    tmp_path, runtime_settings, monkeypatch
) -> None:
    """Auto repair disabled (manual mode) must pause even with budget left."""
    result, _steps, _layout, _runtime = await _run_guard_checkpoint_with_alignment_block(
        tmp_path=tmp_path,
        runtime_settings=runtime_settings,
        monkeypatch=monkeypatch,
        report_hash="",
        repair_control_mode="manual",
    )

    assert result.metadata["archive_quality_auto_repair_enabled"] is False
    assert result.metadata["archive_quality_proceed_with_repair"] is False
    assert result.checkpoint is not None
    assert any(
        option.option_id == "pause_for_human" and option.is_recommended
        for option in result.checkpoint.options
    )


async def test_decision_line_matches_recommended_option(
    tmp_path, runtime_settings, monkeypatch
) -> None:
    """修复3: the summary decision_line must name the same option that is
    actually recommended, so the UI and the autopilot never disagree."""
    result, _steps, _layout, _runtime = await _run_guard_checkpoint_with_alignment_block(
        tmp_path=tmp_path,
        runtime_settings=runtime_settings,
        monkeypatch=monkeypatch,
        report_hash="",  # refresh path recommends apply_repairs_and_finalize
    )

    assert result.checkpoint is not None
    recommended = next(
        option for option in result.checkpoint.options if option.is_recommended
    )
    decision_line = next(
        line for line in result.checkpoint.summary.splitlines() if "AI 判定" in line
    )
    # The decision_line may be format A ("… / 建议 X / …") or format B
    # ("AI 判定：X / …"); either way it must name the recommended option and
    # must NOT name any other option as the recommendation.
    assert recommended.option_id in decision_line
    for option in result.checkpoint.options:
        if option.option_id != recommended.option_id:
            assert f"建议 {option.option_id}" not in decision_line


def test_rewrite_summary_recommendation_handles_both_formats() -> None:
    """Unit-level check of the decision_line rewrite helper."""
    rewrite = chapter_session_handlers._rewrite_summary_recommendation

    format_a = "章节草稿已完成。\nAI 判定：continue_with_constraints / 建议 pause_for_human / 风险 high。"
    assert (
        rewrite(format_a, "apply_repairs_and_finalize")
        == "章节草稿已完成。\nAI 判定：continue_with_constraints / 建议 apply_repairs_and_finalize / 风险 high。"
    )

    format_b = "AI 判定：pause_for_human / 风险 medium。"
    assert rewrite(format_b, "apply_repairs_and_finalize") == (
        "AI 判定：apply_repairs_and_finalize / 风险 medium。"
    )

    # No decision line → unchanged.
    assert rewrite("没有判定行", "pause_for_human") == "没有判定行"


# ── 修复2: AlignmentStep stamps source_text_hash ─────────────────────────


async def test_alignment_execute_stamps_source_text_hash(monkeypatch) -> None:
    """The alignment report must carry the hash of the text it reviewed so the
    archive gate and the repair-evidence binding check can trust it."""
    from novel_forge.pipeline.steps.alignment_step import AlignmentStep

    step = object.__new__(AlignmentStep)
    step._settings = SimpleNamespace(temp_check_alignment=0.15)

    def fake_dynamic_max_tokens(self, task_type, target, *, prompt_overhead, min_tokens):
        del self, task_type, target, prompt_overhead, min_tokens
        return 4096

    async def fake_call_with_retry(self, task_type, context, **kwargs):
        del self, task_type, context, kwargs
        return {
            "alignment_score": 9.0,
            "risk_level": "low",
            "summary": "章节与大纲总体一致。",
            "missing_main_points": [],
            "supportive_subplot_points": [],
            "weak_subplot_points": [],
            "repair_actions": [],
        }

    monkeypatch.setattr(AlignmentStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
    monkeypatch.setattr(AlignmentStep, "_call_with_retry", fake_call_with_retry)

    chapter_text = "正文覆盖了本章目标。"
    input_data = SimpleNamespace(
        chapter_outline={"goal": "目标", "main_plot_points": ["主线点"]},
        chapter_plan={"scene_intents": []},
        chapter_text=chapter_text,
        narrative_context=None,
        genre="",
        pov_hint="",
    )

    report = await step._execute(input_data)

    assert report.source_text_hash == source_text_hash(chapter_text)


# ── 修复4: auto-pilot metadata fallback ──────────────────────────────────


def _autopilot_snapshot_with_checkpoint(checkpoint: DecisionCheckpoint):
    from novel_forge.workspace.contracts import (
        ChapterWorkspaceChapter,
        ChapterWorkspaceSnapshot,
    )

    return ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=3,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=3, status="current"),
            ChapterWorkspaceChapter(chapter_number=4, status="pending"),
        ],
        pending_checkpoint=checkpoint,
    )


def _pause_checkpoint(*, metadata: dict | None = None) -> DecisionCheckpoint:
    return DecisionCheckpoint(
        checkpoint_id="cp-archive-retry",
        checkpoint_type="guard_checkpoint",
        options=[
            DecisionOption(option_id="apply_repairs_and_finalize", label="应用修复后归档"),
            DecisionOption(
                option_id="pause_for_human",
                label="暂停人工处理",
                is_recommended=True,
            ),
        ],
        metadata=metadata or {},
    )


def _decide(snapshot):
    from novel_forge.desktop.pages.chapter_studio.autorun import (
        AutoPilotContext,
        decide_autopilot_action,
    )

    return decide_autopilot_action(
        AutoPilotContext(
            mode="book_auto",
            auto_started=True,
            auto_pilot_pending=False,
            studio=snapshot,
            latest_job=None,
            last_submitted_checkpoint_id=None,
            current_chapter_done=False,
            book_auto_skip_done=True,
            current_project_id="project-demo",
        )
    )


def test_autopilot_stops_on_pause_without_viable_path() -> None:
    """No metadata → a pause_for_human recommendation still stops the run."""
    snapshot = _autopilot_snapshot_with_checkpoint(_pause_checkpoint())
    decision = _decide(snapshot)
    assert decision.action == "stop"


def test_autopilot_resolves_repair_when_metadata_shows_viable_path() -> None:
    """修复4: metadata recording a scheduled repair overrides the pause."""
    snapshot = _autopilot_snapshot_with_checkpoint(
        _pause_checkpoint(
            metadata={
                "archive_quality_proceed_with_repair": True,
                "archive_quality_auto_repair_exhausted": False,
            }
        )
    )
    decision = _decide(snapshot)
    assert decision.action == "resolve_checkpoint"
    assert decision.option is not None
    assert decision.option.option_id == "apply_repairs_and_finalize"


def test_autopilot_stops_when_repair_budget_exhausted() -> None:
    """Even with proceed flagged, an exhausted budget must still stop."""
    snapshot = _autopilot_snapshot_with_checkpoint(
        _pause_checkpoint(
            metadata={
                "archive_quality_proceed_with_repair": True,
                "archive_quality_auto_repair_exhausted": True,
            }
        )
    )
    decision = _decide(snapshot)
    assert decision.action == "stop"


def test_plan_to_guard_redirect_keeps_ai_auto_recommendation() -> None:
    """A raced write request must continue with the live archive decision."""
    from novel_forge.workspace.sessions.chapter_sessions import (
        _guard_redirect_option_id,
    )

    checkpoint = DecisionCheckpoint(
        checkpoint_id="guard-current",
        checkpoint_type="guard_checkpoint",
        options=[
            DecisionOption(option_id="accept_and_finalize", label="接受并归档"),
            DecisionOption(
                option_id="apply_repairs_and_finalize",
                label="应用修复后归档",
                is_recommended=True,
            ),
            DecisionOption(option_id="pause_for_human", label="暂停人工处理"),
        ],
    )

    assert (
        _guard_redirect_option_id(checkpoint, repair_control_mode="ai_auto")
        == "apply_repairs_and_finalize"
    )


def test_plan_to_guard_redirect_keeps_manual_mode_paused() -> None:
    """Manual sessions still surface the newly-created archive checkpoint."""
    from novel_forge.workspace.sessions.chapter_sessions import (
        _guard_redirect_option_id,
    )

    checkpoint = DecisionCheckpoint(
        checkpoint_id="guard-current",
        checkpoint_type="guard_checkpoint",
        options=[
            DecisionOption(
                option_id="accept_and_finalize",
                label="接受并归档",
                is_recommended=True,
            ),
            DecisionOption(
                option_id="pause_for_human",
                label="暂停人工处理",
            ),
        ],
    )

    assert (
        _guard_redirect_option_id(checkpoint, repair_control_mode="manual")
        == "pause_for_human"
    )


def test_plan_to_guard_redirect_uses_viable_archive_repair_metadata() -> None:
    """A transient pause recommendation cannot hide a proven repair path."""
    from novel_forge.workspace.sessions.chapter_sessions import (
        _guard_redirect_option_id,
    )

    checkpoint = _pause_checkpoint(
        metadata={
            "archive_quality_proceed_with_repair": True,
            "archive_quality_auto_repair_exhausted": False,
        }
    )

    assert (
        _guard_redirect_option_id(checkpoint, repair_control_mode="ai_auto")
        == "apply_repairs_and_finalize"
    )
