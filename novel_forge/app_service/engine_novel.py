"""Novel-module read models for replaceable UI clients."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import Field

from novel_forge.app_service.book_autorun import BookAutorunState
from novel_forge.app_service.contracts import JobRecord, JobState
from novel_forge.app_service.engine_views import (
    ENGINE_CONTRACT_VERSION,
    EngineViewModel,
    project_job_view,
    project_running_operation_detail,
)
from novel_forge.app_service.workflow_projection import project_workflow_run
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot, DecisionCheckpoint
from novel_forge.workspace.projects import ProjectDetail


class EngineChapterRailItemView(EngineViewModel):
    number: int
    title: str
    state: Literal["completed", "current", "pending", "needs_decision"]
    detail: str = ""


class EngineChapterMemoryView(EngineViewModel):
    label: str
    value: str
    detail: str = ""


class EngineChapterMemoryCardView(EngineViewModel):
    id: str
    label: str
    content: str
    tone: Literal["default", "highlight", "warning"] = "default"


class EngineChapterMemoryTabView(EngineViewModel):
    id: Literal[
        "overview",
        "motifs",
        "relationships",
        "issues",
        "reading_power",
        "guardrails",
        "control",
    ]
    label: str
    cards: list[EngineChapterMemoryCardView] = Field(default_factory=list)


class EngineCheckpointOptionView(EngineViewModel):
    id: str
    label: str
    description: str = ""
    recommended: bool = False


class EngineCheckpointView(EngineViewModel):
    id: str
    title: str
    summary: str = ""
    prompt: str = ""
    options: list[EngineCheckpointOptionView] = Field(default_factory=list)


class EngineChapterActivityRunView(EngineViewModel):
    """One persisted chapter task available for historical inspection."""

    kind: str
    task_id: str
    status: Literal["queued", "running", "paused", "succeeded", "failed"]
    task_label: str = ""
    current_step_label: str = ""
    progress_percent: int | None = None
    updated_at: str = ""
    stages: list[dict[str, str]] = Field(default_factory=list)


class EngineChapterActivityView(EngineViewModel):
    kind: str = "run_chapter"
    task_id: str = ""
    state: Literal["idle", "running", "checkpoint"] = "idle"
    task_label: str = ""
    current_step_label: str = ""
    operation_detail: str = ""
    progress_percent: int | None = None
    checkpoint: EngineCheckpointView | None = None
    stages: list[dict[str, str]] = Field(default_factory=list)
    run_insights: list[dict[str, Any]] = Field(default_factory=list)
    efficiency: dict[str, Any] = Field(default_factory=dict)
    chapter_flow: EngineChapterActivityRunView | None = None
    history: list[EngineChapterActivityRunView] = Field(default_factory=list)


class EngineTaskErrorLogEntryView(EngineViewModel):
    id: str
    time_label: str
    job_label: str
    task_id: str
    task_label: str
    attempt_label: str = ""
    error_message: str
    excerpt: str = ""
    log_path: str = ""
    kind_label: str = ""
    auto_resolved: bool = False
    acknowledged_at: str = ""
    cause_code: str = ""
    auto_repair_state: str = "unknown"
    auto_repair_explanation: str = ""
    recommended_action: str = ""
    recovery_action_kinds: list[str] = Field(default_factory=list)


class EngineBookAutorunView(EngineViewModel):
    status: Literal[
        "idle",
        "waiting_init",
        "running",
        "retry_wait",
        "paused",
        "completed",
        "failed",
        "cancelled",
    ] = "idle"
    phase: str = ""
    mode: Literal["chapter", "book"] = "book"
    start_chapter: int = 0
    current_chapter: int = 0
    end_chapter: int = 0
    total_chapters: int = 0
    completed_chapters: list[int] = Field(default_factory=list)
    active_task_id: str = ""
    checkpoint: EngineCheckpointView | None = None
    checkpoint_attempts: int = 0
    checkpoint_budget: int = 0
    total_failures: int = 0
    failure_budget: int = 0
    next_retry_at: str = ""
    wait_reason: Literal["", "engine_restart_required"] = ""
    last_error: str = ""
    last_failure_kind: str = ""
    recovery_target: Literal["", "plan", "draft", "wave", "manual", "semantic"] = ""
    updated_at: str = ""


class EngineNovelStudioView(EngineViewModel):
    contract_version: str = ENGINE_CONTRACT_VERSION
    project_id: str
    project_title: str
    project_synopsis: str = ""
    next_chapter: int
    total_chapters: int
    chapters: list[EngineChapterRailItemView] = Field(default_factory=list)
    plan_title: str = ""
    plan_summary: str = ""
    previous_summary: str = ""
    previous_exit_summary: str = ""
    current_goal: str = ""
    current_outline_summary: str = ""
    next_goal: str = ""
    suggestion: str = ""
    memories: list[EngineChapterMemoryView] = Field(default_factory=list)
    memory_tabs: list[EngineChapterMemoryTabView] = Field(default_factory=list)
    activity: EngineChapterActivityView = Field(default_factory=EngineChapterActivityView)
    autorun: EngineBookAutorunView = Field(default_factory=EngineBookAutorunView)
    task_error_log: list[EngineTaskErrorLogEntryView] = Field(default_factory=list)


NOVEL_DURABLE_JOB_KINDS = {
    "run_short",
    "init_long",
    "init_repair_retry",
    "run_chapter",
    "prepare_chapter",
    "semantic_consistency",
    "resolve_chapter_checkpoint",
    "resolve_chapter_checkpoint_finalize",
    "repair_continuity",
    "repair_causal",
    "repair_issues",
    "reevaluate_chapter",
    "polish_chapter",
    "book_consistency",
    "book_editorial_audit",
    "global_repair_queue",
    "export_book",
    "reextract_relationships",
    "repair_motif_history",
    "rebuild_memory_vectors",
    "sync_chapter_contracts",
    "extend_outline",
}


def project_novel_studio_view(
    detail: ProjectDetail,
    snapshot: ChapterWorkspaceSnapshot,
    jobs: list[JobRecord],
    autorun_state: BookAutorunState | None = None,
    task_error_entries: list[dict[str, Any]] | None = None,
) -> EngineNovelStudioView:
    """Build the shared novel-studio read model from existing application data."""

    return EngineNovelStudioView(
        project_id=detail.project_id,
        project_title=snapshot.project_title or detail.title,
        project_synopsis=detail.premise or detail.preview,
        next_chapter=snapshot.chapter_number,
        total_chapters=snapshot.total_chapters,
        chapters=[
            EngineChapterRailItemView(
                number=item.chapter_number,
                title=item.title or f"第 {item.chapter_number} 章",
                state=_chapter_rail_state(item.status),
                detail=_chapter_rail_detail(item.status_label, item.word_count),
            )
            for item in snapshot.chapters
        ],
        plan_title=snapshot.current_title,
        plan_summary=snapshot.current_outline_summary,
        previous_summary=snapshot.previous_summary,
        previous_exit_summary=snapshot.previous_exit_summary,
        current_goal=snapshot.current_goal,
        current_outline_summary=snapshot.current_outline_summary,
        next_goal=snapshot.next_goal,
        suggestion=snapshot.suggestions_for_next_chapter,
        memories=[
            EngineChapterMemoryView(
                label=f"延续约束 {index}",
                value=item,
                detail="上一章出口状态",
            )
            for index, item in enumerate(snapshot.carry_forward, start=1)
        ],
        memory_tabs=_chapter_memory_tabs(snapshot),
        activity=_chapter_activity(snapshot, jobs),
        autorun=project_book_autorun_view(autorun_state),
        task_error_log=_novel_error_log(detail.project_id, jobs, task_error_entries),
    )


def _chapter_rail_state(
    status: str,
) -> Literal["completed", "current", "pending", "needs_decision"]:
    state = {
        "done": "completed",
        "needs_decision": "needs_decision",
        "current": "current",
        "rewriting": "current",
        "stale": "current",
    }.get(status, "pending")
    return cast(Literal["completed", "current", "pending", "needs_decision"], state)


def _chapter_rail_detail(status_label: str, word_count: int) -> str:
    parts = [status_label.strip()]
    if word_count > 0:
        parts.append(f"{word_count} 字")
    return " · ".join(part for part in parts if part)


def _chapter_memory_tabs(snapshot: ChapterWorkspaceSnapshot) -> list[EngineChapterMemoryTabView]:
    score_cards = [
        EngineChapterMemoryCardView(
            id="overall_score",
            label="综合质量",
            content=_score_text(snapshot.overall_score),
            tone="highlight",
        ),
        EngineChapterMemoryCardView(
            id="continuity_score",
            label="连贯性",
            content=_score_text(snapshot.continuity_score),
        ),
        EngineChapterMemoryCardView(
            id="causal_score",
            label="因果链",
            content=_score_text(snapshot.causal_score),
        ),
    ]
    issue_cards: list[EngineChapterMemoryCardView] = []
    for prefix, issues in (
        ("continuity", snapshot.continuity_issues),
        ("causal", snapshot.causal_issues),
    ):
        for index, issue in enumerate(issues[:20], start=1):
            summary = str(
                issue.get("summary") or issue.get("description") or issue.get("message") or issue
            ).strip()
            issue_cards.append(
                EngineChapterMemoryCardView(
                    id=f"{prefix}_{index}",
                    label="连贯性问题" if prefix == "continuity" else "因果链问题",
                    content=summary[:800],
                    tone="warning",
                )
            )
    warning_cards = [
        EngineChapterMemoryCardView(
            id=f"warning_{index}",
            label="注意事项",
            content=warning[:800],
            tone="warning",
        )
        for index, warning in enumerate(snapshot.warnings[:20], start=1)
    ]
    control_cards = [
        EngineChapterMemoryCardView(
            id=f"carry_{index}",
            label="必须延续",
            content=value[:800],
            tone="highlight",
        )
        for index, value in enumerate(snapshot.carry_forward[:20], start=1)
    ]
    return [
        EngineChapterMemoryTabView(id="overview", label="总览", cards=score_cards),
        EngineChapterMemoryTabView(id="motifs", label="母题"),
        EngineChapterMemoryTabView(id="relationships", label="关系"),
        EngineChapterMemoryTabView(id="issues", label="问题", cards=issue_cards),
        EngineChapterMemoryTabView(
            id="reading_power",
            label="阅读力",
            cards=[
                EngineChapterMemoryCardView(
                    id="reading_power_score",
                    label="阅读力评分",
                    content=_score_text(snapshot.reading_power_score),
                )
            ],
        ),
        EngineChapterMemoryTabView(id="guardrails", label="护栏", cards=warning_cards),
        EngineChapterMemoryTabView(id="control", label="控制", cards=control_cards),
    ]


def _score_text(value: float | None) -> str:
    return "暂无评分" if value is None else f"{value:.1f}"


def _chapter_activity(
    snapshot: ChapterWorkspaceSnapshot,
    jobs: list[JobRecord],
) -> EngineChapterActivityView:
    checkpoint = _checkpoint_view(snapshot.pending_checkpoint)
    chapter_kinds = {
        "run_chapter",
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "polish_chapter",
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
    }
    chapter_jobs = [
        record
        for record in jobs
        if record.project_id == snapshot.project_id
        and _enum_value(record.kind) in chapter_kinds
        and _job_matches_chapter(record, snapshot.chapter_number)
    ]
    candidates = [
        record
        for record in chapter_jobs
        if _enum_value(record.status) in {"queued", "running", "paused"}
    ]
    record: JobRecord | None
    if candidates:
        record = max(candidates, key=lambda item: item.updated_at)
    elif checkpoint is not None and snapshot.pending_checkpoint is not None:
        # Reuse the originating run when available, so a pending decision has
        # exactly the same stage sequence/percentage as its task card.
        checkpoint_jobs = [
            record
            for record in chapter_jobs
            if (
                isinstance(record.result.get("checkpoint"), dict)
                and record.result["checkpoint"].get("checkpoint_id") == checkpoint.id
            )
        ]
        record = (
            max(checkpoint_jobs, key=lambda item: item.updated_at)
            if checkpoint_jobs
            else JobRecord(
                job_id="",
                kind="prepare_chapter"
                if snapshot.pending_checkpoint.checkpoint_type == "plan_checkpoint"
                else "resolve_chapter_checkpoint",
                label="章节等待决策",
                project_id=snapshot.project_id,
                status=JobState.PAUSED,
                current_step=snapshot.pending_checkpoint.checkpoint_type,
                result={
                    "status": "needs_decision",
                    "checkpoint": snapshot.pending_checkpoint.model_dump(mode="json"),
                },
            )
        )
    else:
        record = None
    history = _chapter_activity_history(chapter_jobs, current_task_id=record.job_id if record else "")
    chapter_flow = _chapter_flow_projection(record or _chapter_flow_anchor(chapter_jobs))
    if record is None:
        return EngineChapterActivityView(chapter_flow=chapter_flow, history=history)
    job_view = project_job_view(record)
    workflow = project_workflow_run(record)
    waiting = record.result.get("status") == "needs_decision" or job_view.state == "paused"
    return EngineChapterActivityView(
        kind=_enum_value(record.kind),
        task_id=record.job_id,
        state="checkpoint" if waiting else "running",
        task_label=job_view.label,
        current_step_label=str(workflow["current_stage_label"]),
        operation_detail="" if waiting else project_running_operation_detail(record),
        progress_percent=int(workflow["progress_percent"]),
        checkpoint=checkpoint if waiting else None,
        stages=workflow["stages"],
        run_insights=workflow["run_insights"],
        efficiency=workflow["efficiency"],
        chapter_flow=chapter_flow,
        history=history,
    )


_CHAPTER_FLOW_KINDS = {
    "run_chapter",
    "prepare_chapter",
    "resolve_chapter_checkpoint",
    "resolve_chapter_checkpoint_finalize",
}
_CHAPTER_FLOW_DEPTH = {
    "prepare_chapter": 1,
    "resolve_chapter_checkpoint": 2,
    "run_chapter": 3,
    "resolve_chapter_checkpoint_finalize": 3,
}
_CHAPTER_SEGMENT_TO_FLOW_STEP = {
    "plan_checkpoint": "plan",
    "pre_alignment": "alignment",
    "post_alignment": "polish",
    "guard_checkpoint": "humanize",
    "post_guard_repair": "humanize",
    "polish_reextract_canon": "extract_canon",
    "evaluate": "persist",
    "volume_audit": "persist",
}


def _chapter_flow_anchor(chapter_jobs: list[JobRecord]) -> JobRecord | None:
    """Choose the deepest durable core run for the chapter-level journey.

    A later accidental prepare attempt must not hide an already archived
    chapter.  Depth therefore outranks recency, while recency still selects
    the latest retry within the same workflow segment.
    """

    core_jobs = [
        record for record in chapter_jobs if _enum_value(record.kind) in _CHAPTER_FLOW_KINDS
    ]
    if not core_jobs:
        return None
    return max(
        core_jobs,
        key=lambda record: (
            _CHAPTER_FLOW_DEPTH.get(_enum_value(record.kind), 0),
            _enum_value(record.status) == "succeeded",
            record.updated_at,
        ),
    )


def _chapter_flow_projection(record: JobRecord | None) -> EngineChapterActivityRunView | None:
    """Project one checkpoint/continuation run onto the canonical six phases.

    The checkpoint job kinds are transport segments of the same chapter
    pipeline.  Replaying their durable cursor as ``run_chapter`` restores the
    earlier milestones implied by that cursor and makes their artifacts
    inspectable without altering the raw per-attempt history.
    """

    if record is None or _enum_value(record.kind) not in _CHAPTER_FLOW_KINDS:
        return None
    projected_record = record.model_copy(update={"kind": "run_chapter"}, deep=True)
    projected_record.current_step = _CHAPTER_SEGMENT_TO_FLOW_STEP.get(
        projected_record.current_step,
        projected_record.current_step,
    )
    projected_record.events = [
        event.model_copy(
            update={
                "step": _CHAPTER_SEGMENT_TO_FLOW_STEP.get(event.step, event.step),
            },
            deep=True,
        )
        for event in projected_record.events
    ]
    workflow = project_workflow_run(projected_record)
    job_view = project_job_view(record)
    return EngineChapterActivityRunView(
        kind="run_chapter",
        task_id=f"chapter-flow:{record.job_id}",
        status=cast(
            Literal["queued", "running", "paused", "succeeded", "failed"],
            _enum_value(record.status),
        ),
        task_label=f"{job_view.label} · 本章全流程",
        current_step_label=str(workflow["current_stage_label"]),
        progress_percent=int(workflow["progress_percent"]),
        updated_at=record.updated_at,
        stages=workflow["stages"],
    )


def _chapter_activity_history(
    chapter_jobs: list[JobRecord],
    *,
    current_task_id: str,
) -> list[EngineChapterActivityRunView]:
    """Project prior same-chapter tasks like PySide's historical job cards."""

    history: list[EngineChapterActivityRunView] = []
    for record in sorted(chapter_jobs, key=lambda item: item.updated_at, reverse=True):
        if record.job_id == current_task_id:
            continue
        workflow = project_workflow_run(record)
        stages = workflow["stages"]
        if not stages or not any(
            stage.get("state") in {"completed", "failed", "skipped", "blocked", "rolled_back"}
            for stage in stages
        ):
            continue
        job_view = project_job_view(record)
        history.append(
            EngineChapterActivityRunView(
                kind=_enum_value(record.kind),
                task_id=record.job_id,
                status=cast(
                    Literal["queued", "running", "paused", "succeeded", "failed"],
                    _enum_value(record.status),
                ),
                task_label=job_view.label,
                current_step_label=str(workflow["current_stage_label"]),
                progress_percent=int(workflow["progress_percent"]),
                updated_at=record.updated_at,
                stages=stages,
            )
        )
        if len(history) >= 8:
            break
    return history


def _checkpoint_view(checkpoint: DecisionCheckpoint | None) -> EngineCheckpointView | None:
    if checkpoint is None:
        return None
    return EngineCheckpointView(
        id=checkpoint.checkpoint_id,
        title="章节方案确认" if checkpoint.checkpoint_type == "plan_checkpoint" else "章节归档确认",
        summary=checkpoint.summary,
        prompt=checkpoint.prompt,
        options=[
            EngineCheckpointOptionView(
                id=option.option_id,
                label=option.label,
                description=option.description,
                recommended=option.is_recommended,
            )
            for option in checkpoint.options
        ],
    )


def project_book_autorun_view(state: BookAutorunState | None) -> EngineBookAutorunView:
    if state is None:
        return EngineBookAutorunView()
    last_failure_kind = state.last_failure_kind
    recovery_target = state.recovery_target
    # Compatibility for sessions persisted before structured failure ownership
    # was added. Classification stays at the Engine boundary; UI clients never
    # infer domain semantics from localized exception prose.
    if not last_failure_kind:
        legacy_source_marker = ""
        if "审查上游的" in state.last_error and "大纲=[" in state.last_error:
            legacy_source_marker = state.last_error.split("大纲=[", 1)[1].split("]", 1)[0]
        if "upstream_source_conflict" in state.last_error or "," in legacy_source_marker:
            last_failure_kind = "upstream_source_conflict"
            recovery_target = "manual"
    checkpoint: DecisionCheckpoint | None = None
    if state.checkpoint_payload:
        try:
            checkpoint = DecisionCheckpoint.model_validate(state.checkpoint_payload)
        except ValueError:
            checkpoint = None
    checkpoint_attempts = (
        state.checkpoint_attempts.get(state.checkpoint_id, 0) if state.checkpoint_id else 0
    )
    return EngineBookAutorunView(
        status=state.status.value,
        phase=state.phase.value,
        mode=state.mode,
        start_chapter=state.start_chapter,
        current_chapter=state.current_chapter,
        end_chapter=state.end_chapter,
        total_chapters=state.total_chapters,
        completed_chapters=list(state.completed_chapters),
        active_task_id=state.active_job_id,
        checkpoint=_checkpoint_view(checkpoint),
        checkpoint_attempts=checkpoint_attempts,
        checkpoint_budget=state.checkpoint_budget,
        total_failures=state.total_failures,
        failure_budget=state.failure_budget,
        next_retry_at=state.next_retry_at,
        wait_reason=state.wait_reason,
        last_error=state.last_error,
        last_failure_kind=last_failure_kind,
        recovery_target=recovery_target,
        updated_at=state.updated_at,
    )


def _job_matches_chapter(record: JobRecord, chapter_number: int) -> bool:
    observed: set[int] = set()
    for payload in (
        record.current_step_payload,
        record.result,
        *(event.payload for event in record.events[-20:]),
    ):
        for key in ("chapter_number", "chapter"):
            value = _optional_int(payload.get(key))
            if value is not None and value > 0:
                observed.add(value)
    return not observed or chapter_number in observed


def _novel_error_log(
    project_id: str,
    jobs: list[JobRecord],
    persisted_entries: list[dict[str, Any]] | None = None,
) -> list[EngineTaskErrorLogEntryView]:
    """Project the same durable diagnostics used by the workflow surface.

    The fallback maintains compatibility for direct callers that only provide
    job records.  Engine queries pass the durable index, whose stable IDs and
    acknowledgement metadata make confirm/reopen/cleanup work identically in
    the chapter and workflow surfaces.
    """

    if persisted_entries is not None:
        return [
            EngineTaskErrorLogEntryView(
                id=str(entry.get("id") or ""),
                time_label=str(entry.get("time") or ""),
                job_label=str(entry.get("job_label") or ""),
                task_id=str(entry.get("job_id") or ""),
                task_label=str(entry.get("task") or ""),
                attempt_label=str(entry.get("attempt") or ""),
                error_message=str(entry.get("error") or "任务失败，但未收到异常摘要。"),
                excerpt=str(entry.get("excerpt") or "")[:1600],
                log_path=str(entry.get("log_path") or "")[:2000],
                kind_label=str(entry.get("kind") or "任务失败"),
                auto_resolved=(
                    entry.get("auto_resolved") is True
                    or str(entry.get("auto_resolved") or "").casefold() == "true"
                ),
                acknowledged_at=str(entry.get("acknowledged_at") or ""),
                cause_code=str(entry.get("cause_code") or ""),
                auto_repair_state=str(entry.get("auto_repair_state") or "unknown"),
                auto_repair_explanation=str(entry.get("auto_repair_explanation") or "")[:600],
                recommended_action=str(entry.get("recommended_action") or "")[:400],
                recovery_action_kinds=[
                    str(item)
                    for item in (entry.get("recovery_action_kinds") or [])
                    if str(item).strip()
                ][:12],
            )
            for entry in persisted_entries[:50]
            if str(entry.get("id") or "").strip()
        ]

    entries: list[EngineTaskErrorLogEntryView] = []
    for record in sorted(jobs, key=lambda item: item.updated_at, reverse=True):
        if (
            record.project_id != project_id
            or _enum_value(record.kind) not in NOVEL_DURABLE_JOB_KINDS
            or _enum_value(record.status) != "failed"
        ):
            continue
        error = project_job_view(record).error
        if error is None:
            continue
        entries.append(
            EngineTaskErrorLogEntryView(
                id=record.job_id,
                time_label=record.updated_at,
                job_label=record.label,
                task_id=error.failed_step,
                task_label=error.failed_step or record.current_step,
                error_message=error.message,
                excerpt=record.error[:800],
                kind_label=error.category,
                cause_code="not_auto_repairable",
                auto_repair_state="not_applicable",
                auto_repair_explanation="该错误不属于可安全自动改文的已登记场景。",
                recommended_action="查看任务日志和 Engine 提供的恢复操作。",
            )
        )
        if len(entries) >= 50:
            break
    return entries


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "NOVEL_DURABLE_JOB_KINDS",
    "EngineChapterActivityView",
    "EngineChapterActivityRunView",
    "EngineChapterMemoryCardView",
    "EngineChapterMemoryTabView",
    "EngineChapterMemoryView",
    "EngineChapterRailItemView",
    "EngineCheckpointOptionView",
    "EngineCheckpointView",
    "EngineNovelStudioView",
    "EngineTaskErrorLogEntryView",
    "project_novel_studio_view",
]
