"""One event history must produce one cursor and percentage on every surface."""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.app_service.contracts import JobRecord, JobState, JobStepEvent
from novel_forge.app_service.engine_novel import _chapter_activity
from novel_forge.app_service.engine_views import project_job_view, project_task_stream_view
from novel_forge.app_service.workflow_projection import (
    project_workflow_progress,
    project_workflow_run,
    project_workflow_stages,
)
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot, DecisionCheckpoint


def job(kind: str, *events: str | tuple[str, dict[str, Any]], **kwargs: Any) -> JobRecord:
    history = [
        JobStepEvent(
            step=item if isinstance(item, str) else item[0],
            payload={} if isinstance(item, str) else item[1],
        )
        for item in events
    ]
    return JobRecord(
        kind=kind,
        label="进度回归",
        project_id="progress-test",
        status=kwargs.pop("status", JobState.RUNNING),
        events=history,
        current_step=history[-1].step if history else "",
        current_step_payload=history[-1].payload if history else {},
        **kwargs,
    )


def states(record: JobRecord) -> dict[str, str]:
    return {stage["id"]: stage["state"] for stage in project_workflow_stages(record)}


def assert_consistent(record: JobRecord) -> int:
    run = project_workflow_run(record)
    assert project_job_view(record).progress_percent == run["progress_percent"]
    assert project_task_stream_view(record).progress_percent == run["progress_percent"]
    return run["progress_percent"]


@pytest.mark.parametrize(
    "kind,step",
    [
        ("init_long", "plan_blueprint_elements_starting"),
        ("init_long", "plan_blueprint_subplots"),
        ("run_chapter", "wave"),
        ("run_chapter", "extract_canon_start"),
        ("resolve_chapter_checkpoint", "pre_alignment"),
        ("resolve_chapter_checkpoint", "post_alignment"),
        ("resolve_chapter_checkpoint_finalize", "persist"),
    ],
)
@pytest.mark.parametrize("status", [JobState.RUNNING, JobState.FAILED, JobState.PAUSED])
def test_surfaces_share_progress(kind: str, step: str, status: JobState) -> None:
    assert 0 < assert_consistent(job(kind, step, status=status)) < 100


def test_parallel_initialization_preserves_both_branches() -> None:
    running = job(
        "init_long",
        "spec",
        "init_web_research",
        "init_story_bible_starting",
        "plan_blueprint_elements_starting",
    )
    assert states(running)["init_story_bible"] == "active"
    assert states(running)["plan_blueprint_elements"] == "active"
    assert states(running)["plan_blueprint"] == "pending"
    assert assert_consistent(running) == 20
    one_done = job(
        "init_long", *(event.step for event in running.events), "plan_blueprint_elements"
    )
    assert states(one_done)["init_story_bible"] == "active"
    assert states(one_done)["plan_blueprint_elements"] == "completed"
    assert assert_consistent(one_done) == 23
    assert project_workflow_run(one_done)["step_label"] == "世界观设定"
    assert project_job_view(one_done).step_label == "世界观设定"
    assert project_task_stream_view(one_done).step_label == "世界观设定"


@pytest.mark.parametrize("artifact", ["character_bible", "character_system", "entity_graph"])
def test_health_observation_does_not_rewind_initialization(artifact: str) -> None:
    record = job(
        "init_long",
        "init_character_bible",
        "profile_style",
        "init_entity_graph",
        "init_knowledge_boundaries",
        ("init_upstream_health", {"artifact": artifact, "status": "pass"}),
    )
    view = project_workflow_run(record)
    assert view["stage_id"] == "profile_style"
    assert states(record)["profile_style"] == "active"
    assert assert_consistent(record) == 37


@pytest.mark.parametrize("outcome", ["skipped", "failed"])
def test_non_blocking_research_outcome_is_not_completed(outcome: str) -> None:
    record = job("init_long", f"init_web_research_{outcome}", "init_character_bible_starting")
    assert states(record)["init_web_research"] == outcome
    assert project_workflow_run(record)["stage_id"] == "init_character_bible"
    assert_consistent(record)


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("draft_done", "alignment"),
        ("quality_done", "causal_repair"),
        ("causal_repair_done", "reading_power_repair"),
        ("repair_done", "polish"),
        ("canon_done", "persist"),
        ("refinement_done", "persist"),
        ("final_verify_done", "persist"),
    ],
)
@pytest.mark.parametrize("kind", ["run_chapter", "resolve_chapter_checkpoint"])
def test_all_review_resume_stages_keep_their_frontier(stage: str, expected: str, kind: str) -> None:
    record = job(kind, ("resume_from_progress", {"completed_stage": stage}), "state_packet")
    if kind == "resolve_chapter_checkpoint":
        expected = (
            "pre_alignment"
            if stage == "draft_done"
            else "guard_checkpoint"
            if expected == "persist"
            else "post_alignment"
        )
    assert states(record)[expected] == "active"
    assert assert_consistent(record) > 30


def test_optional_chapter_steps_are_skipped_not_fabricated_as_executed() -> None:
    record = job(
        "run_chapter",
        "state_packet",
        "bridge",
        "plan",
        "draft",
        "wave",
        "alignment",
        "extract_canon_start",
    )
    projected = states(record)
    for key in (
        "chapter_research",
        "opening_guard",
        "continuity_repair",
        "alignment_repair",
        "causal_repair",
        "reading_power_repair",
        "polish",
        "humanize",
    ):
        assert projected[key] == "skipped", key
    assert projected["extract_canon"] == "active"
    assert_consistent(record)


def test_humanize_rollback_survives_report_events_but_not_a_retry() -> None:
    record = job(
        "run_chapter",
        "humanize_start",
        "humanize_rolled_back",
        "humanize_scan",
        "humanize_revision_diff",
    )
    assert states(record)["humanize"] == "rolled_back"
    retry = job("run_chapter", *(event.step for event in record.events), "humanize_start")
    assert states(retry)["humanize"] == "active"


def test_continuity_score_gate_no_op_is_not_a_completed_model_repair() -> None:
    record = job(
        "run_chapter",
        "continuity",
        (
            "continuity_repair",
            {
                "applied": False,
                "repair_plan": {"no_op": True},
                "failure_reason": "continuity_score 10.0 == 10.0 (perfect), skipping repair unconditionally",
            },
        ),
        "extract_canon_start",
    )
    assert states(record)["continuity_repair"] == "skipped"
    assert_consistent(record)


def test_input_validation_uses_its_actual_phase() -> None:
    record = job("run_chapter", "polish", ("input_integrity_check", {"stage": "extract_input"}))
    assert states(record)["extract_canon"] == "active"
    assert_consistent(record)


def test_queued_and_finished_progress() -> None:
    queued = job("init_long", status=JobState.QUEUED)
    assert set(states(queued).values()) == {"pending"}
    assert assert_consistent(queued) == 0
    completed = job("init_long", "canon_state", status=JobState.SUCCEEDED)
    assert assert_consistent(completed) == 100


@pytest.mark.parametrize(
    "checkpoint_type,kind",
    [("plan_checkpoint", "prepare_chapter"), ("guard_checkpoint", "resolve_chapter_checkpoint")],
)
def test_waiting_decision_keeps_stages_after_job_completion(
    checkpoint_type: str, kind: str
) -> None:
    checkpoint = DecisionCheckpoint(checkpoint_id="checkpoint-1", checkpoint_type=checkpoint_type)
    snapshot = ChapterWorkspaceSnapshot(
        project_id="progress-test",
        project_title="回归",
        chapter_number=1,
        pending_checkpoint=checkpoint,
    )
    record = job(
        kind,
        checkpoint_type,
        status=JobState.SUCCEEDED,
        result={"status": "needs_decision", "checkpoint": checkpoint.model_dump()},
    )
    activity = _chapter_activity(snapshot, [record])
    assert activity.state == "checkpoint"
    assert activity.kind == kind
    assert activity.task_id == record.job_id
    assert activity.progress_percent == assert_consistent(record) < 100
    assert any(
        stage["id"] == checkpoint_type and stage["state"] == "blocked" for stage in activity.stages
    )
    without_history = _chapter_activity(snapshot, [])
    assert without_history.stages == activity.stages


def test_initialization_does_not_impersonate_a_chapter_task() -> None:
    snapshot = ChapterWorkspaceSnapshot(
        project_id="progress-test", project_title="回归", chapter_number=1
    )
    assert _chapter_activity(snapshot, [job("init_long", "plan_outline")]).state == "idle"


def test_chapter_activity_keeps_prior_checkpoint_runs_for_inspection() -> None:
    snapshot = ChapterWorkspaceSnapshot(
        project_id="progress-test", project_title="回归", chapter_number=1
    )
    prepared = job(
        "prepare_chapter",
        "state_packet",
        "bridge",
        "plan",
        "plan_checkpoint",
        job_id="prepare-history",
        status=JobState.PAUSED,
        updated_at="2026-08-30T14:18:00+00:00",
    )
    finalizing = job(
        "resolve_chapter_checkpoint_finalize",
        "post_guard_repair",
        job_id="finalize-current",
        status=JobState.RUNNING,
        updated_at="2026-08-30T17:53:00+00:00",
    )

    activity = _chapter_activity(snapshot, [prepared, finalizing])

    assert activity.task_id == "finalize-current"
    assert activity.chapter_flow is not None
    assert activity.chapter_flow.kind == "run_chapter"
    assert len(activity.chapter_flow.stages) == 18
    assert activity.model_dump(mode="json", by_alias=True)["chapterFlow"]["kind"] == "run_chapter"
    assert any(
        stage["id"] == "state_packet" and stage["state"] == "completed"
        for stage in activity.chapter_flow.stages
    )
    assert any(
        stage["id"] == "persist" and stage["state"] in {"active", "pending"}
        for stage in activity.chapter_flow.stages
    )
    assert [run.task_id for run in activity.history] == ["prepare-history"]
    historical = activity.history[0]
    assert historical.kind == "prepare_chapter"
    assert historical.status == "paused"
    assert historical.progress_percent is not None
    assert any(stage["state"] == "completed" for stage in historical.stages)


def test_progress_counts_settled_parallel_work_not_the_farthest_index() -> None:
    record = job("init_long", "plan_blueprint_elements_starting")
    stages = [
        {"id": "a", "state": "active"},
        {"id": "b", "state": "completed"},
        {"id": "c", "state": "pending"},
    ]
    assert project_workflow_progress(record, stages) == 50
