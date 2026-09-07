"""Regression coverage for the React/Tauri workflow diagnostic projection."""

from __future__ import annotations

from typing import Any

from novel_forge.api.routes.ui_views import get_step_artifacts, get_workflow_view
from novel_forge.app_service.contracts import JobRecord, JobState
from novel_forge.app_service.workflow_projection import (
    WorkflowProjectionContext,
    project_workflow_run,
)
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest


class _WorkflowLogService:
    def list(self) -> list[Any]:
        return []

    def list_error_log(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "failed-init",
                "time": "2026-08-01T13:39:44+00:00",
                "job_id": "task-17",
                "job_label": "长篇立项 · 眠咒",
                "kind": "任务失败",
                "task": "init_story_bible",
                "attempt": "2/2",
                "error": "模型服务不可用",
                "excerpt": "AuthenticationError: key rejected",
                "log_path": "data/sleep-spell/logs/run-17",
                "auto_resolved": False,
                "acknowledged_at": "2026-08-01T13:45:00+00:00",
                "cause_code": "provider_unavailable",
                "auto_repair_state": "retryable",
                "auto_repair_explanation": "模型服务未完成请求。",
                "recommended_action": "检查路由后重试。",
                "recovery_action_kinds": ["retry"],
            },
            {
                "id": "format-repaired",
                "time": "2026-08-01T13:38:00+00:00",
                "job_id": "task-17",
                "job_label": "长篇立项 · 眠咒",
                "kind": "格式已修复",
                "task": "init_story_bible",
                "attempt": "1/2",
                "error": "格式重试后已恢复",
                "auto_resolved": True,
            },
        ]


async def test_workflow_view_projects_persistent_error_log_entries() -> None:
    view = await get_workflow_view(_WorkflowLogService())

    assert view["error_count"] == 0
    assert len(view["error_log"]) == 2
    entry = view["error_log"][0]
    assert entry == {
        "id": "failed-init",
        "time_label": "2026-08-01T13:39:44+00:00",
        "job_label": "长篇立项 · 眠咒",
        "task_id": "task-17",
        "task_label": "init_story_bible",
        "attempt_label": "2/2",
        "error_message": "模型服务不可用",
        "excerpt": "AuthenticationError: key rejected",
        "log_path": "data/sleep-spell/logs/run-17",
        "kind_label": "任务失败",
        "auto_resolved": False,
        "acknowledged_at": "2026-08-01T13:45:00+00:00",
        "cause_code": "provider_unavailable",
        "auto_repair_state": "retryable",
        "auto_repair_explanation": "模型服务未完成请求。",
        "recommended_action": "检查路由后重试。",
        "recovery_action_kinds": ["retry"],
    }


def test_workflow_projection_exposes_chapter_run_transparency() -> None:
    record = DesktopJobRecord(
        job_id="chapter-transparency",
        kind="run_chapter",
        label="第 3 章",
        project_id="sleep-spell",
        status=DesktopJobState.SUCCEEDED,
        events=[
            DesktopJobEvent(
                at="2026-08-02T10:00:00+00:00",
                step="chapter_research_cache_hit",
                payload={"chapter": 3, "queries": 2},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:01:00+00:00",
                step="final_text_hash_verified",
                payload={"semantic_mutation_allowed_after": False},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:02:00+00:00",
                step="performance_metrics",
                payload={
                    "llm_calls": {"started": 4, "succeeded": 4},
                    "tokens": {"prompt": 120, "completion": 80, "total": 200},
                    "cost_usd": 0.04,
                    "research": {"cache_hits": 1, "cached_queries": 2},
                    "semantic_mutations": {"polish": 1},
                    "report_refreshes": {"requested": 1},
                    "repair_rounds": 1,
                    "final_hash_verifications": 1,
                },
            ),
        ],
    )

    projection = project_workflow_run(record)

    insights = {item["id"]: item for item in projection["run_insights"]}
    assert insights["intent"]["status"] == "success"
    assert insights["research"]["summary"] == "已复用章节证据缓存"
    assert insights["revision"]["count"] == 1
    assert insights["final_verify"]["status"] == "success"
    assert projection["efficiency"] == {
        "llm_calls": 4,
        "prompt_tokens": 120,
        "completion_tokens": 80,
        "total_tokens": 200,
        "cost_usd": 0.04,
        "research_queries": 0,
        "research_cache_hits": 1,
        "semantic_mutations": 1,
        "report_refreshes": 1,
        "repair_rounds": 1,
        "short_revision_rounds": 0,
        "rollbacks": 0,
        "final_hash_verifications": 1,
    }


def test_workflow_quality_uses_latest_recheck_not_repaired_blocker() -> None:
    for kind, check in (
        ("init_long", "init_claim_contract_coverage"),
        ("run_chapter", "quality_gate"),
        ("run_short", "short_quality_gate"),
    ):
        record = DesktopJobRecord(
            job_id=f"repaired-{kind}",
            kind=kind,
            label="已修复",
            status=DesktopJobState.SUCCEEDED,
            current_step="completed",
            events=[
                DesktopJobEvent(
                    at="2026-08-27T10:00:00+00:00",
                    step=check,
                    payload={"blocked": True, "blocked_reason": "旧检查未通过"},
                ),
                DesktopJobEvent(
                    at="2026-08-27T10:01:00+00:00",
                    step=check,
                    payload={"blocked": False, "degraded": False},
                ),
            ],
        )
        projection = project_workflow_run(record)
        assert projection["quality_status"] == "actual"
        assert projection["degradation_reason"] == ""


def test_workflow_quality_does_not_clear_a_different_unresolved_gate() -> None:
    record = DesktopJobRecord(
        job_id="unresolved",
        kind="run_chapter",
        label="审查中",
        status=DesktopJobState.RUNNING,
        events=[
            DesktopJobEvent(
                at="2026-08-27T10:00:00+00:00",
                step="quality_gate",
                payload={"dimension": "continuity", "blocked": True, "summary": "连续性未通过"},
            ),
            DesktopJobEvent(
                at="2026-08-27T10:01:00+00:00",
                step="quality_gate",
                payload={"dimension": "causal", "blocked": False},
            ),
        ],
    )
    projection = project_workflow_run(record)
    assert projection["quality_status"] == "blocked"
    assert projection["degradation_reason"] == "连续性未通过"


def test_workflow_quality_final_result_overrides_intermediate_repair_history() -> None:
    record = DesktopJobRecord(
        job_id="final-quality",
        kind="run_short",
        label="短篇",
        status=DesktopJobState.SUCCEEDED,
        current_step_payload={"blocked": True, "blocked_reason": "修复前"},
        result={"execution_quality_status": "actual"},
    )
    assert project_workflow_run(record)["quality_status"] == "actual"
    record.result = {"execution_quality_status": "blocked", "blocked_reason": "最终未通过"}
    assert project_workflow_run(record)["quality_status"] == "blocked"
    record.status = DesktopJobState.FAILED
    record.result = {"execution_quality_status": "actual"}
    assert project_workflow_run(record)["quality_status"] == "blocked"


def test_workflow_projection_marks_optional_research_and_revision_outcomes() -> None:
    no_research = DesktopJobRecord(
        job_id="short-no-research",
        kind="run_short",
        label="短篇",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        events=[
            DesktopJobEvent(
                at="2026-08-02T10:00:00+00:00",
                step="spec",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:01:00+00:00",
                step="draft",
                payload={},
            ),
        ],
    )
    rolled_back = DesktopJobRecord(
        job_id="short-rollback",
        kind="run_short",
        label="短篇",
        status=DesktopJobState.RUNNING,
        current_step="evaluate",
        events=[
            DesktopJobEvent(
                at="2026-08-02T10:00:00+00:00",
                step="chapter_research_cache_hit",
                payload={"queries": 1},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:01:00+00:00",
                step="short_adaptive_revision_rollback",
                payload={"round": 1},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:02:00+00:00",
                step="evaluate",
                payload={},
            ),
        ],
    )

    no_research_stages = {
        stage["id"]: stage["state"] for stage in project_workflow_run(no_research)["stages"]
    }
    rollback_stages = {
        stage["id"]: stage["state"] for stage in project_workflow_run(rolled_back)["stages"]
    }

    assert no_research_stages["chapter_research"] == "skipped"
    assert rollback_stages["chapter_research"] == "completed"
    assert rollback_stages["edit_"] == "rolled_back"


class _WorkflowRunService:
    def __init__(self, record: DesktopJobRecord, *, storage_root: Any = None) -> None:
        self._record = record
        self.storage_root = storage_root

    def list(self) -> list[DesktopJobRecord]:
        return [self._record]

    def list_error_log(self) -> list[dict[str, Any]]:
        return []


async def test_workflow_view_uses_work_title_instead_of_internal_project_id() -> None:
    record = JobRecord(
        job_id="init-drug-evidence",
        kind="init_long",
        label="长篇立项 · long_be882a54",
        project_id="long_be882a54",
        project_label="药证",
        status=JobState.RUNNING,
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    run = view["runs"][0]
    assert run["project_id"] == "long_be882a54"
    assert run["title"] == "药证"
    assert run["project_label"] == "药证"


async def test_workflow_view_recovers_title_for_legacy_task_history(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("long_be882a54"))
    layout.ensure_dirs()
    storage.save_json(layout.spec_path, {"title": "药证"})
    record = JobRecord(
        job_id="legacy-init",
        kind="init_long",
        label="长篇立项 · long_be882a54",
        project_id="long_be882a54",
        status=JobState.RUNNING,
    )

    view = await get_workflow_view(_WorkflowRunService(record, storage_root=storage.root))

    assert view["runs"][0]["title"] == "药证"
    assert view["runs"][0]["project_label"] == "药证"


async def test_workflow_view_projects_durable_checkpoint_and_signed_lineage(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("sleep-spell"))
    layout.ensure_dirs()
    storage.save_json(
        layout.chapter_review_progress_path(3),
        {
            "completed_stage": "repair_done",
            "source_text_hash": "text-v3",
            "input_signature": "checkpoint-sig",
        },
    )
    manifest = ArtifactManifest(storage, layout)
    manifest.record_success(
        artifact="workflow:run_chapter:result",
        workflow="run_chapter",
        step="completed",
        output_hashes={"result": "result-v1"},
        input_signature="run-sig",
        workflow_version="novel.chapter.v2",
        schema_version=2,
    )
    record = DesktopJobRecord(
        job_id="chapter-3",
        kind="run_chapter",
        label="第 3 章 · 雨夜",
        project_id="sleep-spell",
        status=DesktopJobState.FAILED,
        current_step="continuity_repair",
        current_step_payload={"chapter_number": 3},
        error="模型超时",
        error_summary={"retryable": True},
        cumulative_tokens=4200,
        cumulative_cost_usd=0.21,
    )

    view = await get_workflow_view(_WorkflowRunService(record, storage_root=storage.root))

    run = view["runs"][0]
    assert run["has_checkpoint"] is True
    assert run["checkpoint"] == {
        "exists": True,
        "completed_stage": "repair_done",
        "source_text_hash": "text-v3",
        "input_signature": "checkpoint-sig",
    }
    assert run["workflow_version"] == "novel.chapter.v2"
    assert run["input_signature"] == "run-sig"
    assert run["output_version"] == 1
    assert run["quality_status"] == "blocked"
    assert run["cumulative_tokens"] == 4200
    assert run["recovery_actions"][0]["kind"] == "resume_checkpoint"
    assert any(stage["state"] == "failed" for stage in run["stages"])


async def test_workflow_view_exposes_engine_owned_manual_init_repair_action(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("sleep-spell"))
    layout.ensure_dirs()
    storage.save_json(
        layout.outline_path,
        {
            "title": "眠咒",
            "premise": "雨夜里的一次失约",
            "chapters": [{"number": 1, "title": "雨夜", "summary": "失约"}],
        },
    )
    storage.save_json(
        layout.root / "reports" / "init_readiness.json",
        {
            "summary": "章节大纲继承关系需要人工确认",
            "recovery_actions": [{"kind": "manual_repair", "artifacts": ["outline"]}],
        },
    )
    record = DesktopJobRecord(
        job_id="init-manual-repair",
        kind="init_long",
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=DesktopJobState.FAILED,
        current_step="adjudicate_outline_inheritance",
    )

    view = await get_workflow_view(_WorkflowRunService(record, storage_root=storage.root))

    actions = {action["kind"]: action for action in view["runs"][0]["recovery_actions"]}
    assert actions["manual_init_repair"] == {
        "id": "manual_init_repair",
        "kind": "manual_init_repair",
        "label": "人工修复",
        "enabled": True,
    }


def test_workflow_projection_reuses_project_manifest_within_one_refresh(
    tmp_path,
    monkeypatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("sleep-spell"))
    layout.ensure_dirs()
    ArtifactManifest(storage, layout).record_success(
        artifact="workflow:run_chapter:result",
        workflow="run_chapter",
        step="completed",
        output_hashes={"result": "result-v1"},
    )
    records = [
        DesktopJobRecord(
            job_id=f"chapter-{chapter}",
            kind="run_chapter",
            label=f"第 {chapter} 章",
            project_id="sleep-spell",
            status=DesktopJobState.SUCCEEDED,
            current_step_payload={"chapter_number": chapter},
        )
        for chapter in (1, 2)
    ]
    from novel_forge.app_service import workflow_projection

    reads = 0
    original = workflow_projection._read_json_uncached

    def counted(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(workflow_projection, "_read_json_uncached", counted)
    context = WorkflowProjectionContext(storage_root=storage.root)

    for record in records:
        project_workflow_run(record, context=context)

    assert reads == 1


async def test_workflow_view_uses_engine_owned_progress_and_frontier_for_running_init() -> None:
    record = DesktopJobRecord(
        job_id="init-17",
        kind="init_long",
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=DesktopJobState.RUNNING,
        current_step="plan_blueprint_subplots",
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    run = view["runs"][0]
    assert run["progress_percent"] == 43
    assert run["workflow_projection_version"] == "novel-production.v2"
    assert run["actual_state"] == "running"
    states = {stage["id"]: stage["state"] for stage in run["stages"]}
    assert states["spec"] == "completed"
    assert states["plan_blueprint"] == "active"


async def test_workflow_view_projects_cache_hit_to_its_coherence_stage() -> None:
    record = DesktopJobRecord(
        job_id="init-cache-hit",
        kind="init_long",
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=DesktopJobState.RUNNING,
        current_step="init_claim_entity_adjudication_cache_hit",
        current_step_payload={"stage": "outline_inheritance"},
        events=[
            DesktopJobEvent(
                at="2026-08-02T10:00:00+00:00",
                step="init_claim_entity_adjudication_cache_hit",
                payload={"stage": "outline_inheritance", "cache_hits": 8},
            )
        ],
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    run = view["runs"][0]
    assert run["progress_percent"] > 0
    assert run["current_stage_label"] == "章节大纲"
    assert run["activity_label"] == "章节大纲"
    states = {stage["id"]: stage["state"] for stage in run["stages"]}
    assert states["plan_outline"] == "active"


async def test_workflow_view_emits_chinese_step_label_with_batch_detail() -> None:
    record = DesktopJobRecord(
        job_id="init-adjudicate",
        kind="init_long",
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=DesktopJobState.RUNNING,
        current_step="adjudicate_init_conflict_candidates_73_80",
        current_step_payload={
            "stage": "contract_coherence",
            "batch": 7,
            "batch_total": 7,
            "issues": 0,
            "verdict": "accept",
            "max_parallel": 2,
        },
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    run = view["runs"][0]
    assert run["step_label"] == (
        "冲突候选裁判  ·  当前层：章节契约  ·  批次 7 / 7  ·  并发 2  ·  问题 0  ·  判定：通过"
    )
    assert run["kind"] == "init_long"


async def test_workflow_view_discards_resume_frontier_after_init_rollback() -> None:
    """The web task flow must match the PySide reset after a resume rollback."""

    record = DesktopJobRecord(
        job_id="init-resume-rollback",
        kind="init_long",
        label="长篇立项 · 眠咒",
        project_id="sleep-spell",
        status=DesktopJobState.RUNNING,
        current_step="init_web_research_start",
        events=[
            DesktopJobEvent(
                at="2026-08-02T11:29:45+00:00",
                step="init_resume_anchor",
                payload={"step": "plan_outline"},
            ),
            DesktopJobEvent(
                at="2026-08-02T11:29:46+00:00",
                step="spec_resumed",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-08-02T11:29:47+00:00",
                step="init_web_research_start",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-08-02T11:31:02+00:00",
                step="init_resume_rollback",
                payload={"step": "story_bible"},
            ),
        ],
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    run = view["runs"][0]
    states = {stage["id"]: stage["state"] for stage in run["stages"]}
    assert run["current_stage_label"] == "资料检索"
    assert states["spec"] == "completed"
    assert states["init_web_research"] == "active"
    assert states["plan_chapter_design_matrix"] == "pending"
    assert states["plan_outline"] == "pending"


async def test_step_artifacts_use_pyside_short_story_paths(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = storage.ensure_project_dir("short-project")
    artifact_path = project_dir / "plans" / "short_blueprint.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text('{"title": "短篇蓝图"}', encoding="utf-8")

    result = await get_step_artifacts(
        project_id="short-project",
        kind="run_short",
        step_key="short_blueprint",
        storage=storage,
    )

    assert result["artifacts"] == [
        {
            "label": "叙事蓝图",
            "path": "plans/short_blueprint.json",
            "content": '{"title": "短篇蓝图"}',
            "format": "json",
        }
    ]


async def test_step_artifacts_include_actual_chapter_prose_word_count(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = storage.ensure_project_dir("chapter-artifact-project")
    draft_path = project_dir / "drafts" / "chapter_007" / "v0_draft.md"
    draft_path.parent.mkdir(parents=True)
    prose = "雨水沿着药铺的窗棂滑下。" * 18
    draft_path.write_text(prose, encoding="utf-8")

    result = await get_step_artifacts(
        project_id="chapter-artifact-project",
        kind="run_chapter",
        step_key="bridge",
        storage=storage,
        chapter_number=7,
    )

    artifact = next(
        item for item in result["artifacts"] if item["path"] == "drafts/chapter_007/v0_draft.md"
    )
    assert artifact["word_count"] == count_chapter_words(prose)


async def test_workflow_view_supplies_chapter_context_for_step_artifacts() -> None:
    record = DesktopJobRecord(
        job_id="chapter-7",
        kind="run_chapter",
        label="章节续写 · 测试项目 / 第 7 章",
        project_id="test-long",
        status=DesktopJobState.RUNNING,
        current_step="state_packet",
    )

    view = await get_workflow_view(_WorkflowRunService(record))

    assert view["runs"][0]["chapter_number"] == 7


async def test_step_artifacts_skip_binary_decoding_and_report_size(tmp_path) -> None:
    """Binary exports must never be UTF-8 decoded; a size placeholder is shown."""
    storage = FileSystemStorage(tmp_path)
    project_dir = storage.ensure_project_dir("export-project")
    exports = project_dir / "exports"
    exports.mkdir(parents=True)
    # Invalid UTF-8 bytes would raise UnicodeDecodeError if decoded naively.
    (exports / "全书导出.docx").write_bytes(b"\x00\xff\xfePK\x03\x04binary-blob")

    result = await get_step_artifacts(
        project_id="export-project",
        kind="export_book",
        step_key="export",
        storage=storage,
    )

    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert artifact["format"] == "binary"
    assert artifact["path"] == "exports/全书导出.docx"
    assert "二进制文件" in artifact["content"]
    assert "无法读取" not in artifact["content"]


async def test_step_artifacts_render_subtitle_text_and_binary_audio(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = storage.ensure_project_dir("tts-project")
    audio_dir = project_dir / "tts" / "audio" / "chapter_005"
    audio_dir.mkdir(parents=True)
    (audio_dir / "chapter.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n你好，世界\n", encoding="utf-8"
    )
    (audio_dir / "chapter_full.mp3").write_bytes(b"\xff\xfb\x90\x00id3-audio")

    result = await get_step_artifacts(
        project_id="tts-project",
        kind="tts_full_pipeline",
        step_key="tts_delivery",
        storage=storage,
        chapter_number=5,
    )

    by_path = {item["path"]: item for item in result["artifacts"]}
    subtitle = by_path["tts/audio/chapter_005/chapter.srt"]
    assert subtitle["format"] == "subtitle"
    assert "你好，世界" in subtitle["content"]
    audio = by_path["tts/audio/chapter_005/chapter_full.mp3"]
    assert audio["format"] == "binary"
    assert "二进制文件" in audio["content"]


async def test_step_artifacts_truncate_large_text_with_bounded_read(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_dir = storage.ensure_project_dir("big-project")
    (project_dir / "spec.json").write_text("字" * 600_000, encoding="utf-8")

    result = await get_step_artifacts(
        project_id="big-project",
        kind="run_short",
        step_key="spec",
        storage=storage,
    )

    content = result["artifacts"][0]["content"]
    assert content.endswith("... (内容已截断)")
    assert len(content) == 500_000 + len("\n\n... (内容已截断)")


async def test_step_artifacts_empty_hint_lists_candidate_paths(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir("empty-project")

    result = await get_step_artifacts(
        project_id="empty-project",
        kind="run_short",
        step_key="short_blueprint",
        storage=storage,
    )

    assert result["artifacts"] == []
    assert result["candidate_paths"] == [
        {"label": "叙事蓝图", "path": "plans/short_blueprint.json"}
    ]
    assert "已检查候选产物" in result["empty_hint"]
    assert "plans/short_blueprint.json" in result["empty_hint"]
