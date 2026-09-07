from __future__ import annotations

import pytest

from novel_forge.app_service.contracts import JobKind, JobRecord, JobState, JobStepEvent
from novel_forge.app_service.engine_novel import project_novel_studio_view
from novel_forge.app_service.engine_views import (
    engine_capabilities,
    project_job_view,
    project_running_operation_detail,
    project_task_stream_view,
)
from novel_forge.app_service.engine_voice import project_voice_studio_view
from novel_forge.tts.schemas import (
    ChapterTakeManifest,
    DubbingScript,
    DubbingSegment,
    SegmentTakeVersion,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TakeReviewStatus,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.workspace.contracts import (
    ChapterWorkspaceChapter,
    ChapterWorkspaceSnapshot,
    DecisionCheckpoint,
    DecisionOption,
)
from novel_forge.workspace.projects import ProjectDetail


def test_stream_projection_keeps_parallel_validation_active_after_sibling_ends() -> None:
    record = JobRecord(
        job_id="parallel",
        kind=JobKind.INIT_LONG,
        label="初始化",
        status=JobState.RUNNING,
        events=[
            JobStepEvent(
                at="2026-08-27T10:00:00Z", step="llm_stream_start", payload={"stream_id": "a"}
            ),
            JobStepEvent(
                at="2026-08-27T10:00:01Z",
                step="llm_stream_end",
                payload={"stream_id": "b", "validation_status": "validated"},
            ),
        ],
    )
    assert project_task_stream_view(record).status == "streaming"


@pytest.mark.parametrize(
    "verdict,expected",
    [("repairing", "streaming"), ("failed", "failed"), ("validated", "completed")],
)
def test_stream_projection_uses_durable_verdicts_and_marks_clipped_snapshots(
    verdict: str,
    expected: str,
) -> None:
    record = JobRecord(
        job_id="durable",
        kind=JobKind.INIT_LONG,
        label="初始化",
        status=JobState.RUNNING,
        stream_results={
            "s": JobStepEvent(
                at="2026-08-27T10:00:00Z",
                step="llm_stream_validation",
                payload={
                    "stream_id": "s",
                    "operation_id": "op",
                    "validation_status": verdict,
                    "text": '{"summary":"截取',
                    "text_length": 7000,
                    "repair_source": "local",
                    "attempt": 2,
                    "max_attempts": 2,
                    "model_id": "model-x",
                    "finish_reason": "stop",
                },
            )
        },
    )
    view = project_task_stream_view(record)
    assert view.status == expected
    assert len(view.events) == 2
    snapshot = view.events[0]
    assert snapshot.text_mode == "snapshot"
    assert snapshot.text_truncated is True
    assert snapshot.text_length == 7000
    assert snapshot.operation_id == "op"
    assert snapshot.validation_status == verdict
    assert snapshot.model_id == "model-x"
    assert snapshot.finish_reason == "stop"


def test_paused_checkpoint_keeps_validated_clipped_result_metadata() -> None:
    record = JobRecord(
        job_id="paused-plan",
        kind=JobKind.PREPARE_CHAPTER,
        label="章节方案",
        status=JobState.PAUSED,
        stream_results={
            "plan": JobStepEvent(
                at="2026-08-30T14:18:31Z",
                step="llm_stream_validation",
                payload={
                    "stream_id": "plan",
                    "operation_id": "plan-op",
                    "output_kind": "json",
                    "validation_status": "validated",
                    "text": '{"scene_intents":[{"summary":"截取预览"}',
                    "text_length": 23_938,
                    "text_truncated": True,
                    "attempt": 2,
                    "max_attempts": 3,
                },
            )
        },
    )

    view = project_task_stream_view(record)
    assert view.status == "paused"
    assert view.job_state == "paused"
    snapshot = next(event for event in view.events if event.segment == "content")
    assert snapshot.validation_status == "validated"
    assert snapshot.text_length == 23_938
    assert snapshot.text_truncated is True


def test_stream_status_resolves_only_the_same_operation_retry() -> None:
    record = JobRecord(
        job_id="retries",
        kind=JobKind.INIT_LONG,
        label="初始化",
        status=JobState.RUNNING,
        events=[
            JobStepEvent(
                at=f"2026-08-27T10:00:0{index}Z",
                step="llm_stream_validation",
                payload={
                    "stream_id": stream_id,
                    "operation_id": operation_id,
                    "attempt": attempt,
                    "task": "extract_init_coherence_claims",
                    "validation_status": status,
                },
            )
            for index, (stream_id, operation_id, attempt, status) in enumerate(
                [("a1", "a", 1, "retrying"), ("b1", "b", 1, "failed"), ("a2", "a", 2, "validated")]
            )
        ],
    )
    assert project_task_stream_view(record).status == "failed"
    record.events.pop(1)
    assert project_task_stream_view(record).status == "completed"


def test_running_operation_detail_explains_capacity_retry_and_local_repair() -> None:
    record = JobRecord(
        kind=JobKind.PREPARE_CHAPTER,
        label="章节准备",
        status=JobState.RUNNING,
        events=[
            JobStepEvent(
                at="2026-08-29T10:00:00Z",
                step="llm_stream_validation",
                payload={
                    "task": "plan_chapter",
                    "validation_status": "retrying",
                    "finish_reason": "length",
                    "attempt": 1,
                    "max_attempts": 3,
                },
            )
        ],
    )
    assert project_running_operation_detail(record) == (
        "章节规划达到输出上限，正在扩容重试（第 2/3 次）"
    )
    record.events.append(
        JobStepEvent(
            at="2026-08-29T10:00:01Z",
            step="llm_stream_validation",
            payload={
                "task": "plan_chapter",
                "validation_status": "repairing",
                "repair_source": "local",
                "attempt": 2,
                "max_attempts": 3,
            },
        )
    )
    assert project_running_operation_detail(record) == (
        "正在本地修正章节规划格式（第 2/3 次）"
    )


def test_engine_capabilities_are_explicit_about_safe_phase_one_scope() -> None:
    view = engine_capabilities()

    assert view.contract_version == "1.0"
    assert view.features == {
        "job_queries": True,
        "task_stream_snapshots": True,
        "task_stream_subscription": True,
        "engine_commands": True,
        "runtime_status": True,
        "authoring_policy_v1": True,
        "authoring_coauthor": True,
        "planning_horizon_jobs": True,
        "repair_workbench_v1": True,
        "repair_publish_v1": True,
    }
    assert view.commands == {
        "prepare_chapter": True,
        "resolve_chapter_checkpoint": True,
        "polish_chapter": True,
        "repair_continuity": True,
        "repair_causal": True,
        "repair_issues": True,
        "reevaluate_chapter": True,
        "reextract_relationships": True,
        "repair_motif_history": True,
        "polish_outline": True,
        "audit_book": True,
        "audit_book_editorial": True,
        "execute_global_repair_queue": True,
        "export_book": True,
        "clean_chapters": True,
        "cancel_job": True,
        "resume_job": True,
        "retry_init_repair": True,
        "save_init_manual_repair": True,
        "save_narrative_character": True,
        "retire_narrative_character": True,
        "save_narrative_relationship": True,
        "remove_narrative_relationship": True,
        "save_humanize_pattern": True,
        "set_humanize_pattern_enabled": True,
        "remove_humanize_pattern": True,
        "merge_humanize_patterns": True,
        "rebuild_memory_vectors": True,
        "clear_job_history": True,
        "acknowledge_task_errors": True,
        "reopen_task_errors": True,
        "clear_closed_task_errors": True,
        "clear_error_archive": True,
        "continue_long_init": True,
        "restart_long_init": True,
        "start_workflow": True,
        "save_chapter_revision": True,
        "save_token_dashboard_preferences": True,
        "save_settings": True,
        "delete_projects": True,
        "test_model_profile": True,
        "generate_workflow_fields": True,
        "synthesize_voice": True,
        "build_voice_team": True,
        "confirm_voice_team": True,
        "clone_character_voice": True,
        "design_character_voice": True,
        "approve_character_voice": True,
        "preview_character_voice": True,
        "update_voice_performance": True,
        "assign_catalog_voice": True,
        "generate_voice_script": True,
        "save_voice_script": True,
        "preview_voice_segment": True,
        "accept_voice_take": True,
        "reassemble_voice": True,
        "resolve_speakers": True,
        "export_audio": True,
        "export_audiobook": True,
        "ollama_management": True,
    }
    modules = {item.id: item for item in view.modules}
    assert "run_chapter" in modules["novel"].durable_job_kinds
    assert modules["voice"].durable_job_kinds == [
        "tts_build_voice_team",
        "tts_export_audio",
        "tts_export_audiobook",
        "tts_full_pipeline",
        "tts_generate_script",
        "tts_synthesize",
    ]
    assert "generate_script" not in modules["voice"].in_process_only_operations
    assert "export_audio" not in modules["voice"].in_process_only_operations


def test_job_projection_is_camel_case_ready_and_uses_true_prompt_failure() -> None:
    record = JobRecord(
        job_id="job-failed",
        kind=JobKind.RESOLVE_CHAPTER_CHECKPOINT,
        label="断点恢复 · 青瓦梦匙 / 第 6 章",
        project_id="青瓦梦匙",
        status=JobState.FAILED,
        current_step="guard_constraint_compliance_check",
        error="Prompt context validation failed",
        error_summary={
            "error_code": "validation_error",
            "category": "validation",
            "summary": "Prompt context validation failed",
            "context": {
                "field": "prompt_context",
                "value": {
                    "task": "evaluate",
                    "source": "PromptBuilder.render:evaluate",
                },
            },
            "recovery_actions": [{"action": "retry", "label": "重试"}],
        },
    )

    view = project_job_view(record)
    payload = view.model_dump(mode="json", by_alias=True)

    assert view.error is not None
    assert view.error.failed_step == "evaluate"
    assert payload["projectId"] == "青瓦梦匙"
    # Failed jobs retain the same workflow position as their task card.
    assert payload["progressPercent"] == 79
    assert payload["error"]["failedStep"] == "evaluate"


def test_job_progress_projection_accepts_ratio_and_terminal_state() -> None:
    running = JobRecord(
        kind=JobKind.RUN_CHAPTER,
        label="章节续写",
        status=JobState.RUNNING,
        current_step_payload={"progress": 0.42},
    )
    succeeded = running.model_copy(update={"status": JobState.SUCCEEDED})

    assert project_job_view(running).progress_percent == 42
    assert project_job_view(succeeded).progress_percent == 100


def test_task_stream_projection_preserves_ordered_segments_and_summary() -> None:
    record = JobRecord(
        job_id="job-stream",
        kind=JobKind.RUN_CHAPTER,
        label="第 6 章",
        status=JobState.RUNNING,
        current_step="draft_chapter",
        cumulative_tokens=34,
        cumulative_cost_usd=0.12,
        events=[
            JobStepEvent(
                at="2026-07-18T10:00:00+00:00",
                step="llm_stream_start",
                payload={"stream_id": "s1", "task": "DRAFT_CHAPTER", "attempt": 1},
            ),
            JobStepEvent(
                at="2026-07-18T10:00:01+00:00",
                step="llm_stream_delta",
                payload={
                    "stream_id": "s1",
                    "task": "DRAFT_CHAPTER",
                    "output_kind": "text",
                    "segments": [
                        {"kind": "reasoning", "text": "先梳理因果"},
                        {"kind": "content", "text": "沈岸握住了银手链。"},
                    ],
                },
            ),
            JobStepEvent(
                at="2026-07-18T10:00:02+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_done",
                    "call_id": "call-draft-1",
                    "task": "DRAFT_CHAPTER",
                    "provider": "openai",
                    "model": "gpt-test",
                    "route": "primary",
                    "attempt": 1,
                    "max_attempts": 3,
                    "latency_ms": 1200,
                    "prompt_tokens": 21,
                    "completion_tokens": 13,
                    "total_tokens": 34,
                    "cost_usd": 0.12,
                },
            ),
            JobStepEvent(
                at="2026-07-18T10:00:03+00:00",
                step="llm_stream_end",
                payload={
                    "stream_id": "s1",
                    "task": "DRAFT_CHAPTER",
                    "attempt": 1,
                    "output_kind": "text",
                    "chars": 10,
                    "text": "完整结果用于替换片段",
                },
            ),
        ],
    )

    first = project_task_stream_view(record)
    second = project_task_stream_view(record)

    assert first.status == "completed"
    assert [event.sequence for event in first.events] == [1, 2, 3, 4, 5]
    assert [event.kind for event in first.events] == [
        "stream_start",
        "delta",
        "delta",
        "stream_end",
        "stream_end",
    ]
    assert [event.segment for event in first.events] == [
        "system",
        "reasoning",
        "content",
        "content",
        "system",
    ]
    assert first.events[1].text == "先梳理因果"
    assert first.events[2].text == "沈岸握住了银手链。"
    assert first.events[2].text_mode == "delta"
    assert first.events[3].text == "完整结果用于替换片段"
    assert first.events[3].text_mode == "snapshot"
    # Event-level output_kind is carried from the delta payload so readers can
    # pick the renderer without guessing from partial text.
    assert first.events[1].output_kind == "text"
    assert first.events[2].output_kind == "text"
    # stream_end also carries the declared contract; only the start event
    # predates it (no output_kind in its payload).
    assert first.events[0].output_kind is None
    assert first.events[-1].output_kind == "text"
    assert first.events[-1].text is None
    assert [event.cursor for event in first.events] == [event.cursor for event in second.events]
    assert first.summary is not None
    assert first.summary.output_kind == "text"
    assert first.summary.output_characters == 10
    assert first.summary.provider == "openai"
    assert first.summary.total_tokens == 34
    assert len(first.calls) == 1
    assert first.calls[0].call_id == "call-draft-1"
    assert first.calls[0].task_label == "DRAFT 原稿"
    assert first.calls[0].status == "success"
    assert first.calls[0].route == "primary"
    assert first.calls[0].total_tokens == 34
    assert first.calls[0].started_at == "2026-07-18T10:00:02+00:00"
    assert first.calls[0].finished_at == "2026-07-18T10:00:02+00:00"


def test_task_stream_projects_only_valid_engine_owned_delivery_link() -> None:
    record = JobRecord(
        job_id="job-export",
        kind=JobKind.TTS_EXPORT_AUDIO,
        label="导出第 2 章音频",
        project_id="青瓦梦匙",
        status=JobState.SUCCEEDED,
        result={"export_filename": "chapter_002.mp3", "chapter_text": "不会泄漏"},
    )

    view = project_task_stream_view(record)

    assert view.delivery is not None
    assert view.delivery.filename == "chapter_002.mp3"
    assert view.delivery.download_url.endswith("/exports/chapter_002.mp3")
    assert "%E9%9D%92" in view.delivery.download_url
    assert "chapter_text" not in view.model_dump(mode="json")

    unsafe = record.model_copy(update={"result": {"export_filename": "../outside.mp3"}})
    assert project_task_stream_view(unsafe).delivery is None


def test_task_stream_projection_collapses_model_call_lifecycle_and_retries() -> None:
    record = JobRecord(
        job_id="job-calls",
        kind=JobKind.INIT_LONG,
        label="长篇立项",
        status=JobState.RUNNING,
        current_step="plan_outline",
        events=[
            JobStepEvent(
                at="2026-07-18T10:00:00+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_start",
                    "call_id": "call-outline",
                    "task": "PLAN_OUTLINE",
                    "provider": "openai",
                    "model": "gpt-test",
                    "route": "primary",
                    "max_tokens": 8192,
                },
            ),
            JobStepEvent(
                at="2026-07-18T10:00:03+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_error",
                    "call_id": "call-outline",
                    "task": "PLAN_OUTLINE",
                    "attempt": 1,
                    "max_attempts": 3,
                    "will_retry": True,
                    "latency_ms": 3000,
                },
            ),
            JobStepEvent(
                at="2026-07-18T10:00:04+00:00",
                step="model_call_update",
                payload={
                    "event": "api_stream_done",
                    "call_id": "call-outline",
                    "task": "PLAN_OUTLINE",
                    "attempt": 2,
                    "max_attempts": 3,
                    "will_retry": False,
                    "prompt_tokens": 1200,
                    "completion_tokens": 460,
                    "latency_ms": 4100,
                    "cost_usd": 0.02,
                },
            ),
        ],
    )

    view = project_task_stream_view(record)

    assert len(view.calls) == 1
    call = view.calls[0]
    assert call.status == "success"
    assert call.attempt == 2
    assert call.max_attempts == 3
    assert call.total_tokens == 1660
    assert call.started_at == "2026-07-18T10:00:00+00:00"
    assert call.finished_at == "2026-07-18T10:00:04+00:00"


def test_init_long_job_projection_emits_chinese_step_label() -> None:
    record = JobRecord(
        job_id="job-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        project_id="眠咒",
        status=JobState.RUNNING,
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

    view = project_job_view(record)
    payload = view.model_dump(mode="json", by_alias=True)

    assert view.step_label == (
        "冲突候选裁判  ·  当前层：章节契约  ·  批次 7 / 7  ·  并发 2  ·  问题 0  ·  判定：通过"
    )
    assert payload["stepLabel"] == view.step_label
    assert payload["currentStep"] == "adjudicate_init_conflict_candidates_73_80"


def test_init_long_task_stream_projection_keeps_step_id_and_localizes_label() -> None:
    record = JobRecord(
        job_id="job-stream-init",
        kind=JobKind.INIT_LONG,
        label="长篇立项 · 眠咒",
        status=JobState.RUNNING,
        current_step="init_coherence_recheck_chunk_start",
        current_step_payload={
            "stage": "contract_coherence",
            "chunk_index": 6,
            "chunk_count": 12,
            "focus_chapters": [25],
        },
    )

    view = project_task_stream_view(record)

    assert view.step_id == "init_coherence_recheck_chunk_start"
    assert view.step_label == (
        "init_coherence_recheck_chunk_start  ·  分块 6 / 12  ·  当前层：章节契约  ·  聚焦第 25 章"
    )


def test_novel_studio_projection_matches_shared_frontend_shape() -> None:
    detail = ProjectDetail(
        project_id="book", mode="long", title="青瓦梦匙", premise="追查被改写的记忆"
    )
    snapshot = ChapterWorkspaceSnapshot(
        project_id="book",
        project_title="青瓦梦匙",
        chapter_number=6,
        total_chapters=20,
        current_title="银链",
        current_goal="找到苏晚死亡的线索",
        current_outline_summary="沈岸追查银链来历",
        previous_summary="梦境线索浮现",
        carry_forward=["保留怀表异常"],
        chapters=[
            ChapterWorkspaceChapter(
                chapter_number=5,
                title="梦痕",
                status="done",
                status_label="已完成",
                word_count=4200,
            ),
            ChapterWorkspaceChapter(
                chapter_number=6,
                title="银链",
                status="needs_decision",
                status_label="待确认",
            ),
        ],
        pending_checkpoint=DecisionCheckpoint(
            checkpoint_id="cp-6",
            checkpoint_type="plan_checkpoint",
            summary="方案已生成",
            prompt="是否开始写作？",
            options=[
                DecisionOption(
                    option_id="write_now",
                    label="开始写作",
                    is_recommended=True,
                )
            ],
        ),
    )

    view = project_novel_studio_view(detail, snapshot, [])
    payload = view.model_dump(mode="json", by_alias=True)

    assert payload["projectId"] == "book"
    assert payload["nextChapter"] == 6
    assert payload["chapters"][0]["state"] == "completed"
    assert payload["chapters"][1]["state"] == "needs_decision"
    assert payload["activity"]["state"] == "checkpoint"
    assert payload["activity"]["checkpoint"]["options"][0]["recommended"] is True
    assert [item["id"] for item in payload["memoryTabs"]] == [
        "overview",
        "motifs",
        "relationships",
        "issues",
        "reading_power",
        "guardrails",
        "control",
    ]


def test_novel_studio_activity_exposes_engine_operation_detail() -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙")
    snapshot = ChapterWorkspaceSnapshot(
        project_id="book",
        project_title="青瓦梦匙",
        chapter_number=6,
        total_chapters=20,
        current_title="银链",
    )
    record = JobRecord(
        job_id="prepare-6",
        kind=JobKind.PREPARE_CHAPTER,
        label="准备第 6 章",
        project_id="book",
        status=JobState.RUNNING,
        current_step="plan",
        current_step_payload={"chapter_number": 6},
        events=[
            JobStepEvent(
                step="llm_stream_validation",
                payload={
                    "chapter_number": 6,
                    "task": "plan_chapter",
                    "validation_status": "validating",
                    "attempt": 2,
                    "max_attempts": 3,
                },
            )
        ],
    )

    view = project_novel_studio_view(detail, snapshot, [record])

    assert view.activity.operation_detail == "章节规划已返回，正在校验结构（第 2/3 次）"


def test_voice_studio_projection_uses_validated_artifacts_without_paths_or_credentials() -> None:
    detail = ProjectDetail(project_id="book", mode="long", title="青瓦梦匙")
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="shen_an",
                character_name="沈岸",
                voice_id="voice-1",
                clone_status=VoiceCloneStatus.READY,
                approval_status="approved",
                match_reasons=["声纹克制"],
                match_warnings=["确认低声部在短句中的清晰度"],
                voice_design_prompt="男声，克制、略低，避免过度压迫。",
                preview_text="这条银链不对。",
                character_gender="male",
                character_age="32岁",
                character_role="protagonist",
                speed_offset=0.12,
                pitch_offset=-2,
                vol_offset=0.08,
            )
        ],
        default_tts_model="speech-test",
    )
    script = DubbingScript(
        chapter_number=6,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.DIALOGUE,
                character_id="shen_an",
                character_name="沈岸",
                text="这条银链不对。",
            ),
            DubbingSegment(
                segment_index=30,
                segment_type=SegmentType.DIALOGUE,
                text="这句话还没有确认说话人。",
            ),
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [30],
                "decisions": [
                    {
                        "segment_index": 30,
                        "character_id": "shen_an",
                        "confidence": 0.86,
                        "rationale": "前文动作主体仍是沈岸",
                    }
                ],
            }
        },
    )

    view = project_voice_studio_view(
        detail,
        chapter_number=6,
        voice_team=team,
        script=script,
        characters=[
            {
                "character_id": "shen_an",
                "name": "沈岸",
                "role": "protagonist",
                "personality": "审慎、克制",
            }
        ],
        segment_statuses={0: "completed"},
        audio_result=None,
        active_task_id=None,
        default_provider="mock",
        default_model="fallback",
        take_manifest=ChapterTakeManifest(
            chapter_number=6,
            drafts={"30": script.segments[1]},
            takes=[
                SegmentTakeVersion(
                    take_id="take-candidate",
                    chapter_number=6,
                    segment_index=0,
                    status=TakeReviewStatus.CANDIDATE,
                    segment=script.segments[0],
                    segment_result=SynthesisResult(
                        segment_index=0,
                        status=SynthesisStatus.COMPLETED,
                        audio_path="/private/take-candidate.mp3",
                    ),
                ),
                SegmentTakeVersion(
                    take_id="take-rejected",
                    chapter_number=6,
                    segment_index=30,
                    status=TakeReviewStatus.REJECTED,
                    segment=script.segments[1],
                    segment_result=SynthesisResult(
                        segment_index=30,
                        status=SynthesisStatus.COMPLETED,
                        audio_path="/private/take-rejected.mp3",
                    ),
                ),
            ],
        ),
    )
    payload = view.model_dump(mode="json", by_alias=True)

    assert payload["projectTitle"] == "青瓦梦匙"
    assert payload["configuredModelLabel"] == "speech-test"
    provider_catalog = payload["providerCatalog"]
    assert {provider["id"] for provider in provider_catalog} >= {
        "minimax",
        "dashscope",
        "tencent",
        "qwen3",
    }
    minimax = next(provider for provider in provider_catalog if provider["id"] == "minimax")
    assert minimax["defaultModel"] == "speech-2.8-hd"
    assert any(setting["parameterId"] == "tts-minimax-force-cbr" for setting in minimax["settings"])
    assert payload["cast"][0]["statusLabel"] == "已就绪"
    assert payload["cast"][0]["voiceId"] == "voice-1"
    assert payload["cast"][0]["voiceSourceLabel"] == "系统音色库"
    assert payload["cast"][0]["speedOffset"] == 0.12
    assert payload["cast"][0]["pitchOffset"] == -2
    assert payload["cast"][0]["volumeOffset"] == 0.08
    assert payload["cast"][0]["performancePolicyLabel"]
    assert payload["cast"][0]["matchSummary"] == "人工分配 / 未评估"
    assert payload["cast"][0]["matchReasons"] == ["声纹克制"]
    assert payload["cast"][0]["auditionWarnings"] == ["确认低声部在短句中的清晰度"]
    assert payload["cast"][0]["auditionText"] == "这条银链不对。"
    assert payload["cast"][0]["designBrief"] == "男声，克制、略低，避免过度压迫。"
    assert payload["cast"][0]["detailFacts"][0]["label"] == "角色依据"
    assert "32岁" in payload["cast"][0]["detailFacts"][0]["value"]
    assert payload["script"][0]["speakerLabel"] == "沈岸"
    assert payload["script"][0]["statusLabel"] == "已合成"
    assert payload["script"][0]["segmentIndex"] == 0
    assert payload["script"][0]["needsSpeakerReview"] is False
    assert payload["script"][1]["segmentIndex"] == 30
    assert payload["script"][1]["needsSpeakerReview"] is True
    assert payload["script"][1]["contextBefore"] == "这条银链不对。"
    assert payload["script"][1]["speakerCandidates"] == [
        {
            "characterId": "shen_an",
            "characterName": "沈岸",
            "confidence": 0.86,
            "reason": "前文动作主体仍是沈岸",
        }
    ]
    assert [
        {key: value for key, value in item.items() if key != "guidance"}
        for item in payload["roomTakes"]
    ] == [
        {
            "segmentIndex": 0,
            "state": "candidate",
            "takeId": "take-candidate",
            "audioUrl": "/api/v1/engine/voice/projects/book/chapters/6/takes/take-candidate/audio",
        },
        {"segmentIndex": 30, "state": "guidance_saved", "takeId": "", "audioUrl": ""},
    ]
    guidance = payload["roomTakes"][0]["guidance"]
    assert guidance["emotion"] == "neutral"
    assert guidance["emotion_intensity"] == 0.5
    assert "text" not in guidance
    assert "platform_extensions" not in guidance
    assert "audioPath" not in str(payload)
    assert "apiKey" not in str(payload)
