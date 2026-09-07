"""Tests for desktop-specific progress smoothing."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
    _compact_payload,
    _infer_failed_step,
)
from novel_forge.desktop.pages.chapter_studio.jobs import AuditState, ChapterStudioJobsMixin
from novel_forge.desktop.pages.workflow.artifacts import step_has_artifacts
from novel_forge.desktop.pages.workflow.jobs import (
    _active_display_step_name_for_job,
    _compact_step_summary,
    _compute_card_progress,
    _indicator_current_step_key,
    _indicator_state_for_job,
    _init_long_blueprint_to_outline_completed_key,
    _latest_visible_context_for_raw_step,
    _visible_steps_for_kind,
)
from novel_forge.desktop.progress import (
    compute_job_progress,
    compute_task_flow_progress,
    display_step_name_for_job,
)
from novel_forge.pipeline.progress import (
    compute_progress_percent,
    display_step_name,
    is_non_progress_step_event,
    resolve_step_key,
    summary_steps,
)


def _make_job(
    *,
    step: str,
    payload: dict[str, object] | None = None,
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id="job-1",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step=step,
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step=step,
                payload=payload or {},
            )
        ],
    )


def test_init_model_observation_events_do_not_advance_card_progress() -> None:
    job = DesktopJobRecord(
        job_id="job-observe",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="",
        events=[
            DesktopJobEvent(
                at="2026-06-19T10:00:00+00:00",
                step="run_log_started",
                payload={"run_log_dir": "data/demo/logs/run"},
            ),
            DesktopJobEvent(
                at="2026-06-19T10:00:01+00:00",
                step="model_call_update",
                payload={"status": "running", "task": "init_story_bible"},
            ),
        ],
    )

    assert compute_job_progress(job) == 3
    assert _compute_card_progress(job) == 0


def test_init_outline_stage_survives_event_window_eviction() -> None:
    """The active stage payload must outlive the bounded diagnostic event window."""
    job = DesktopJobRecord(
        job_id="job-outline-window",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        current_step_payload={
            "stage": "outline_inheritance",
            "artifact": "outline",
            "batch": 1,
            "batch_total": 1,
        },
        events=[
            DesktopJobEvent(
                at=f"t{index}",
                step="preflight_token_estimate",
                payload={"task": "adjudicate_entity_references"},
            )
            for index in range(160)
        ],
    )
    steps = _visible_steps_for_kind("init_long")
    visible_keys = {step.key for step in steps}

    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert compute_job_progress(job) == 93
    assert _compute_card_progress(job) == 63

    state = _indicator_state_for_job(job, steps)
    active_indexes = [
        index for index, dot_state in enumerate(state.dot_states) if dot_state == "active"
    ]
    assert active_indexes == [9]


@pytest.mark.parametrize(
    "internal_step",
    [
        "llm_stream_delta_summary",
        "claim_semantic_repair_requested",
        "claim_semantic_repair_retry",
        "claim_semantic_repair_succeeded",
        "claim_semantic_repair_failed",
    ],
)
def test_init_internal_claim_events_do_not_replace_semantic_progress(
    internal_step: str,
) -> None:
    job = DesktopJobRecord(
        job_id=f"job-{internal_step}",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step=internal_step,
        events=[
            DesktopJobEvent(
                at="t1",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "outline_inheritance",
                    "artifact": "outline",
                    "batch": 1,
                    "batch_total": 1,
                },
            ),
            DesktopJobEvent(at="t2", step=internal_step, payload={}),
        ],
    )

    assert compute_job_progress(job) == 93
    assert _compute_card_progress(job) == 63


def test_init_claim_cache_event_uses_its_coherence_stage_for_task_flow() -> None:
    job = DesktopJobRecord(
        job_id="job-claim-cache",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="init_claim_entity_adjudication_cache_hit",
        current_step_payload={"stage": "outline_inheritance"},
        events=[
            DesktopJobEvent(
                at="t1",
                step="init_claim_entity_adjudication_cache_hit",
                payload={"stage": "outline_inheritance", "cache_hits": 3},
            )
        ],
    )
    steps = _visible_steps_for_kind("init_long")
    visible_keys = {step.key for step in steps}

    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert _compute_card_progress(job) > 0


def test_task_flow_progress_matches_visible_checkpoint_step_position() -> None:
    job = DesktopJobRecord(
        job_id="job-status-alignment",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 1 章",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        events=[
            DesktopJobEvent(at="t1", step="plan_checkpoint"),
            DesktopJobEvent(at="t2", step="draft"),
        ],
    )

    assert compute_task_flow_progress(job) == _compute_card_progress(job) == 21


@pytest.mark.parametrize(
    "kind",
    [
        "init_long",
        "run_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "prepare_chapter",
        "book_consistency",
        "export_book",
        "run_short",
    ],
)
def test_task_flow_progress_matches_every_visible_card_step(kind: str) -> None:
    for step in summary_steps(kind):
        job = DesktopJobRecord(
            job_id=f"{kind}-{step.key}",
            kind=kind,
            label="任务",
            status=DesktopJobState.RUNNING,
            current_step=step.key,
        )

        assert compute_task_flow_progress(job) == _compute_card_progress(job)


def test_prompt_pressure_does_not_replace_progress_step() -> None:
    base = DesktopJobRecord(
        job_id="job-base",
        kind="run_chapter",
        label="章节任务",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        events=[
            DesktopJobEvent(at="2026-06-19T10:00:00+00:00", step="draft", payload={}),
        ],
    )
    diagnostic = DesktopJobRecord(
        job_id="job-diagnostic",
        kind="run_chapter",
        label="章节任务",
        status=DesktopJobState.RUNNING,
        current_step="prompt_pressure",
        events=[
            DesktopJobEvent(at="2026-06-19T10:00:00+00:00", step="draft", payload={}),
            DesktopJobEvent(
                at="2026-06-19T10:00:01+00:00",
                step="prompt_pressure",
                payload={"task": "DRAFT_CHAPTER", "token_pressure": 0.82},
            ),
        ],
    )

    assert compute_job_progress(diagnostic) == compute_job_progress(base)


def test_compute_job_progress_smooths_init_long_outline_batches() -> None:
    starting = compute_job_progress(
        _make_job(
            step="plan_outline_starting",
            payload={"chapters_done": 0, "total_chapters": 36},
        )
    )
    early = compute_job_progress(
        _make_job(
            step="plan_outline_batch_1_5",
            payload={"batch_start": 1, "batch_end": 5, "chapters_done": 5, "chapters_total": 36},
        )
    )
    middle = compute_job_progress(
        _make_job(
            step="plan_outline_continue_6_18",
            payload={"batch_start": 6, "batch_end": 18, "chapters_done": 18, "chapters_total": 36},
        )
    )
    final_batch = compute_job_progress(
        _make_job(
            step="plan_outline_batch_31_36",
            payload={"batch_start": 31, "batch_end": 36, "chapters_done": 36, "chapters_total": 36},
        )
    )

    assert starting == 82
    assert 82 < early < 92
    assert starting < early
    assert early < middle < 92
    assert middle <= final_batch <= 91


def test_init_long_web_research_is_visible_between_spec_and_story_bible() -> None:
    steps = _visible_steps_for_kind("init_long")
    keys = [step.key for step in steps]

    assert keys.index("spec") < keys.index("init_web_research")
    assert keys.index("init_web_research") < keys.index("init_story_bible")
    assert resolve_step_key("init_long", "init_web_research_start") == "init_web_research"
    assert resolve_step_key("init_long", "init_web_research_failed") == "init_web_research"
    assert resolve_step_key("init_long", "init_research_dossier") == "init_web_research"
    assert resolve_step_key("init_long", "outline_research_grounding") == "init_web_research"
    assert compute_progress_percent("init_long", "running", "init_web_research") == 12
    assert compute_progress_percent("init_long", "running", "outline_research_grounding") == 93


def test_init_long_web_research_artifact_is_discoverable(tmp_path) -> None:
    report_path = tmp_path / "reports" / "init_web_research.json"
    dossier_path = tmp_path / "reports" / "init_research_dossier.json"
    grounding_path = tmp_path / "reports" / "outline_research_grounding.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"status":"skipped"}', encoding="utf-8")
    dossier_path.write_text('{"status":"skipped"}', encoding="utf-8")
    grounding_path.write_text('{"status":"skipped"}', encoding="utf-8")

    assert step_has_artifacts(
        kind="init_long",
        step_key="init_web_research",
        project_dir=tmp_path,
    )


def test_compute_job_progress_uses_final_outline_milestone_when_complete() -> None:
    job = _make_job(step="plan_outline")

    assert compute_job_progress(job) == 92


def test_outline_reveal_guard_rejection_stays_on_outline_start() -> None:
    job = _make_job(step="plan_outline_cache_rejected")
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert compute_job_progress(job) == 82
    assert display_step_name_for_job(job) == "章节大纲缓存已失效"
    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"


def test_editorial_contract_is_visible_before_outline() -> None:
    steps = _visible_steps_for_kind("init_long")
    keys = [step.key for step in steps]
    visible_keys = set(keys)
    job = _make_job(step="derive_editorial_contract")

    assert keys.index("derive_editorial_contract") < keys.index("plan_outline")
    assert keys.index("plan_chapter_design_matrix") < keys.index("plan_outline")
    assert keys.index("plan_outline") < keys.index("init_narrative_contract")
    assert compute_job_progress(job) == 80
    assert _indicator_current_step_key(job, visible_keys) == "derive_editorial_contract"


def test_outline_reveal_guard_rejection_then_regeneration_advances_task_flow() -> None:
    def make_sequence_job(current_step: str, events: list[DesktopJobEvent]) -> DesktopJobRecord:
        return DesktopJobRecord(
            job_id=f"job-{current_step}",
            kind="init_long",
            label="长篇立项 · demo",
            status=DesktopJobState.RUNNING,
            current_step=current_step,
            events=events,
        )

    events = [
        DesktopJobEvent(
            at="2026-03-13T13:15:00+00:00",
            step="plan_outline_cache_rejected",
            payload={"reason": "reveal_guard_manifest_missing"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:16:00+00:00",
            step="plan_outline_starting",
            payload={"chapters_done": 0, "total_chapters": 36},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:17:00+00:00",
            step="plan_outline_batch_1_5",
            payload={"batch_start": 1, "batch_end": 5, "chapters_done": 5, "chapters_total": 36},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:18:00+00:00",
            step="plan_outline_continue_6_18",
            payload={
                "batch_start": 6,
                "batch_end": 18,
                "chapters_done": 18,
                "chapters_total": 36,
            },
        ),
        DesktopJobEvent(
            at="2026-03-13T13:19:00+00:00",
            step="plan_outline",
            payload={},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:20:00+00:00",
            step="plan_chapter_contracts",
            payload={},
        ),
    ]
    jobs = [
        make_sequence_job("plan_outline_cache_rejected", events[:1]),
        make_sequence_job("plan_outline_batch_1_5", events[:3]),
        make_sequence_job("plan_outline_continue_6_18", events[:4]),
        make_sequence_job("plan_outline", events[:5]),
        make_sequence_job("plan_chapter_contracts", events),
    ]
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}
    raw_progress = [compute_job_progress(job) for job in jobs]
    card_progress = [_compute_card_progress(job) for job in jobs]

    assert raw_progress == sorted(raw_progress)
    assert raw_progress[0] == 82
    assert raw_progress[-2:] == [92, 95]
    assert card_progress[-1] > card_progress[-2]
    assert [_indicator_current_step_key(job, visible_keys) for job in jobs] == [
        "plan_outline",
        "plan_outline",
        "plan_outline",
        "plan_outline",
        "plan_chapter_contracts",
    ]
    assert display_step_name_for_job(jobs[0]) == "章节大纲缓存已失效"
    assert display_step_name_for_job(jobs[2]) == (
        "章节大纲续写（6-18章）  ·  已安全保存到第18章（18/36）"
    )


def test_init_character_bible_starting_advances_visible_task_flow() -> None:
    job = DesktopJobRecord(
        job_id="job-init-character-starting",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="init_character_bible_starting",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="spec",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="plan_blueprint_elements",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="init_story_bible",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:18:00+00:00",
                step="init_character_bible_starting",
                payload={"generation_mode": "split"},
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert compute_job_progress(job) == 38
    assert _indicator_current_step_key(job, visible_keys) == "init_character_bible"
    assert _compact_step_summary(job) == "步骤：角色设定 · 5/15"
    assert display_step_name_for_job(job) == "角色设定"


def test_display_step_name_includes_outline_starting_total_chapters() -> None:
    job = _make_job(
        step="plan_outline_starting",
        payload={"chapters_done": 0, "total_chapters": 36},
    )

    assert display_step_name_for_job(job) == "开始生成章节大纲  ·  尚无安全保存章节（0/36）"


def test_init_long_outline_pending_batch_uses_safe_saved_progress() -> None:
    accepted = DesktopJobEvent(
        at="2026-03-13T13:15:00+00:00",
        step="plan_outline_batch_1_6",
        payload={
            "batch_start": 1,
            "batch_end": 6,
            "safe_chapters_done": 6,
            "safe_saved_chapter": 6,
            "chapters_total": 36,
            "current_batch_status": "accepted",
        },
    )
    pending = DesktopJobEvent(
        at="2026-03-13T13:16:00+00:00",
        step="plan_outline_batch_7_9",
        payload={
            "batch_start": 7,
            "batch_end": 9,
            "safe_chapters_done": 6,
            "safe_saved_chapter": 6,
            "chapters_done": 9,
            "chapters_total": 36,
            "current_batch_status": "pending",
        },
    )
    job = DesktopJobRecord(
        job_id="job-outline-pending",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="plan_outline_batch_7_9",
        events=[accepted, pending],
    )

    assert compute_job_progress(job) == compute_job_progress(
        _make_job(
            step="plan_outline_batch_1_6",
            payload={"safe_chapters_done": 6, "chapters_total": 36},
        )
    )
    assert display_step_name_for_job(job) == (
        "章节大纲分批生成（7-9章）  ·  已安全保存到第6章（6/36）  ·  第7-9章未提交"
    )


def test_display_step_name_includes_init_claims_batch_detail() -> None:
    job = _make_job(
        step="extract_init_coherence_claims",
        payload={
            "artifact": "blueprint",
            "batch": 15,
            "batch_total": 27,
            "claims": 186,
        },
    )

    assert display_step_name_for_job(job) == (
        "初始化一致性 Claims 抽取  ·  15 / 27  ·  当前检查：叙事蓝图  ·  已抽取 186 条"
    )


def test_display_step_name_uses_completed_claim_batches_after_payload_compaction() -> None:
    payload = _compact_payload(
        "extract_init_coherence_claims",
        {
            "stage": "outline_inheritance",
            "artifact": "outline",
            "batch": 6,
            "batch_index": 6,
            "batch_total": 18,
            "batches_done": 4,
            "claims": 35,
            "max_parallel": 4,
        },
    )
    job = _make_job(step="extract_init_coherence_claims", payload=payload)

    assert display_step_name_for_job(job) == (
        "初始化一致性 Claims 抽取  ·  4 / 18  ·  当前检查：章节大纲  ·  已抽取 35 条  ·  并发 4"
    )


def test_display_step_name_marks_stream_claims_as_batch_local() -> None:
    job = _make_job(
        step="extract_init_coherence_claims",
        payload={
            "stage": "outline_inheritance",
            "artifact": "outline",
            "extraction_mode": "stream",
            "batch": 1,
            "batch_total": 1,
            "claims": 3,
            "fallback_claims": 3,
            "max_parallel": 4,
        },
    )

    assert display_step_name_for_job(job) == (
        "初始化一致性 Claims 抽取  ·  1 / 1  ·  当前检查：章节大纲  ·  本批抽取 3 条  ·  "
        "本地兜底 3 条  ·  并发 4"
    )


def test_display_step_name_includes_init_candidate_retrieval_detail() -> None:
    job = _make_job(
        step="retrieve_init_conflict_candidates",
        payload={
            "stage": "outline_inheritance",
            "claims": 137,
            "extracted_claims": 42,
            "active_claims": 137,
            "candidates": 27,
            "degraded_memory": False,
        },
    )

    assert display_step_name_for_job(job) == (
        "初始化冲突候选检索  ·  当前层：章节大纲  ·  活跃一致性 Claims 137 / 抽取 42  ·  候选 27"
    )


def test_display_step_name_includes_init_candidate_adjudication_batch_detail() -> None:
    job = _make_job(
        step="adjudicate_init_conflict_candidates_17_17",
        payload={
            "stage": "outline_inheritance",
            "batch": 17,
            "batch_total": 27,
            "issues": 0,
            "verdict": "accept",
            "max_parallel": 2,
        },
    )

    assert display_step_name_for_job(job) == (
        "冲突候选裁判  ·  当前层：章节大纲  ·  批次 17 / 27  ·  并发 2  ·  问题 0  ·  判定：通过"
    )


def test_display_step_name_includes_init_blueprint_block_detail() -> None:
    job = _make_job(
        step="plan_blueprint_suspense",
        payload={
            "block": "suspense",
            "block_title": "悬念规划",
            "block_index": 6,
            "block_total": 7,
        },
    )

    assert display_step_name_for_job(job) == "叙事蓝图：悬念规划  ·  6 / 7"


def test_init_coherence_after_outline_stays_on_outline_visible_step() -> None:
    job = DesktopJobRecord(
        job_id="job-outline-coherence",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:01+00:00",
                step="plan_outline",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:01+00:00",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "outline_inheritance",
                    "artifact": "outline",
                    "batch": 4,
                    "batch_total": 8,
                    "claims": 72,
                },
            ),
        ],
    )

    assert compute_job_progress(job) >= 92
    assert _compute_card_progress(job) == _compute_card_progress(_make_job(step="plan_outline"))
    assert _compact_step_summary(job) == "步骤：章节大纲 · 10/15"


def test_init_coherence_before_outline_keeps_card_on_blueprint_visible_step() -> None:
    job = DesktopJobRecord(
        job_id="job-blueprint-coherence",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="retrieve_init_conflict_candidates",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:01+00:00",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "blueprint_coherence",
                    "artifact": "blueprint",
                    "batch": 10,
                    "batch_total": 26,
                    "claims": 34,
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:01+00:00",
                step="retrieve_init_conflict_candidates",
                payload={
                    "stage": "blueprint_coherence",
                    "claims": 34,
                    "active_claims": 34,
                    "candidates": 3,
                },
            ),
        ],
    )

    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert compute_job_progress(job) == 80
    assert _indicator_current_step_key(job, visible_keys) == "plan_blueprint"
    assert _init_long_blueprint_to_outline_completed_key(job, visible_keys) == "plan_blueprint"
    assert _compute_card_progress(job) > _compute_card_progress(_make_job(step="plan_blueprint"))
    assert _compute_card_progress(job) < _compute_card_progress(_make_job(step="plan_outline"))
    assert _compact_step_summary(job) == "步骤：叙事蓝图 · 7/15"


def test_legacy_blueprint_claims_without_stage_anchor_to_blueprint_visible_step() -> None:
    job = DesktopJobRecord(
        job_id="job-legacy-blueprint-coherence",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="profile_style",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:01+00:00",
                step="extract_init_coherence_claims",
                payload={
                    "artifact": "blueprint",
                    "batch": 16,
                    "batch_total": 17,
                    "claims": 147,
                },
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_blueprint"
    assert _compact_step_summary(job) == "步骤：叙事蓝图 · 7/15"


def test_unstaged_legacy_claim_never_resets_to_specification_progress() -> None:
    """A compacted old job must not paint a later claim task as 3%/spec."""

    job = DesktopJobRecord(
        job_id="job-legacy-unstaged-claim",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[DesktopJobEvent(at="t1", step="spec", payload={})],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_blueprint"
    assert _compute_card_progress(job) == _compute_card_progress(_make_job(step="plan_blueprint"))
    assert _compute_card_progress(job) > 3


def test_legacy_coherence_claim_uses_nearest_staged_sibling_not_first_step() -> None:
    """Old compacted events still retain their nearby *_start stage marker."""

    job = DesktopJobRecord(
        job_id="job-legacy-staged-outline-claims",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(at="t1", step="spec", payload={}),
            DesktopJobEvent(
                at="t2",
                step="extract_init_coherence_claims_start",
                payload={"stage": "outline_inheritance", "batch_total": 8},
            ),
            DesktopJobEvent(
                at="t3",
                step="extract_init_coherence_claims",
                payload={"artifact": "outline", "batch": 4, "batch_total": 8},
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert _compute_card_progress(job) == _compute_card_progress(_make_job(step="plan_outline"))


def test_hidden_creative_director_resume_anchors_to_blueprint_not_style() -> None:
    job = DesktopJobRecord(
        job_id="job-creative-resume",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="creative_director_packet_resumed",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:01+00:00",
                step="profile_style_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:30+00:00",
                step="init_entity_graph_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="creative_director_packet_resumed",
                payload={},
            ),
        ],
    )

    assert display_step_name_for_job(job) == "创作导演包（已恢复）"
    assert _compact_step_summary(job) == "步骤：叙事蓝图 · 7/15"
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}
    assert _indicator_current_step_key(job, visible_keys) == "plan_blueprint"
    assert _compute_card_progress(job) > _compute_card_progress(_make_job(step="profile_style"))


def test_init_resume_anchor_keeps_card_at_highest_validated_artifact() -> None:
    job = DesktopJobRecord(
        job_id="job-init-anchor",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="init_character_system",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:00+00:00",
                step="init_resume_anchor",
                payload={
                    "step": "profile_style",
                    "artifact": "style_profile",
                    "label": "风格规范",
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:10+00:00",
                step="init_character_system",
                payload={},
            ),
        ],
    )

    assert _compute_card_progress(job) >= _compute_card_progress(_make_job(step="profile_style"))
    assert _compact_step_summary(job) == "步骤：风格与实体 · 6/15"


def test_init_resume_anchor_marks_lower_resumed_steps_as_dependency_recheck() -> None:
    job = DesktopJobRecord(
        job_id="job-init-contract-anchor",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="init_character_bible_resumed",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:00+00:00",
                step="init_resume_anchor",
                payload={
                    "step": "plan_chapter_contracts",
                    "artifact": "chapter_contracts",
                    "label": "章节契约",
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:10+00:00",
                step="spec_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:20+00:00",
                step="init_story_bible_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:30+00:00",
                step="plan_blueprint_elements_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:40+00:00",
                step="init_character_bible_resumed",
                payload={},
            ),
        ],
    )

    assert _compact_step_summary(job) == "步骤：章节契约 · 12/15"
    assert _compute_card_progress(job) >= _compute_card_progress(
        _make_job(step="plan_chapter_contracts")
    )
    assert _active_display_step_name_for_job(job) == "依赖重检 · 角色设定（已恢复）"


def test_init_resume_fallback_preserves_valid_late_stage_progress_floor() -> None:
    job = DesktopJobRecord(
        job_id="job-init-contract-fallback",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="build_init_coherence_profile_start",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:00+00:00",
                step="init_resume_anchor",
                payload={"step": "init_claim_contract_coverage", "label": "契约覆盖修复"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:01+00:00",
                step="init_resume_fallback",
                payload={"reason": "missing_reusable_narrative_contract"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:02+00:00",
                step="init_story_bible_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:03+00:00",
                step="build_init_coherence_profile_start",
                payload={"status": "running"},
            ),
        ],
    )

    assert _compute_card_progress(job) >= _compute_card_progress(
        _make_job(step="init_claim_contract_coverage")
    )
    assert _active_display_step_name_for_job(job).startswith("依赖重检 · ")


def test_desktop_job_manager_retains_resume_anchor_when_event_window_rolls() -> None:
    from novel_forge.desktop.jobs import DesktopJobManager
    from novel_forge.desktop.progress import init_long_resume_anchor_step

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="job-resume-window",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
    )
    try:
        with manager._lock:  # noqa: SLF001
            manager._jobs[record.job_id] = record  # noqa: SLF001

        manager._handle_step(  # noqa: SLF001
            record.job_id,
            "init_resume_anchor",
            {"step": "plan_outline", "artifact": "outline"},
        )
        manager._handle_step(record.job_id, "spec_resumed", {})  # noqa: SLF001
        manager._handle_step(record.job_id, "init_story_bible_resumed", {})  # noqa: SLF001
        for index in range(200):
            manager._handle_step(  # noqa: SLF001
                record.job_id,
                "format_validation_success",
                {"task": "plan_outline", "index": index},
            )

        assert len(record.events) == 160
        assert any(event.step == "init_resume_anchor" for event in record.events)
        assert init_long_resume_anchor_step(record) == "plan_outline"
    finally:
        manager.shutdown(wait_ms=100)


def test_failed_readiness_anchor_prevents_dependency_recheck_progress_regression() -> None:
    job = DesktopJobRecord(
        job_id="job-init-readiness-anchor",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="retrieve_init_conflict_candidates_start",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:00+00:00",
                step="init_resume_anchor",
                payload={
                    "step": "init_readiness",
                    "artifact": "init_readiness",
                    "label": "初始化准入未通过",
                    "blocked": True,
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:14:10+00:00",
                step="retrieve_init_conflict_candidates_start",
                payload={"stage": "outline_inheritance"},
            ),
        ],
    )

    assert _compact_step_summary(job) == "步骤：初始化准入 · 14/15"
    assert _compute_card_progress(job) >= _compute_card_progress(_make_job(step="init_readiness"))
    assert _active_display_step_name_for_job(job).startswith("依赖重检 · ")


def test_legacy_init_coherence_after_outline_uses_recent_visible_context() -> None:
    job = DesktopJobRecord(
        job_id="job-legacy-outline-coherence",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:01+00:00",
                step="plan_outline",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:01+00:00",
                step="extract_init_coherence_claims",
                payload={"artifact": "outline", "batch": 4, "batch_total": 8, "claims": 72},
            ),
        ],
    )

    assert _compute_card_progress(job) > 50
    assert _compact_step_summary(job) == "步骤：章节大纲 · 10/15"


def test_resume_outline_claims_without_outline_event_keeps_semantic_progress() -> None:
    job = _make_job(
        step="extract_init_coherence_claims",
        payload={
            "stage": "outline_inheritance",
            "artifact": "outline",
            "batch": 20,
            "batch_total": 26,
            "claims": 180,
        },
    )

    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert compute_job_progress(job) >= 92
    assert _compute_card_progress(job) == _compute_card_progress(_make_job(step="plan_outline"))
    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert _compact_step_summary(job) == "步骤：章节大纲 · 10/15"


def test_blueprint_coherence_transition_marks_blueprint_completed_between_steps() -> None:
    job = DesktopJobRecord(
        job_id="job-blueprint-transition",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="retrieve_init_conflict_candidates",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:14:00+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="retrieve_init_conflict_candidates",
                payload={"stage": "blueprint_coherence", "claims": 120, "candidates": 20},
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _init_long_blueprint_to_outline_completed_key(job, visible_keys) == "plan_blueprint"
    assert _compute_card_progress(_make_job(step="plan_blueprint")) < _compute_card_progress(job)
    assert _compute_card_progress(job) <= _compute_card_progress(
        _make_job(
            step="plan_outline_starting",
            payload={"chapters_done": 0, "total_chapters": 70},
        )
    )


def test_init_coherence_report_resume_uses_stage_progress_and_visible_step() -> None:
    job = _make_job(
        step="init_coherence_report_resumed",
        payload={"stage": "contract_coherence", "verdict": "accept"},
    )

    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert compute_job_progress(job) == 96
    assert _indicator_current_step_key(job, visible_keys) == "plan_chapter_contracts"


def test_repair_init_artifact_targets_maps_to_repair_init_artifact_patch_progress() -> None:
    """repair_init_artifact_targets should map to repair_init_artifact_patch (80%), not default 50%."""
    job = _make_job(
        step="repair_init_artifact_targets",
        payload={},
    )
    assert compute_progress_percent("init_long", "running", "repair_init_artifact_targets") == 80
    assert compute_job_progress(job) == 80


def test_latest_visible_context_ignores_events_after_raw_step() -> None:
    """_latest_visible_context_for_raw_step should only consider events before raw_step."""
    job = DesktopJobRecord(
        job_id="job-context-order",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="repair_init_artifact_targets",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="derive_editorial_contract",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="plan_chapter_contracts",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:18:00+00:00",
                step="repair_init_artifact_targets",
                payload={},
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    # Should return derive_editorial_contract (the last visible step BEFORE repair_init_artifact_targets),
    # NOT plan_chapter_contracts (which comes after in event order but is a later pipeline phase).
    context = _latest_visible_context_for_raw_step(
        job, "repair_init_artifact_targets", visible_keys
    )
    assert context == "derive_editorial_contract"


def test_repair_init_artifact_targets_step_summary_consistent_with_progress() -> None:
    """Step summary and progress bar should be consistent for repair_init_artifact_targets."""
    job = DesktopJobRecord(
        job_id="job-repair-targets",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="repair_init_artifact_targets",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="derive_editorial_contract",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="repair_init_artifact_targets",
                payload={},
            ),
        ],
    )

    # Progress should be 80% (mapped to repair_init_artifact_patch)
    assert compute_job_progress(job) == 80

    # Step summary should show derive_editorial_contract, not a later step
    summary = _compact_step_summary(job)
    assert "编辑契约" in summary
    # Should NOT show plan_chapter_contracts (章节契约)
    assert "章节契约" not in summary


def test_contract_repair_targets_stay_in_contract_phase_without_visual_regression() -> None:
    job = DesktopJobRecord(
        job_id="job-contract-repair-targets",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="repair_init_artifact_targets",
        current_step_payload={"artifact": "chapter_contracts", "target_count": 3},
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="plan_chapter_contracts",
                payload={"count": 70},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="repair_init_artifact_targets",
                payload={"artifact": "chapter_contracts", "target_count": 3},
            ),
        ],
    )

    assert compute_job_progress(job) == 96
    assert _compute_card_progress(job) == _compute_card_progress(
        _make_job(step="plan_chapter_contracts")
    )
    assert _compact_step_summary(job) == "步骤：章节契约 · 12/15"


def test_contract_reaudit_keeps_late_phase_progress_and_contract_step() -> None:
    job = DesktopJobRecord(
        job_id="job-contract-reaudit",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="init_repair_reaudit_stage",
        current_step_payload={
            "stage": "claim_contract_coverage",
            "status": "running",
            "focus_chapters": [49],
        },
        events=[
            DesktopJobEvent(
                at="2026-07-14T17:44:00+00:00",
                step="init_repair_reaudit_started",
                payload={"artifact": "chapter_contracts", "focus_chapters": [49]},
            ),
            DesktopJobEvent(
                at="2026-07-14T17:44:01+00:00",
                step="init_repair_reaudit_stage",
                payload={"stage": "claim_contract_coverage", "status": "running"},
            ),
        ],
    )

    assert compute_job_progress(job) == 96
    assert _compact_step_summary(job) == "步骤：章节契约 · 12/15"


def test_compute_job_progress_tracks_short_hidden_events() -> None:
    job = DesktopJobRecord(
        job_id="job-short",
        kind="run_short",
        label="短篇创作 · demo",
        status=DesktopJobState.RUNNING,
        current_step="short_profile_style_failed",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="short_profile_style_failed",
                payload={},
            )
        ],
    )

    assert compute_job_progress(job) == 23
    assert display_step_name_for_job(job) == "短篇风格规范生成失败（已跳过）"


def test_compute_job_progress_falls_back_to_normalized_outline_progress_without_payload() -> None:
    job = _make_job(step="plan_outline_batch_1_5")

    assert compute_job_progress(job) == 92


def test_compute_job_progress_does_not_regress_on_outline_retry_steps() -> None:
    job = DesktopJobRecord(
        job_id="job-2",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        # current_step regresses to an early repair batch, but events already reached higher progress.
        current_step="plan_outline_repair_1_5",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="plan_outline_continue_6_18",
                payload={
                    "batch_start": 6,
                    "batch_end": 18,
                    "chapters_done": 18,
                    "chapters_total": 36,
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="plan_outline_repair_1_5",
                payload={
                    "batch_start": 1,
                    "batch_end": 5,
                    "chapters_done": 5,
                    "chapters_total": 36,
                },
            ),
        ],
    )

    # 18/36 -> 86, 5/36 -> 82; should keep the high-water mark at 86.
    assert compute_job_progress(job) == 86


def test_init_long_format_retry_does_not_take_over_visible_progress() -> None:
    job = DesktopJobRecord(
        job_id="job-format-retry",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="format_retry",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="plan_outline_starting",
                payload={"chapters_done": 0, "total_chapters": 70},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="plan_outline_batch_1_4",
                payload={
                    "batch_start": 1,
                    "batch_end": 4,
                    "chapters_done": 0,
                    "chapters_total": 70,
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="format_retry",
                payload={
                    "task": "plan_outline_continue",
                    "attempt": 1,
                    "max_attempts": 4,
                    "error": "Local JSON repair lost structural content",
                },
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert _compact_step_summary(job) == "步骤：章节大纲 · 10/15"
    assert _compute_card_progress(job) == _compute_card_progress(
        _make_job(
            step="plan_outline_batch_1_4",
            payload={"batch_start": 1, "batch_end": 4, "chapters_done": 0, "chapters_total": 70},
        )
    )


def test_init_long_format_validation_success_does_not_take_over_visible_progress() -> None:
    job = DesktopJobRecord(
        job_id="job-format-success",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="format_validation_success",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="plan_outline_starting",
                payload={"chapters_done": 0, "total_chapters": 70},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="plan_outline_batch_1_4",
                payload={
                    "batch_start": 1,
                    "batch_end": 4,
                    "chapters_done": 0,
                    "chapters_total": 70,
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="format_validation_success",
                payload={
                    "task": "plan_outline_continue",
                    "attempt": 1,
                    "max_attempts": 4,
                    "parse_source": "json",
                },
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_outline"
    assert _compact_step_summary(job) == "步骤：章节大纲 · 10/15"
    assert _active_display_step_name_for_job(job) != "format_validation_success"
    assert _compute_card_progress(job) == _compute_card_progress(
        _make_job(
            step="plan_outline_batch_1_4",
            payload={"batch_start": 1, "batch_end": 4, "chapters_done": 0, "chapters_total": 70},
        )
    )


def test_init_long_token_preflight_does_not_regress_contract_resume_card() -> None:
    job = DesktopJobRecord(
        job_id="job-contract-preflight",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="preflight_token_estimate",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="init_resume_anchor",
                payload={
                    "step": "plan_chapter_contracts",
                    "artifact": "chapter_contracts",
                    "label": "章节契约",
                },
            ),
            DesktopJobEvent(
                at="2026-03-13T13:16:00+00:00",
                step="plan_chapter_contracts_resumed",
                payload={"count": 70},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:17:00+00:00",
                step="preflight_token_estimate",
                payload={
                    "task": "ADJUDICATE_CONTRACT_COHERENCE",
                    "input_token_estimate": 32000,
                },
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("init_long")}

    assert _indicator_current_step_key(job, visible_keys) == "plan_chapter_contracts"
    assert _compact_step_summary(job) == "步骤：章节契约 · 12/15"
    assert compute_job_progress(job) >= compute_job_progress(
        _make_job(step="plan_chapter_contracts")
    )


def test_compute_job_progress_uses_dynamic_book_consistency_repair_ratio() -> None:
    job = DesktopJobRecord(
        job_id="job-3",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_repair_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_repair_progress",
                payload={"processed": 3, "total": 6, "chapter_number": 18},
            )
        ],
    )

    # 3/6 -> 68 + 20 = 88 (audit=50%, verify=25%, repair=25%)
    assert compute_job_progress(job) == 88


def test_book_consistency_repair_display_includes_issue_focus() -> None:
    job = DesktopJobRecord(
        job_id="job-3b",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_repair_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_repair_progress",
                payload={
                    "status": "matching",
                    "order": 2,
                    "processed": 1,
                    "total": 6,
                    "chapter_number": 18,
                    "issue_focus": "时间线2项、角色状态1项 / 严重1",
                },
            )
        ],
    )

    assert display_step_name_for_job(job) == (
        "全书逐章修复  ·  第 18 章  ·  2 / 6  ·  定位问题  ·  时间线2项、角色状态1项 / 严重1"
    )


def test_book_consistency_audit_display_shows_scope_and_batch() -> None:
    job = DesktopJobRecord(
        job_id="job-3c",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency",
                payload={
                    "status": "running",
                    "count": 82,
                    "analysis_mode": "full_text",
                    "audit_max_chapters_per_batch": 12,
                },
            )
        ],
    )

    assert display_step_name_for_job(job) == (
        "全书一致性审计  ·  模型审计中 · 82 章 · 全文深审 · 每批≤12章"
    )


def test_book_consistency_two_phase_progress_uses_named_milestone() -> None:
    job = DesktopJobRecord(
        job_id="job-audit-two-phase",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_two_phase_start",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:52+00:00",
                step="book_consistency_issue_pool_ready",
                payload={"enabled": True, "issue_pool_size": 142, "chapters": 82},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_two_phase_start",
                payload={
                    "total_chapters": 82,
                    "threshold": 0.7,
                    "max_target_chapters": 24,
                },
            ),
        ],
    )

    assert compute_job_progress(job) == 22
    assert display_step_name_for_job(job) == (
        "一致性审计 · 智能漏斗摘要筛查开始  ·  扫描 82 章 · 最多深审 24 章"
    )


def test_book_consistency_chunk_progress_stays_in_audit_phase_after_two_phase() -> None:
    job = DesktopJobRecord(
        job_id="job-audit-chunk",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_chunk_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:52+00:00",
                step="book_consistency_two_phase_start",
                payload={"total_chapters": 82, "max_target_chapters": 24},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_chunk_progress",
                payload={"current": 1, "total": 8},
            ),
        ],
    )

    assert compute_job_progress(job) == 22
    assert display_step_name_for_job(job) == "一致性审计 · 全书分批审计推进中  ·  块 1 / 8"


def test_book_consistency_button_progress_uses_event_payload() -> None:
    class Dummy(ChapterStudioJobsMixin):
        pass

    dummy = Dummy()
    dummy._book_level_jobs = [
        DesktopJobRecord(
            job_id="job-audit-progress",
            kind="book_consistency",
            label="全书一致性审计 · demo",
            status=DesktopJobState.RUNNING,
            current_step="book_consistency_repair_progress",
            events=[
                DesktopJobEvent(
                    at="2026-03-13T13:15:54+00:00",
                    step="book_consistency_repair_progress",
                    payload={"processed": 3, "total": 6, "chapter_number": 18},
                )
            ],
        )
    ]

    assert dummy._compute_audit_state() == (AuditState.REPAIRING, 88)


def test_book_consistency_verify_progress_accepts_current_payload_key() -> None:
    job = DesktopJobRecord(
        job_id="job-audit-verify",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_verify_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_verify_progress",
                payload={"current": 5, "total": 20},
            )
        ],
    )

    assert compute_job_progress(job) == 56
    assert display_step_name_for_job(job) == "一致性审计 · 逐章验证审计问题  ·  5 / 20"


def test_book_consistency_issue_pool_display_shows_anchor_count() -> None:
    job = DesktopJobRecord(
        job_id="job-3d",
        kind="book_consistency",
        label="全书一致性审计 · demo",
        status=DesktopJobState.RUNNING,
        current_step="book_consistency_issue_pool_ready",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="book_consistency_issue_pool_ready",
                payload={"enabled": True, "issue_pool_size": 142, "chapters": 82},
            )
        ],
    )

    assert display_step_name_for_job(job) == (
        "准备审计 · 问题池锚点已就绪  ·  问题池 142 条 · 覆盖 82 章"
    )


def test_resolve_checkpoint_progress_and_display_use_localized_quality_stage() -> None:
    job = DesktopJobRecord(
        job_id="job-4",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="reading_power_eval",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="reading_power_eval",
                payload={},
            )
        ],
    )

    assert compute_job_progress(job) == 67
    assert display_step_name_for_job(job) == "文本精修 · 追读力复评"


def test_resolve_checkpoint_wave_progress_uses_generate_handoff_stage() -> None:
    job = DesktopJobRecord(
        job_id="job-wave",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="wave",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="wave",
                payload={"artifact": "v1_wave.md"},
            )
        ],
    )

    assert compute_job_progress(job) == 35
    assert display_step_name_for_job(job) == "初稿成章 · WAVE 场景编织"


@pytest.mark.parametrize(
    ("completed_stage", "expected_step", "expected_progress", "expected_label", "expected_summary"),
    [
        (
            "draft_done",
            "pre_alignment",
            40,
            "断点恢复 · 已完成初稿成章，继续质量检查",
            "步骤：质量检查 · 3/7",
        ),
        (
            "quality_done",
            "post_alignment",
            62,
            "断点恢复 · 已完成质量检查，继续文本精修",
            "步骤：文本精修 · 6/7",
        ),
        (
            "repair_done",
            "post_alignment",
            62,
            "断点恢复 · 已完成修复循环，继续文本精修",
            "步骤：文本精修 · 6/7",
        ),
    ],
)
def test_resolve_checkpoint_resume_from_progress_uses_next_visible_stage(
    completed_stage: str,
    expected_step: str,
    expected_progress: int,
    expected_label: str,
    expected_summary: str,
) -> None:
    job = DesktopJobRecord(
        job_id=f"job-resume-{completed_stage}",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="resume_from_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="resume_from_progress",
                payload={"completed_stage": completed_stage, "skipped": [completed_stage]},
            ),
        ],
    )
    visible_keys = {step.key for step in _visible_steps_for_kind("resolve_chapter_checkpoint")}

    assert compute_job_progress(job) == expected_progress
    assert display_step_name_for_job(job) == expected_label
    assert _indicator_current_step_key(job, visible_keys) == expected_step
    assert _compact_step_summary(job) == expected_summary


def test_guard_constraint_check_does_not_jump_to_archive_decision() -> None:
    job = DesktopJobRecord(
        job_id="job-guard-check",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="guard_constraint_compliance_check",
        events=[
            DesktopJobEvent(
                at="2026-05-14T13:15:54+00:00",
                step="alignment",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:16:54+00:00",
                step="guard_constraint_compliance_check",
                payload={},
            ),
        ],
    )

    assert compute_job_progress(job) == 62
    assert display_step_name_for_job(job) == "文本精修 · 护栏约束合规检查"


def test_resolve_checkpoint_indicator_ignores_future_phase_high_water() -> None:
    job = DesktopJobRecord(
        job_id="job-precheck-with-stale-polish",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="audit_context_preparing",
        events=[
            DesktopJobEvent(
                at="2026-05-14T13:15:54+00:00",
                step="audit_context_preparing",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:16:54+00:00",
                step="reading_power_eval",
                payload={},
            ),
        ],
    )
    steps = _visible_steps_for_kind("resolve_chapter_checkpoint")

    assert compute_job_progress(job) == 40
    assert _compact_step_summary(job) == "步骤：质量检查 · 3/7"
    assert _indicator_state_for_job(job, steps).dot_states == (
        "done",
        "done",
        "active",
        "pending",
        "pending",
        "pending",
        "pending",
    )


def test_resolve_checkpoint_extract_failure_ignores_parallel_evaluate_high_water() -> None:
    job = DesktopJobRecord(
        job_id="job-extract-failed",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 81 章",
        status=DesktopJobState.FAILED,
        current_step="extract_canon",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="extract_canon",
                payload={"status": "starting"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:55+00:00",
                step="evaluate",
                payload={"overall_score": 7.2},
            ),
        ],
    )

    assert compute_job_progress(job) == 72
    assert display_step_name_for_job(job) == "归档选择 · 提取剧情状态"


def test_failed_step_inference_prefers_unfinished_extract_over_parallel_evaluate() -> None:
    job = DesktopJobRecord(
        job_id="job-failed-step",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 81 章",
        status=DesktopJobState.RUNNING,
        current_step="evaluate",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="extract_canon",
                payload={"status": "starting"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:55+00:00",
                step="evaluate",
                payload={"overall_score": 7.2},
            ),
        ],
    )
    payload = {
        "category": "model",
        "title": "AI 输出 JSON 格式错误",
        "summary": "模型返回的内容无法解析为合法 JSON。",
        "detail": "Extra data: line 1 column 3271",
    }

    assert _infer_failed_step(job, payload) == "extract_canon"


def test_failed_step_inference_uses_structured_prompt_task() -> None:
    job = DesktopJobRecord(
        job_id="job-prompt-contract",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 6 章",
        status=DesktopJobState.RUNNING,
        current_step="guard_constraint_compliance_check",
    )
    payload = {
        "title": "任务失败",
        "summary": "ValidationError: prompt context invalid",
        "context": {
            "field": "prompt_context",
            "value": {"task": "evaluate", "source": "PromptBuilder.render:evaluate"},
        },
    }

    assert _infer_failed_step(job, payload) == "evaluate"


def test_failed_step_inference_parses_legacy_prompt_error_text() -> None:
    job = DesktopJobRecord(
        job_id="job-legacy-prompt-contract",
        kind="resolve_chapter_checkpoint",
        label="断点恢复 · demo / 第 6 章",
        status=DesktopJobState.RUNNING,
        current_step="repair_audit_summary",
    )
    payload = {
        "detail": (
            "RuntimeError: PromptBuilder.render(evaluate): 'list object' has no attribute beats"
        )
    }

    assert _infer_failed_step(job, payload) == "evaluate"


def test_finalize_checkpoint_progress_uses_remapped_percentages() -> None:
    job = DesktopJobRecord(
        job_id="job-5",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档决策 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="post_guard_repair",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="post_guard_repair",
                payload={},
            )
        ],
    )

    assert compute_job_progress(job) == 30
    assert display_step_name_for_job(job) == "归档前修复"


def test_finalize_checkpoint_guard_constraint_check_keeps_archive_decision_active() -> None:
    job = DesktopJobRecord(
        job_id="job-finalize-guard-check",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行 · demo / 第 1 章",
        status=DesktopJobState.RUNNING,
        current_step="guard_constraint_compliance_check",
        events=[
            DesktopJobEvent(
                at="2026-06-24T13:15:54+00:00",
                step="guard_constraint_compliance_check",
                payload={"chapter": 1},
            ),
        ],
    )
    steps = _visible_steps_for_kind("resolve_chapter_checkpoint_finalize")

    assert compute_job_progress(job) == 10
    assert display_step_name_for_job(job) == "归档选择 · 护栏约束合规检查"
    assert _compact_step_summary(job) == "步骤：归档选择 · 1/7"
    assert _indicator_state_for_job(job, steps).dot_states == (
        "active",
        "pending",
        "pending",
        "pending",
        "pending",
        "pending",
        "pending",
    )


def test_finalize_checkpoint_review_event_stays_on_state_extraction_step() -> None:
    job = DesktopJobRecord(
        job_id="job-finalize-causal",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行 · demo / 第 1 章",
        status=DesktopJobState.RUNNING,
        current_step="causal_validation",
        events=[
            DesktopJobEvent(
                at="2026-06-24T13:15:54+00:00",
                step="accept_and_finalize",
                payload={"chapter_number": 1},
            ),
            DesktopJobEvent(
                at="2026-06-24T13:15:55+00:00",
                step="polish_reextract_canon",
                payload={"chapter": 1},
            ),
            DesktopJobEvent(
                at="2026-06-24T13:15:56+00:00",
                step="causal_validation",
                payload={"chapter": 1},
            ),
        ],
    )
    steps = _visible_steps_for_kind("resolve_chapter_checkpoint_finalize")

    assert compute_job_progress(job) == 52
    assert display_step_name_for_job(job) == "状态提取 · 校验因果逻辑"
    assert _compact_step_summary(job) == "步骤：状态提取 · 3/7"
    assert _indicator_state_for_job(job, steps).dot_states == (
        "done",
        "done",
        "active",
        "pending",
        "pending",
        "pending",
        "pending",
    )


def test_finalize_checkpoint_state_adjudication_stays_before_persist() -> None:
    job = DesktopJobRecord(
        job_id="job-finalize-state",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行 · demo / 第 1 章",
        status=DesktopJobState.RUNNING,
        current_step="state_delta_adjudication",
        events=[
            DesktopJobEvent(
                at="2026-06-24T13:15:54+00:00",
                step="accept_and_finalize",
                payload={"chapter_number": 1},
            ),
            DesktopJobEvent(
                at="2026-06-24T13:15:55+00:00",
                step="candidate_state_deltas",
                payload={"chapter": 1, "status": "done"},
            ),
            DesktopJobEvent(
                at="2026-06-24T13:15:56+00:00",
                step="state_delta_adjudication",
                payload={"chapter": 1},
            ),
        ],
    )
    steps = _visible_steps_for_kind("resolve_chapter_checkpoint_finalize")

    assert compute_job_progress(job) == 52
    assert display_step_name_for_job(job) == "状态提取 · LLM 裁判状态变化"
    assert _indicator_state_for_job(job, steps).dot_states[:4] == (
        "done",
        "done",
        "active",
        "pending",
    )


def test_finalize_checkpoint_memory_progress_has_own_visible_stage() -> None:
    job = DesktopJobRecord(
        job_id="job-memory",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档前修复 · demo / 第 2 章",
        status=DesktopJobState.RUNNING,
        current_step="memory_concurrent_tasks_started",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="persist",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:55+00:00",
                step="volume_audit",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:56+00:00",
                step="memory_concurrent_tasks_started",
                payload={"tasks": ["episodic", "summary", "motifs"]},
            ),
        ],
    )

    assert compute_job_progress(job) == 99
    assert display_step_name_for_job(job) == "记忆更新 · 记忆并发任务执行中"


def test_motif_repair_layer2_progress_shows_running_summary() -> None:
    job = DesktopJobRecord(
        job_id="job-6",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 21,
                    "total": 30,
                    "chapter_number": 24,
                    "status": "done",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 71
    assert display_step_name_for_job(job) == "母题逐章处理  ·  第 24 章  ·  已提取 1  ·  21 / 30"


def test_motif_repair_layer2_progress_running_summary_with_mixed_statuses() -> None:
    events = [
        DesktopJobEvent(
            at="2026-03-13T13:15:01+00:00",
            step="motif_repair_layer2_scanning",
            payload={"processed": 1, "total": 30, "chapter_number": 1, "status": "skipped"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:02+00:00",
            step="motif_repair_layer2_scanning",
            payload={"processed": 2, "total": 30, "chapter_number": 2, "status": "skipped"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:03+00:00",
            step="motif_repair_layer2_scanning",
            payload={"processed": 3, "total": 30, "chapter_number": 4, "status": "missing"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:10+00:00",
            step="motif_repair_layer2_progress",
            payload={"processed": 4, "total": 30, "chapter_number": 3, "status": "done"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:11+00:00",
            step="motif_repair_layer2_progress",
            payload={"processed": 5, "total": 30, "chapter_number": 5, "status": "done"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:12+00:00",
            step="motif_repair_layer2_progress",
            payload={"processed": 6, "total": 30, "chapter_number": 7, "status": "empty"},
        ),
        DesktopJobEvent(
            at="2026-03-13T13:15:13+00:00",
            step="motif_repair_layer2_progress",
            payload={"processed": 7, "total": 30, "chapter_number": 6, "status": "done"},
        ),
    ]
    job = DesktopJobRecord(
        job_id="job-6b",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=events,
    )

    assert display_step_name_for_job(job) == (
        "母题逐章处理  ·  第 6 章  ·  已有缓存，跳过 2 · 章节文件缺失 1 · 已提取 3 · 无母题返回 1  ·  7 / 30"
    )


def test_motif_repair_layer2_done_summarizes_counts() -> None:
    job = DesktopJobRecord(
        job_id="job-7",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_done",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_done",
                payload={
                    "chapters_processed": 8,
                    "chapters_skipped": 9,
                    "chapters_empty": 2,
                    "chapters_failed": 1,
                    "motifs_extracted": 124,
                },
            )
        ],
    )

    assert display_step_name_for_job(job) == (
        "母题重新提取完成  ·  提取 8 章，跳过 9 章，母题片段 124 条，空结果 2 章，失败 1 章"
    )


def test_motif_repair_layer2_progress_shows_skipped_status() -> None:
    job = DesktopJobRecord(
        job_id="job-8",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 5,
                    "total": 30,
                    "chapter_number": 3,
                    "status": "skipped",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 47
    assert (
        display_step_name_for_job(job) == "母题逐章处理  ·  第 3 章  ·  已有缓存，跳过 1  ·  5 / 30"
    )


def test_motif_repair_layer2_progress_shows_missing_status() -> None:
    job = DesktopJobRecord(
        job_id="job-9",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 7,
                    "total": 30,
                    "chapter_number": 12,
                    "status": "missing",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 50
    assert (
        display_step_name_for_job(job) == "母题逐章处理  ·  第 12 章  ·  章节文件缺失 1  ·  7 / 30"
    )


def test_motif_repair_layer2_progress_shows_empty_status() -> None:
    job = DesktopJobRecord(
        job_id="job-10",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 10,
                    "total": 30,
                    "chapter_number": 15,
                    "status": "empty",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 55
    assert (
        display_step_name_for_job(job) == "母题逐章处理  ·  第 15 章  ·  无母题返回 1  ·  10 / 30"
    )


def test_motif_repair_layer2_progress_shows_error_status() -> None:
    job = DesktopJobRecord(
        job_id="job-11",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 15,
                    "total": 30,
                    "chapter_number": 20,
                    "status": "error",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 62
    assert display_step_name_for_job(job) == "母题逐章处理  ·  第 20 章  ·  提取失败 1  ·  15 / 30"


def test_motif_repair_layer2_progress_without_chapter_number() -> None:
    job = DesktopJobRecord(
        job_id="job-12",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 2,
                    "total": 30,
                    "status": "error",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 43
    assert display_step_name_for_job(job) == "母题逐章处理  ·  提取失败 1  ·  2 / 30"


def test_motif_repair_layer2_progress_unknown_status_falls_back_to_ratio() -> None:
    job = DesktopJobRecord(
        job_id="job-13",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_progress",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:54+00:00",
                step="motif_repair_layer2_progress",
                payload={
                    "processed": 25,
                    "total": 30,
                    "chapter_number": 28,
                    "status": "unknown_value",
                },
            )
        ],
    )

    assert compute_job_progress(job) == 77
    assert display_step_name_for_job(job) == "母题逐章处理  ·  第 28 章  ·  25 / 30"


def test_motif_repair_layer2_scanning_shows_running_summary() -> None:
    job = DesktopJobRecord(
        job_id="job-14",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_scanning",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:01+00:00",
                step="motif_repair_layer2_scanning",
                payload={"processed": 1, "total": 30, "chapter_number": 1, "status": "skipped"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:02+00:00",
                step="motif_repair_layer2_scanning",
                payload={"processed": 2, "total": 30, "chapter_number": 2, "status": "skipped"},
            ),
            DesktopJobEvent(
                at="2026-03-13T13:15:03+00:00",
                step="motif_repair_layer2_scanning",
                payload={"processed": 3, "total": 30, "chapter_number": 4, "status": "missing"},
            ),
        ],
    )

    assert compute_job_progress(job) == 44
    assert (
        display_step_name_for_job(job)
        == "母题扫描章节文件  ·  第 4 章  ·  已有缓存，跳过 2 · 章节文件缺失 1  ·  3 / 30"
    )


def test_motif_repair_layer2_start_shows_concurrency_and_range() -> None:
    job = DesktopJobRecord(
        job_id="job-15",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_start",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="motif_repair_layer2_start",
                payload={"start_chapter": 1, "end_chapter": 30, "concurrency": 3},
            )
        ],
    )

    assert display_step_name_for_job(job) == "开始重新提取母题  ·  3章并行 · 第 1-30 章"


def test_motif_repair_layer2_start_without_concurrency() -> None:
    job = DesktopJobRecord(
        job_id="job-16",
        kind="repair_motif_history",
        label="修补母题历史 · demo / 第 30 章",
        status=DesktopJobState.RUNNING,
        current_step="motif_repair_layer2_start",
        events=[
            DesktopJobEvent(
                at="2026-03-13T13:15:00+00:00",
                step="motif_repair_layer2_start",
                payload={"start_chapter": 5, "end_chapter": 20},
            )
        ],
    )

    assert display_step_name_for_job(job) == "开始重新提取母题  ·  第 5-20 章"


def test_handle_step_maps_repair_attempt_guidance_dimension() -> None:
    """_handle_step sets current_step to payload['dimension'] for repair_attempt_guidance."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-dim",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001

    manager._handle_step(  # noqa: SLF001
        "j-dim",
        "repair_attempt_guidance",
        {"chapter": 1, "dimension": "continuity_repair", "round": 1, "max_rounds": 2},
    )
    assert record.current_step == "continuity_repair"


def test_handle_step_fallback_when_dimension_missing() -> None:
    """_handle_step falls back to repair_attempt_guidance when dimension is absent."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-nodim",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001

    manager._handle_step(  # noqa: SLF001
        "j-nodim",
        "repair_attempt_guidance",
        {"chapter": 1, "round": 1},
    )
    assert record.current_step == "repair_attempt_guidance"


def test_handle_step_prompt_pressure_keeps_current_step() -> None:
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-pressure",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="draft",
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001

    manager._handle_step(  # noqa: SLF001
        "j-pressure",
        "prompt_pressure",
        {"task": "DRAFT_CHAPTER", "token_pressure": 0.82},
    )

    assert record.current_step == "draft"
    # prompt_pressure is a high-frequency diagnostic event that is skipped
    # by the fast path in _handle_step (not appended to events list).
    assert len(record.events) == 0


@pytest.mark.parametrize(
    ("step", "label"),
    [
        ("bridge_prompt_diagnostics", "章节桥接提示词诊断"),
        ("draft_prompt_diagnostics", "草稿提示词诊断"),
        ("edit_prompt_diagnostics", "章节编辑提示词诊断"),
        ("plan_prompt_diagnostics", "章节计划提示词诊断"),
        ("plan_prompt_diagnostics_retry", "章节计划提示词诊断（重试）"),
    ],
)
def test_prompt_diagnostics_are_localized_non_progress_events(step: str, label: str) -> None:
    """Prompt diagnostics must never replace a visible workflow milestone."""
    assert is_non_progress_step_event(step)
    assert display_step_name(step) == label


def test_prompt_diagnostic_keeps_draft_milestone_active() -> None:
    """A persisted diagnostic event projects back to the active draft stage."""
    job = DesktopJobRecord(
        job_id="job-draft-diagnostics",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 9 章",
        status=DesktopJobState.RUNNING,
        current_step="draft_prompt_diagnostics",
        events=[
            DesktopJobEvent(
                at="2026-07-18T10:00:00+00:00",
                step="plan_checkpoint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-07-18T10:00:01+00:00",
                step="draft",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-07-18T10:00:02+00:00",
                step="draft_prompt_diagnostics",
                payload={},
            ),
        ],
    )
    steps = _visible_steps_for_kind(job.kind)

    assert display_step_name_for_job(job) == "初稿成章 · 草稿生成"
    assert _compact_step_summary(job) == "步骤：初稿成章 · 2/7"
    assert _compute_card_progress(job) == 21
    assert _indicator_state_for_job(job, steps).dot_states == (
        "done",
        "active",
        "pending",
        "pending",
        "pending",
        "pending",
        "pending",
    )


def test_handle_step_prompt_diagnostic_keeps_current_step() -> None:
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-draft-diagnostics",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo",
        status=DesktopJobState.RUNNING,
        current_step="draft",
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001

    manager._handle_step(  # noqa: SLF001
        record.job_id,
        "draft_prompt_diagnostics",
        {"prompt_tokens": 1200},
    )

    assert record.current_step == "draft"
    assert record.events[-1].step == "draft_prompt_diagnostics"


def test_handle_step_format_validation_success_keeps_current_step() -> None:
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-format-success",
        kind="init_long",
        label="长篇立项 · demo",
        status=DesktopJobState.RUNNING,
        current_step="plan_outline_batch_1_4",
    )
    with manager._lock:  # noqa: SLF001
        manager._jobs[record.job_id] = record  # noqa: SLF001

    manager._handle_step(  # noqa: SLF001
        "j-format-success",
        "format_validation_success",
        {"task": "plan_outline_continue", "attempt": 1, "parse_source": "json"},
    )

    assert record.current_step == "plan_outline_batch_1_4"
    assert record.events[-1].step == "format_validation_success"


def test_repair_round_display_continuity_repair() -> None:
    """continuity_repair with round/max_rounds shows '第 N/M 轮'."""
    job = DesktopJobRecord(
        job_id="job-cont-repair",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 1,
                    "dimension": "continuity_repair",
                    "round": 3,
                    "max_rounds": 5,
                    "strategy": "patch",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "第 3/5 轮" in label


def test_repair_round_display_causal_repair() -> None:
    """causal_repair with round/max_rounds shows '第 N/M 轮'."""
    job = DesktopJobRecord(
        job_id="job-causal-repair",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="causal_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 2,
                    "dimension": "causal_repair",
                    "round": 1,
                    "max_rounds": 2,
                    "strategy": "fulltext",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "第 1/2 轮" in label


def test_repair_round_display_reading_power_repair() -> None:
    """reading_power_repair with round/max_rounds shows '第 N/M 轮'."""
    job = DesktopJobRecord(
        job_id="job-rp-repair",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="reading_power_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 3,
                    "dimension": "reading_power_repair",
                    "round": 2,
                    "max_rounds": 3,
                    "strategy": "patch",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "第 2/3 轮" in label


def test_repair_round_fallback_when_max_rounds_missing() -> None:
    """Payload missing max_rounds → no round suffix, no KeyError."""
    job = DesktopJobRecord(
        job_id="job-no-max",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 1,
                    "dimension": "continuity_repair",
                    "round": 3,
                    "strategy": "patch",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "轮" not in label


def test_repair_round_fallback_when_round_missing() -> None:
    """Payload missing round → no round suffix, no KeyError."""
    job = DesktopJobRecord(
        job_id="job-no-round",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 1,
                    "dimension": "continuity_repair",
                    "max_rounds": 5,
                    "strategy": "patch",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "轮" not in label


def test_repair_round_fallback_when_no_guidance_event() -> None:
    """No repair_attempt_guidance event → no round suffix."""
    job = DesktopJobRecord(
        job_id="job-no-event",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="continuity_eval",
                payload={},
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "轮" not in label


def test_repair_round_display_latest_event_wins() -> None:
    """When multiple repair_attempt_guidance events exist, latest round is used."""
    job = DesktopJobRecord(
        job_id="job-multi",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 1,
                    "dimension": "continuity_repair",
                    "round": 1,
                    "max_rounds": 5,
                    "strategy": "patch",
                },
            ),
            DesktopJobEvent(
                at="2026-06-06T10:01:00+00:00",
                step="repair_attempt_guidance",
                payload={
                    "chapter": 1,
                    "dimension": "continuity_repair",
                    "round": 2,
                    "max_rounds": 5,
                    "strategy": "patch",
                },
            ),
        ],
    )
    label = display_step_name_for_job(job)
    assert "第 2/5 轮" in label


# --- T3: Repair-loop dynamic progress interpolation ---


def _make_repair_job(
    dimension: str,
    round_num: int | None = None,
    max_rounds: int | None = None,
) -> DesktopJobRecord:
    payload: dict[str, object] = {
        "chapter": 1,
        "dimension": dimension,
        "strategy": "patch",
    }
    if round_num is not None:
        payload["round"] = round_num
    if max_rounds is not None:
        payload["max_rounds"] = max_rounds
    return DesktopJobRecord(
        job_id=f"job-repair-{dimension}",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step=dimension,
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="repair_attempt_guidance",
                payload=payload,
            ),
        ],
    )


def test_continuity_repair_round_start() -> None:
    """continuity_repair round 1/2 → 73 (start)."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 1, 2)) == 73


def test_continuity_repair_round_end() -> None:
    """continuity_repair round 2/2 → 75 (end)."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 2, 2)) == 75


def test_continuity_repair_round_start_max5() -> None:
    """continuity_repair round 1/5 → 73 (start)."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 1, 5)) == 73


def test_continuity_repair_round_end_max5() -> None:
    """continuity_repair round 5/5 → 75 (end)."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 5, 5)) == 75


def test_causal_repair_always_87() -> None:
    """causal_repair any round → 87 (start==end, no change)."""
    assert compute_job_progress(_make_repair_job("causal_repair", 1, 2)) == 87
    assert compute_job_progress(_make_repair_job("causal_repair", 2, 2)) == 87
    assert compute_job_progress(_make_repair_job("causal_repair", 1, 5)) == 87
    assert compute_job_progress(_make_repair_job("causal_repair", 5, 5)) == 87


def test_reading_power_repair_interpolation() -> None:
    """reading_power_repair round 1/2 → 88, round 2/2 → 89."""
    assert compute_job_progress(_make_repair_job("reading_power_repair", 1, 2)) == 88
    assert compute_job_progress(_make_repair_job("reading_power_repair", 2, 2)) == 89


def test_repair_loop_missing_round_returns_start() -> None:
    """Payload missing round → returns start, no KeyError."""
    assert compute_job_progress(_make_repair_job("continuity_repair", None, 5)) == 73
    assert compute_job_progress(_make_repair_job("reading_power_repair", None, 2)) == 88


def test_repair_loop_missing_max_rounds_returns_start() -> None:
    """Payload missing max_rounds → returns start, no KeyError."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 3, None)) == 73


def test_repair_loop_zero_max_rounds() -> None:
    """max_rounds=0 → returns start, no ZeroDivisionError."""
    assert compute_job_progress(_make_repair_job("continuity_repair", 1, 0)) == 73


def test_repair_loop_no_guidance_event_returns_start() -> None:
    """No repair_attempt_guidance event → returns start via static lookup."""
    job = DesktopJobRecord(
        job_id="job-no-guidance",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        current_step="continuity_repair",
        events=[
            DesktopJobEvent(
                at="2026-06-06T10:00:00+00:00",
                step="continuity_eval",
                payload={},
            ),
        ],
    )
    assert compute_job_progress(job) == 73


@pytest.fixture
def desktop_window(qtbot: QtBot):
    from novel_forge.desktop.window import NovelForgeDesktopWindow

    # B2 async init: force synchronous RuntimeServices construction so the
    # fixture returns a window with _workspace already populated.
    NovelForgeDesktopWindow._sync_runtime_services_init = True
    try:
        win = NovelForgeDesktopWindow()
        qtbot.addWidget(win)
        return win
    finally:
        NovelForgeDesktopWindow._sync_runtime_services_init = False


def _make_running_chapter_job(*, job_id: str, created_at: str, step: str) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind="run_chapter",
        label=f"章节生成 · {job_id}",
        status=DesktopJobState.RUNNING,
        created_at=created_at,
        current_step=step,
        events=[DesktopJobEvent(at=created_at, step=step, payload={})],
    )


def test_status_bar_shows_running_job_step_and_progress(desktop_window) -> None:
    job = _make_running_chapter_job(
        job_id="running-1",
        created_at="2026-06-06T10:00:00+00:00",
        step="plan",
    )

    desktop_window._job_manager.jobs = lambda: [job]  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    step_text = desktop_window._status_step_label.text()
    cost_text = desktop_window._status_cost_label.text()

    assert step_text != ""
    assert "·" in step_text
    assert step_text.rstrip().endswith("%")
    assert cost_text == ""


def test_status_bar_labels_empty_when_no_running_jobs(desktop_window) -> None:
    desktop_window._job_manager.jobs = lambda: []  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    assert desktop_window._status_step_label.text() == ""
    assert desktop_window._status_cost_label.text() == ""


def test_status_bar_picks_earliest_running_job_by_created_at(desktop_window) -> None:
    later = _make_running_chapter_job(
        job_id="running-later",
        created_at="2026-06-06T10:05:00+00:00",
        step="draft",
    )
    earlier = _make_running_chapter_job(
        job_id="running-earlier",
        created_at="2026-06-06T10:00:00+00:00",
        step="plan",
    )

    desktop_window._job_manager.jobs = lambda: [later, earlier]  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    step_text = desktop_window._status_step_label.text()
    assert step_text != ""
    assert "·" in step_text
    assert (
        step_text
        == f"{display_step_name_for_job(earlier)} · {compute_task_flow_progress(earlier)}%"
    )
    assert step_text != f"{display_step_name_for_job(later)} · {compute_task_flow_progress(later)}%"


def test_status_bar_ignores_non_running_jobs(desktop_window) -> None:
    succeeded = DesktopJobRecord(
        job_id="succeeded-1",
        kind="run_chapter",
        label="章节生成 · succeeded",
        status=DesktopJobState.SUCCEEDED,
        created_at="2026-06-06T10:00:00+00:00",
        current_step="plan",
    )

    desktop_window._job_manager.jobs = lambda: [succeeded]  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    assert desktop_window._status_step_label.text() == ""
    assert desktop_window._status_cost_label.text() == ""


def test_status_bar_permanent_widgets_attached_to_status_bar(desktop_window) -> None:
    bar = desktop_window.statusBar()
    permanent = set(bar.findChildren(type(desktop_window._status_step_label)))

    assert desktop_window._status_step_label in permanent
    assert desktop_window._status_cost_label in permanent


# --- T10: Real-time token/cost display ---


def test_handle_step_accumulates_tokens_so_far() -> None:
    """Trace totals update tokens without fabricating an unknown provider cost."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-tok",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-1",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    manager._handle_step("j-tok", "draft", {"tokens_so_far": 1500})
    assert record.cumulative_tokens == 1500
    assert record.cumulative_cost_usd == 0.0

    manager._handle_step("j-tok", "edit", {"tokens_so_far": 3200})
    assert record.cumulative_tokens == 3200
    assert record.cumulative_cost_usd == 0.0

    manager._handle_step(
        "j-tok",
        "evaluate",
        {"tokens_so_far": 3400, "cost_so_far": 0.034},
    )
    assert record.cumulative_tokens == 3400
    assert record.cumulative_cost_usd == pytest.approx(0.034)


def test_handle_step_ignores_payload_without_tokens_so_far() -> None:
    """_handle_step does not touch cumulative_tokens when payload lacks tokens_so_far."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-no-tok",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-1",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    manager._handle_step("j-no-tok", "draft", {"chapter": 1})
    assert record.cumulative_tokens == 0
    assert record.cumulative_cost_usd == 0.0


def test_token_update_coalesced_by_50ms_timer(qtbot: QtBot) -> None:
    """Multiple _handle_step calls within 50 ms produce a single token_update emission."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-coal",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-coal",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    received: list[tuple[str, int, float]] = []
    manager.token_update.connect(lambda pid, t, c: received.append((pid, t, c)))

    for tokens in [100, 200, 500, 1000, 2000]:
        manager._handle_step("j-coal", "draft", {"tokens_so_far": tokens})

    assert len(received) == 0
    qtbot.wait(120)
    assert len(received) == 1
    assert received[0] == ("proj-coal", 2000, 0.0)


def test_step_coalescing_reuses_timers(qtbot: QtBot) -> None:
    """Coalescing timers are reusable QObjects, not one timer per burst."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-reuse",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-reuse",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    jobs_timer = manager._jobs_bind_timer
    token_timer = manager._token_timer

    manager._handle_step("j-reuse", "draft", {"tokens_so_far": 100})
    qtbot.wait(120)
    manager._handle_step("j-reuse", "wave", {"tokens_so_far": 200})

    assert manager._jobs_bind_timer is jobs_timer
    assert manager._token_timer is token_timer


def test_high_frequency_job_events_use_slower_bind_timer(qtbot: QtBot) -> None:
    """Stream/model-call bursts should not drive full job binding at 20 FPS."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-high-frequency",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-high-frequency",
        status=DesktopJobState.RUNNING,
        current_step="draft",
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    manager._handle_step(
        record.job_id,
        "llm_stream_delta",
        {"stream_id": "s1", "delta": "正文"},
    )
    assert manager._jobs_bind_timer.isActive()
    assert manager._jobs_bind_timer.interval() >= 200

    manager._handle_step(record.job_id, "wave", {})
    assert manager._jobs_bind_timer.interval() <= 130

    manager.shutdown(wait_ms=0)


def test_shutdown_stops_job_manager_coalescing_timers() -> None:
    """DesktopJobManager.shutdown stops pending coalesced job/token emits."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    record = DesktopJobRecord(
        job_id="j-shutdown",
        kind="run_chapter",
        label="章节生成 · demo",
        project_id="proj-shutdown",
        status=DesktopJobState.RUNNING,
    )
    with manager._lock:
        manager._jobs[record.job_id] = record

    manager._handle_step("j-shutdown", "draft", {"tokens_so_far": 100})
    assert manager._jobs_bind_timer.isActive()
    assert manager._token_timer.isActive()

    manager.shutdown(wait_ms=0)

    assert not manager._jobs_bind_timer.isActive()
    assert not manager._token_timer.isActive()


def test_history_loaded_marks_ready_and_refreshes_jobs() -> None:
    """Async history completion marks readiness and notifies job consumers."""
    from novel_forge.desktop.jobs import DesktopJobManager

    manager = DesktopJobManager(load_persisted_history=False)
    manager._history_loaded = False
    manager._history_event.clear()

    history_events: list[bool] = []
    jobs_events: list[bool] = []
    manager.history_loaded.connect(lambda: history_events.append(True))
    manager.jobs_changed.connect(lambda: jobs_events.append(True))

    manager._handle_history_loaded()

    assert manager._history_loaded is True
    assert manager._history_event.is_set()
    assert history_events == [True]
    assert jobs_events == [True]


def test_cost_label_updated_by_token_update_signal(desktop_window) -> None:
    """_update_cost_label sets the cost label text correctly."""
    desktop_window._update_cost_label("proj-1", 5000, 0.0075)
    text = desktop_window._status_cost_label.text()
    assert "5.0k tokens" in text
    assert "$0.007" in text


def test_cost_label_does_not_refade_on_every_token_change(desktop_window, monkeypatch) -> None:
    """Running token updates should change text without restarting the fade animation."""
    import novel_forge.desktop.window as window_module

    calls: list[tuple[object, int, bool]] = []

    class _FakeAnimation:
        def __init__(self) -> None:
            self.finished = self

        def connect(self, callback) -> None:  # type: ignore[no-untyped-def]
            self._callback = callback

        def stop(self) -> None:
            pass

    def fake_fade_in(widget, *, duration, delete_when_stopped):  # type: ignore[no-untyped-def]
        calls.append((widget, duration, delete_when_stopped))
        return _FakeAnimation()

    monkeypatch.setattr(window_module, "motion_animations_supported", lambda _kind: True)
    monkeypatch.setattr(window_module.Motion, "fade_in", fake_fade_in)

    desktop_window._update_cost_label("proj-1", 5000, 0.0075)
    first_anim = desktop_window._cost_fade_anim
    desktop_window._update_cost_label("proj-1", 5200, 0.0078)
    desktop_window._update_cost_label("proj-1", 5200, 0.0078)

    assert len(calls) == 1
    assert desktop_window._cost_fade_anim is first_anim
    assert "5.2k tokens" in desktop_window._status_cost_label.text()


def test_cost_label_cleared_when_zero_tokens(desktop_window) -> None:
    """_update_cost_label with 0 tokens clears the label."""
    desktop_window._last_cost_text = "5.0k tokens  ·  $0.007"
    desktop_window._status_cost_label.setText(desktop_window._last_cost_text)
    desktop_window._update_cost_label("proj-1", 0, 0.0)
    assert desktop_window._status_cost_label.text() == ""
    assert desktop_window._last_cost_text == ""


def test_cost_label_shows_token_data_from_running_job(desktop_window) -> None:
    """_update_status_bar_labels populates cost label from running job's cumulative_tokens."""
    job = DesktopJobRecord(
        job_id="running-tok",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        created_at="2026-06-06T10:00:00+00:00",
        current_step="draft",
        cumulative_tokens=8500,
        cumulative_cost_usd=8500 * 0.0015,
        events=[DesktopJobEvent(at="2026-06-06T10:00:00+00:00", step="draft", payload={})],
    )

    desktop_window._job_manager.jobs = lambda: [job]  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    text = desktop_window._status_cost_label.text()
    assert "8.5k tokens" in text
    assert "$" in text


def test_cost_label_empty_when_running_job_has_zero_tokens(desktop_window) -> None:
    """_update_status_bar_labels clears cost label when running job has 0 tokens."""
    job = DesktopJobRecord(
        job_id="running-no-tok",
        kind="run_chapter",
        label="章节生成 · demo",
        status=DesktopJobState.RUNNING,
        created_at="2026-06-06T10:00:00+00:00",
        current_step="plan",
        cumulative_tokens=0,
        events=[DesktopJobEvent(at="2026-06-06T10:00:00+00:00", step="plan", payload={})],
    )

    desktop_window._job_manager.jobs = lambda: [job]  # type: ignore[method-assign]
    desktop_window._bind_jobs()

    assert desktop_window._status_cost_label.text() == ""
