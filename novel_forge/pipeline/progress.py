"""Shared step metadata for CLI and desktop progress displays."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

_NON_PROGRESS_EVENT_PREFIXES: tuple[str, ...] = (
    "api_call_",
    "api_stream_",
    "llm_stream_",
    "format_",
    "claim_semantic_repair_",
)
NON_PROGRESS_STEP_EVENTS: frozenset[str] = frozenset(
    {
        "run_log_started",
        "model_call_update",
        "token_escalation",
        "token_budget_normalized",
        "retry_transient_error",
        "task_output_normalized",
        "preflight_token_estimate",
        "memory_gap_compensated",
        "memory_update_failed",
        "budget_status",
        "style_drift_warning",
        "audit_result_update",
        "human_decision_requested",
        "human_decision_resolved",
        "human_decision_timeout",
        "repair_repeated_issue_guard",
        "repair_strategy_diagnosis",
        "repair_strategy_diagnosis_failed",
        "repair_audit_event",
        "repair_audit_summary",
        "repair_round_focus",
        "prompt_pressure",
        "summary_drift_checked",
        "summary_drift_check_failed",
    }
)


def is_non_progress_step_event(step: str) -> bool:
    """Return whether *step* is an observation/diagnostic, not a flow milestone."""
    normalized = str(step or "").strip()
    return (
        normalized in NON_PROGRESS_STEP_EVENTS
        or normalized.startswith(_NON_PROGRESS_EVENT_PREFIXES)
        # Prompt diagnostics describe the request about to be sent to a model;
        # they never advance the workflow itself. Treat every current and
        # future variant (including retry variants) as observation-only so it
        # cannot replace the active UI milestone.
        or "_prompt_diagnostics" in normalized
    )


_PROGRESS_HISTORY_PINNED_STEPS: frozenset[str] = frozenset(
    {
        # These records establish the recovery epoch and must survive bounded
        # diagnostic history.  Without them, a long-running resumed job loses
        # its validated frontier and the UI can appear to jump backwards.
        "init_resume_anchor",
        "init_resume_fallback",
        "init_resume_rollback",
        "short_resume_rollback",
        "init_param_drift",
        "init_project_reset",
        "consistency_replan",
        "regenerate_plan",
        "regenerate_plan_with_notes",
        "edit_plan_and_write",
        "adjust_outline_and_finalize",
    }
)

_HistoryEventT = TypeVar("_HistoryEventT")


def compact_progress_event_history(
    events: Sequence[_HistoryEventT],
    *,
    limit: int,
) -> list[_HistoryEventT]:
    """Bound event history without discarding workflow-replay context.

    Model and stream observations can outnumber actual workflow milestones by
    several orders of magnitude.  Retaining only the last ``limit`` items
    therefore drops ``init_resume_anchor`` and rollback boundaries, making a
    resumed task look like it restarted from an earlier stage.  Keep those
    boundaries plus recent semantic stages, then use any remaining capacity
    for the newest diagnostics.  The original event order is preserved.
    """

    history = list(events)
    if limit <= 0:
        return []
    if len(history) <= limit:
        return history

    pinned_indexes = [
        index
        for index, event in enumerate(history)
        if str(getattr(event, "step", "") or "") in _PROGRESS_HISTORY_PINNED_STEPS
    ]
    if len(pinned_indexes) >= limit:
        selected_indexes = set(pinned_indexes[-limit:])
        return [event for index, event in enumerate(history) if index in selected_indexes]

    selected_indexes = set(pinned_indexes)
    remaining = limit - len(selected_indexes)

    semantic_indexes = [
        index
        for index, event in enumerate(history)
        if index not in selected_indexes
        and not is_non_progress_step_event(str(getattr(event, "step", "") or ""))
    ]
    selected_indexes.update(semantic_indexes[-remaining:])
    remaining = limit - len(selected_indexes)
    if remaining > 0:
        diagnostic_indexes = [
            index for index in range(len(history)) if index not in selected_indexes
        ]
        selected_indexes.update(diagnostic_indexes[-remaining:])

    return [event for index, event in enumerate(history) if index in selected_indexes]


@dataclass(frozen=True)
class StepVisualSpec:
    """Shared UI/CLI metadata for one logical step milestone."""

    key: str
    label: str
    is_prefix: bool = False


_PARALLEL_GROUPS: dict[str, tuple[tuple[str, str], ...]] = {
    "init_long": (
        ("init_story_bible", "plan_blueprint_elements"),
        ("profile_style", "init_entity_registry"),
        ("profile_style", "init_entity_graph"),
        ("plan_blueprint_turning_points", "plan_blueprint_character_arcs"),
    ),
    "run_short": (("short_profile_style", "beats"),),
    "reevaluate_chapter": (
        ("evaluate", "continuity_eval_after_repair"),
        ("evaluate", "causal_eval_after_repair"),
        ("evaluate", "reading_power_eval"),
    ),
}


def parallel_groups_for(kind: str) -> list[tuple[str, str]]:
    """Return concurrent step-key pairs for *kind* (steps run via asyncio.gather)."""
    return list(_PARALLEL_GROUPS.get(kind, ()))


_SUMMARY_STEPS: dict[str, tuple[StepVisualSpec, ...]] = {
    "run_short": (
        StepVisualSpec("spec", "规格确认", is_prefix=True),
        StepVisualSpec("chapter_research", "章节研究", is_prefix=True),
        StepVisualSpec("short_blueprint_elements", "要素选择"),
        StepVisualSpec("short_blueprint", "叙事蓝图"),
        StepVisualSpec("short_profile_style", "风格规范"),
        StepVisualSpec("beats", "节拍生成"),
        StepVisualSpec("short_execution_plan", "执行方案"),
        StepVisualSpec("draft", "初稿"),
        StepVisualSpec("edit_", "自适应修订", is_prefix=True),
        StepVisualSpec("short_completeness_check", "完整性检查"),
        StepVisualSpec("evaluate", "质量评估"),
        StepVisualSpec("creative_summary", "创作分析"),
    ),
    "init_long": (
        StepVisualSpec("spec", "规格确认", is_prefix=True),
        StepVisualSpec("init_web_research", "资料检索", is_prefix=True),
        StepVisualSpec("init_story_bible", "世界观设定", is_prefix=True),
        StepVisualSpec("plan_blueprint_elements", "要素选择", is_prefix=True),
        StepVisualSpec("init_character_bible", "角色设定", is_prefix=True),
        StepVisualSpec("init_character_system", "角色审计", is_prefix=True),
        StepVisualSpec("profile_style", "风格与实体", is_prefix=True),
        StepVisualSpec("init_entity_registry", "实体注册表", is_prefix=True),
        StepVisualSpec("init_entity_graph", "实体图谱", is_prefix=True),
        StepVisualSpec("creative_director_packet", "创作导演", is_prefix=True),
        StepVisualSpec("plan_blueprint", "叙事蓝图", is_prefix=True),
        StepVisualSpec("plan_blueprint_validated", "蓝图验证", is_prefix=True),
        StepVisualSpec("plan_blueprint_repaired", "蓝图修复", is_prefix=True),
        StepVisualSpec("build_init_coherence_profile", "一致性画像", is_prefix=True),
        StepVisualSpec("extract_init_coherence_claims", "一致性 Claims 抽取", is_prefix=True),
        StepVisualSpec("retrieve_init_conflict_candidates", "冲突候选检索", is_prefix=True),
        StepVisualSpec("adjudicate_init_conflict_candidates", "冲突候选裁判", is_prefix=True),
        StepVisualSpec("adjudicate_blueprint_coherence", "蓝图裁判", is_prefix=True),
        StepVisualSpec("repair_init_artifact_patch", "初始化修复", is_prefix=True),
        StepVisualSpec("derive_editorial_contract", "编辑契约", is_prefix=True),
        StepVisualSpec("plan_blueprint_fragments", "蓝图分块", is_prefix=True),
        StepVisualSpec("plan_chapter_design_matrix", "章节设计矩阵", is_prefix=True),
        StepVisualSpec("plan_outline", "章节大纲", is_prefix=True),
        StepVisualSpec("adjudicate_outline_inheritance", "大纲继承裁判", is_prefix=True),
        StepVisualSpec("init_narrative_contract", "叙事契约", is_prefix=True),
        StepVisualSpec("plan_chapter_contracts", "章节契约", is_prefix=True),
        StepVisualSpec("adjudicate_contract_coherence", "契约裁判", is_prefix=True),
        StepVisualSpec("init_claim_contract_coverage", "契约覆盖", is_prefix=True),
        StepVisualSpec("init_source_artifacts", "源头准入", is_prefix=True),
        StepVisualSpec("init_readiness", "初始化准入", is_prefix=True),
        StepVisualSpec("canon_state", "规范初始化", is_prefix=True),
    ),
    "run_chapter": (
        # Keep the autonomous chapter run as detailed as the interactive
        # checkpoint flow.  Both paths execute the same six-phase pipeline,
        # so folding it into five dots made the chapter studio hide the work
        # users need to inspect and made the displayed percentage feel abrupt.
        StepVisualSpec("state_packet", "准备上下文"),
        StepVisualSpec("chapter_research", "章节研究", is_prefix=True),
        StepVisualSpec("bridge", "章节桥接"),
        StepVisualSpec("plan", "章节规划"),
        StepVisualSpec("draft", "DRAFT 草稿"),
        StepVisualSpec("wave", "WAVE 编织"),
        StepVisualSpec("opening_guard", "开篇护栏"),
        StepVisualSpec("alignment", "质量审读"),
        StepVisualSpec("continuity_repair", "连续性修复"),
        StepVisualSpec("alignment_repair", "对齐修复"),
        StepVisualSpec("guard_review", "护栏复核"),
        StepVisualSpec("causal_repair", "因果修复"),
        StepVisualSpec("reading_power_repair", "追读力修复"),
        StepVisualSpec("polish", "文学精修"),
        StepVisualSpec("humanize", "拟人化清理"),
        StepVisualSpec("extract_canon", "状态提取"),
        StepVisualSpec("persist", "正文归档"),
        StepVisualSpec("memory_updated", "记忆更新"),
    ),
    "prepare_chapter": (
        StepVisualSpec("state_packet", "章节上下文"),
        StepVisualSpec("bridge", "桥接与方案"),
        StepVisualSpec("plan_checkpoint", "方案确认"),
    ),
    "resolve_chapter_checkpoint": (
        StepVisualSpec("plan_checkpoint", "方案确认"),
        StepVisualSpec("draft", "初稿成章"),
        StepVisualSpec("pre_alignment", "质量检查"),
        StepVisualSpec("continuity_repair", "连续性修复"),
        StepVisualSpec("alignment_repair", "对齐修复"),
        StepVisualSpec("post_alignment", "文本精修"),
        StepVisualSpec("guard_checkpoint", "归档选择"),
    ),
    "resolve_chapter_checkpoint_finalize": (
        StepVisualSpec("guard_checkpoint", "归档选择"),
        StepVisualSpec("post_guard_repair", "归档前修复"),
        StepVisualSpec("polish_reextract_canon", "状态提取"),
        StepVisualSpec("persist", "正文落盘"),
        StepVisualSpec("evaluate", "质量评估"),
        StepVisualSpec("volume_audit", "卷末审计"),
        StepVisualSpec("memory_updated", "记忆更新"),
    ),
    "polish_chapter": (
        StepVisualSpec("polish_start", "准备精修"),
        StepVisualSpec("polish", "精修与复查"),
    ),
    "repair_continuity": (
        StepVisualSpec("repair_continuity_start", "连贯性修复"),
        StepVisualSpec("continuity_eval_after_repair", "修复后复查"),
        StepVisualSpec("repair_continuity", "修复完成"),
    ),
    "repair_causal": (
        StepVisualSpec("repair_causal_start", "因果链修复"),
        StepVisualSpec("causal_eval_after_repair", "修复后复查"),
        StepVisualSpec("repair_causal", "修复完成"),
    ),
    "repair_issues": (
        StepVisualSpec("repair_continuity_start", "连贯性修复"),
        StepVisualSpec("repair_causal_start", "因果链修复"),
        StepVisualSpec("repair_issues", "修复完成"),
    ),
    "reevaluate_chapter": (
        StepVisualSpec("reevaluate_start", "准备复评"),
        StepVisualSpec("evaluate", "质量评估"),
        StepVisualSpec("continuity_eval_after_repair", "连贯性复评"),
        StepVisualSpec("causal_eval_after_repair", "因果链复评"),
        StepVisualSpec("reading_power_eval", "追读力复评"),
        StepVisualSpec("reevaluate_chapter", "报告更新"),
    ),
    "book_consistency": (
        StepVisualSpec("book_consistency_start", "准备审计"),
        StepVisualSpec("book_consistency", "一致性审计"),
        StepVisualSpec("book_consistency_repair_", "逐章修复", is_prefix=True),
        StepVisualSpec("book_consistency_report_written", "审计完成"),
    ),
    "export_book": (StepVisualSpec("export", "导出"),),
    "reextract_relationships": (
        StepVisualSpec("reextract_start", "准备提取"),
        StepVisualSpec("reextract_chapter", "提取中", is_prefix=True),
        StepVisualSpec("reextract_done", "存档完成"),
    ),
    "repair_motif_history": (
        StepVisualSpec("motif_history_repair_start", "准备修补"),
        StepVisualSpec("motif_repair_layer1_start", "重建缓存统计"),
        StepVisualSpec("motif_repair_layer1_done", "缓存统计完成"),
        StepVisualSpec("motif_repair_layer2_start", "开始重新提取"),
        StepVisualSpec("motif_repair_layer2_scanning", "扫描章节文件", is_prefix=True),
        StepVisualSpec("motif_repair_layer2_progress", "逐章处理", is_prefix=True),
        StepVisualSpec("motif_repair_layer2_done", "重新提取完成"),
        StepVisualSpec("motif_history_repair_done", "修补完成"),
        StepVisualSpec("motif_history_repair_error", "修补失败"),
    ),
    "rebuild_memory_vectors": (
        StepVisualSpec("memory_vector_rebuild_start", "准备重建"),
        StepVisualSpec("memory_vector_rebuild_episodic", "重建主记忆向量", is_prefix=True),
        StepVisualSpec(
            "memory_vector_rebuild_expression",
            "重建表达通道向量",
            is_prefix=True,
        ),
        StepVisualSpec("memory_vector_rebuild_done", "重建完成"),
    ),
    "tts_full_pipeline": (
        StepVisualSpec("tts_prepare", "声音准备"),
        StepVisualSpec("tts_script", "脚本改写"),
        StepVisualSpec("tts_synthesis", "分段合成"),
        StepVisualSpec("tts_post", "对齐混音"),
        StepVisualSpec("tts_delivery", "交付产物"),
    ),
    "tts_post_archive": (
        StepVisualSpec("tts_prepare", "声音准备"),
        StepVisualSpec("tts_script", "脚本改写"),
        StepVisualSpec("tts_synthesis", "分段合成"),
        StepVisualSpec("tts_post", "对齐混音"),
        StepVisualSpec("tts_delivery", "交付产物"),
    ),
    "tts_synthesize": (
        StepVisualSpec("tts_synthesis", "分段合成"),
        StepVisualSpec("tts_post", "对齐混音"),
        StepVisualSpec("tts_delivery", "交付产物"),
    ),
}

_TTS_STEP_ALIASES: dict[str, str] = {
    "tts_auto_trigger_queued": "tts_prepare",
    "tts_auto_trigger_started": "tts_prepare",
    "tts_automation_mode": "tts_prepare",
    "tts_manual_prerequisites_ready": "tts_prepare",
    "tts_narrator_start": "tts_prepare",
    "tts_narrator_llm_call": "tts_prepare",
    "tts_narrator_done": "tts_prepare",
    "tts_voice_team_narrator_only": "tts_prepare",
    "tts_voice_team_reused": "tts_prepare",
    "tts_voice_team_confirmed": "tts_prepare",
    "tts_script_start": "tts_script",
    "tts_script_llm_call": "tts_script",
    "tts_script_reused": "tts_script",
    "tts_script_done": "tts_script",
    "tts_synthesis_start": "tts_synthesis",
    "tts_synthesis_complete": "tts_synthesis",
    "tts_segment": "tts_synthesis",
    "tts_alignment_start": "tts_post",
    "tts_alignment_repair_start": "tts_post",
    "tts_alignment_repair_complete": "tts_post",
    "tts_alignment_complete": "tts_post",
    "tts_assembly_start": "tts_post",
    "tts_assembly_complete": "tts_post",
    "tts_assembly_partial": "tts_post",
    "tts_quality_complete": "tts_delivery",
    "tts_auto_trigger_completed": "tts_delivery",
    "tts_auto_trigger_partial": "tts_delivery",
    "tts_auto_trigger_failed": "tts_delivery",
    "tts_auto_trigger_retry_scheduled": "tts_delivery",
    "tts_speaker_repair_start": "tts_delivery",
    "tts_speaker_repair_done": "tts_delivery",
    "tts_speaker_repair_failed": "tts_delivery",
    "tts_speaker_repair_no_progress": "tts_delivery",
}

_TTS_STEP_PREFIX_ALIASES: tuple[tuple[str, str], ...] = (
    ("voice_team_", "tts_prepare"),
    ("voice_catalog_", "tts_prepare"),
    ("character_voice_", "tts_prepare"),
    ("build_voice_team_", "tts_prepare"),
    ("tts_voice_", "tts_prepare"),
    ("tts_narrator_", "tts_prepare"),
    ("tts_script_", "tts_script"),
    ("tts_synthesis_", "tts_synthesis"),
    ("tts_segment_", "tts_synthesis"),
    ("audio_preflight_", "tts_synthesis"),
    ("tts_alignment_", "tts_post"),
    ("tts_assembly_", "tts_post"),
    ("sound_", "tts_post"),
    ("tts_quality_", "tts_delivery"),
    ("tts_delivery_", "tts_delivery"),
    ("tts_auto_trigger_", "tts_delivery"),
)

_PROGRESS_TTS: dict[str, int] = {
    "tts_prepare": 10,
    "tts_script": 28,
    "tts_synthesis": 55,
    "tts_post": 82,
    "tts_delivery": 96,
}

# _RUN_CHAPTER_STEP_MAP: maps raw step events → detailed UI stages.
# `run_chapter` and `resolve_chapter_checkpoint` execute the same pipeline;
# only their decision boundaries differ.  Keep their visible milestones close
# enough that the chapter studio can expose real work rather than a five-dot
# summary that jumps from drafting straight to archival.
#
# Prefix groups handle the bulk of mappings; this dict keeps only entries that
# do NOT match any prefix pattern (exact-match exceptions).
_RUN_CHAPTER_STEP_MAP: dict[str, str] = {
    "future_planning": "memory_updated",
    "draft_chapter": "draft",
    # --- state_packet stage ---
    "state_packet": "state_packet",
    "writing_mode": "state_packet",
    "context_compress": "state_packet",
    "context_compress_failed": "state_packet",
    "adjudicate_character_introduction_start": "state_packet",
    "adjudicate_character_introduction_done": "state_packet",
    "adjudicate_character_introduction_failed": "state_packet",
    "introduce_character_detected": "state_packet",
    "introduce_character_done": "state_packet",
    "introduce_character_failed": "state_packet",
    "input_integrity_check": "plan",
    "resume_from_progress": "state_packet",
    "chapter_research_start": "chapter_research",
    "chapter_research_cache_hit": "chapter_research",
    "chapter_research_ready": "chapter_research",
    "chapter_research_skipped": "chapter_research",
    # --- planning and generation stages ---
    "bridge": "bridge",
    "plan": "plan",
    "upstream_compass": "plan",
    "scene_plan_validation": "plan",
    "draft": "draft",
    "wave": "wave",
    "bridge_coherence_warning": "bridge",
    "draft_short_text_warning": "wave",
    "precheck_skip_empty_similarity": "wave",
    "time_validation": "plan",
    # --- review and repair stages (exact matches that don't fit prefixes) ---
    "alignment": "alignment",
    "alignment_after_repair": "alignment_repair",
    "alignment_recheck_skipped": "alignment_repair",
    "archive_policy_block": "guard_review",
    "archive_stale_reports_refresh": "guard_review",
    "archive_stale_reports_refresh_failed": "guard_review",
    "best_effort_accepted": "guard_review",
    "chapter_repair_after_alignment": "alignment_repair",
    "chapter_repair_recheck_mode": "alignment_repair",
    "chapter_repair_after_continuity": "alignment_repair",
    "check_editorial": "alignment",
    "consistency_replan": "alignment",
    "cross_dimension_regression": "alignment_repair",
    "early_stop": "alignment_repair",
    "guard_compliance_recheck": "guard_review",
    "guard_constraint_compliance_check": "guard_review",
    "guard_ticket_repair_complete": "guard_review",
    "mechanical_pronoun_fix": "alignment_repair",
    "opening_guard_issues_merged": "opening_guard",
    "polish": "polish",
    "polish_start": "polish",
    "polish_triggered": "polish",
    "pre_repair_alignment_warning": "alignment_repair",
    "prompt_leak_patch_repair_start": "alignment_repair",
    "prompt_leak_patch_repair_complete": "alignment_repair",
    "prompt_leak_deterministic_fallback": "alignment_repair",
    "anchor_recalibration": "alignment_repair",
    "progressive_repair_narrowed": "alignment_repair",
    "quality_check_short_text_warning": "alignment",
    "quality_gate": "guard_review",
    "repair_dimension_skipped_total_cap": "alignment_repair",
    "repair_prescreen_failed": "alignment_repair",
    "repair_rollback": "alignment_repair",
    "repair_suggestion_warnings": "alignment_repair",
    "revelation_density_warning": "alignment_repair",
    "step_2b_continuity_repair": "continuity_repair",
    "word_count_archive_gate": "persist",
    "word_count_archive_gate_skipped": "persist",
    "word_count_polish_continuity_check_error": "polish",
    "word_count_polish_continuity_rollback": "polish",
    "word_count_restructure_applied": "polish",
    "word_count_restructure_guard_verify": "guard_review",
    # --- extract_canon stage ---
    "extract_canon_start": "extract_canon",
    "extract_canon_normalized": "extract_canon",
    "extract_canon": "extract_canon",
    "candidate_state_deltas": "extract_canon",
    "state_delta_adjudication": "extract_canon",
    "contract_coverage_report": "extract_canon",
    "state_adjudication_pre_block_recheck": "extract_canon",
    "final_state_adjudication": "extract_canon",
    "state_adjudication_skipped": "extract_canon",
    "state_adjudication_non_blocking_repair_remaining": "extract_canon",
    "repair_adjudicated_issue": "extract_canon",
    "contract_execution_audit": "extract_canon",
    "contract_execution_audit_skipped": "extract_canon",
    "contract_execution_repair_start": "extract_canon",
    "contract_execution_repair_skipped": "extract_canon",
    "contract_execution_repair_complete": "extract_canon",
    "contract_execution_repair_checkpoint": "extract_canon",
    "progression_ledger_updated": "extract_canon",
    "causal_eval_after_polish": "polish",
    "continuity_eval_after_text_change": "polish",
    "quality_reports_refresh_after_text_change": "polish",
    "alignment_refresh_failed_fallback": "polish",
    "continuity_refresh_failed_fallback": "polish",
    "causal_refresh_failed_fallback": "polish",
    "alignment_refresh_gather_exception": "polish",
    "continuity_refresh_gather_exception": "polish",
    "causal_refresh_gather_exception": "polish",
    "state_ledger_prepared": "extract_canon",
    "state_ledger_written": "extract_canon",
    "kernel_persist_deferred": "extract_canon",
    "continuity_eval_after_polish": "polish",
    "creative_report": "extract_canon",
    "consistency_warnings": "extract_canon",
    "legacy_canon_validation_advisory": "extract_canon",
    "canon_extract_retry": "extract_canon",
    "canon_extract_retry_ok": "extract_canon",
    "chapter_compact": "extract_canon",
    "persist": "persist",
    "evaluate": "persist",
    "auto_register_character_done": "extract_canon",
    "auto_register_character_start": "extract_canon",
    "element_progress_scheduled": "bridge",
    "element_progress_failed": "memory_updated",
    "element_progress_updated": "memory_updated",
    "enrich_introduced_done": "extract_canon",
    "enrich_introduced_failed": "extract_canon",
    "polish_reextract_canon": "polish",
    "post_guard_repair_start": "guard_review",
    "prompt_artifact_cleanup": "extract_canon",
    "prompt_artifact_warning": "extract_canon",
    "reading_power_critical": "reading_power_repair",
    "reading_power_eval_fallback": "reading_power_repair",
    "reading_power_next_chapter_constraints": "reading_power_repair",
    "reading_power_trend": "reading_power_repair",
    "reading_power_warning": "reading_power_repair",
    "reading_power_window_alert": "reading_power_repair",
    "reading_power_window_init_failed": "reading_power_repair",
    "strand_weave_recorded": "extract_canon",
    "style_trend_skipped": "extract_canon",
    "memory_planning_context": "state_packet",
    "memory_draft_context": "bridge",
    "memory_finalize_context": "extract_canon",
    "macro_guard_audit": "persist",
    "macro_guard_audit_failed": "persist",
    "memory_updated": "memory_updated",
    "post_polish_reaudit_skipped": "polish",
    "post_polish_reaudit_warning": "polish",
    "reading_power_post_polish_eval": "polish",
}

# Prefix-based groups for run_chapter step resolution.
# Each tuple: (prefix, target_stage).  Checked in order; first match wins.
_RUN_CHAPTER_PREFIX_GROUPS: tuple[tuple[str, str], ...] = (
    # Generation
    ("edit_", "wave"),
    ("chapter_repair", "wave"),
    ("precheck_", "wave"),
    # Review and repair.  Keep the specific dimensions before generic repair
    # prefixes so the visible timeline retains their diagnostic meaning.
    ("opening_", "opening_guard"),
    ("continuity_", "continuity_repair"),
    ("causal_", "causal_repair"),
    ("reading_power_", "reading_power_repair"),
    ("humanize_", "humanize"),
    ("polish_", "polish"),
    ("guard_", "guard_review"),
    ("macro_guard_", "persist"),
    ("cross_dimension_", "alignment_repair"),
    ("pronoun_", "alignment_repair"),
    ("quality_gate_", "guard_review"),
    ("repair_", "alignment_repair"),
    ("self_repetition_", "alignment_repair"),
    ("semantic_drift_", "alignment_repair"),
    ("post_repair_", "alignment_repair"),
    ("change_budget_", "alignment_repair"),
    ("audit_context_", "alignment"),
    ("critique_", "alignment"),
    ("non_cjk_", "alignment_repair"),
    ("alignment_", "alignment_repair"),
    # Finalize
    ("word_count_", "persist"),
    ("volume_", "persist"),
    ("memory_", "memory_updated"),
    ("budget_", "persist"),
    ("style_drift_", "persist"),
    ("downstream_", "persist"),
    ("enrich_", "extract_canon"),
)

_SHORT_STEP_ALIASES: dict[str, str] = {
    "early_stop": "edit_",
    "blueprint_resumed": "short_blueprint",
    "short_blueprint_elements_resumed": "short_blueprint_elements",
    "short_profile_style_resumed": "short_profile_style",
    "short_profile_style_skipped": "short_profile_style",
    "short_profile_style_failed": "short_profile_style",
    "beats_resumed": "beats",
    "draft_resumed": "draft",
    "short_segment_plan": "draft",
    "short_completion_repair": "short_completeness_check",
    "short_completion_repair_after_eval": "short_completeness_check",
    "chapter_research_start": "chapter_research",
    "chapter_research_cache_hit": "chapter_research",
    "chapter_research_ready": "chapter_research",
    "chapter_research_skipped": "chapter_research",
}

_SHORT_STEP_PREFIX_ALIASES: tuple[tuple[str, str], ...] = (
    ("short_adaptive_", "edit_"),
    ("draft_segment_", "draft"),
    ("short_segment_bridge_", "draft"),
)

_PREPARE_CHAPTER_STEP_MAP: dict[str, str] = {
    "rewrite_strategy": "state_packet",
    "state_packet": "state_packet",
    "context_compress": "state_packet",
    "context_compress_failed": "state_packet",
    "bridge": "bridge",
    "bridge_coherence_warning": "bridge",
    "element_progress_scheduled": "bridge",
    "plan": "bridge",
    "plan_checkpoint": "plan_checkpoint",
    "input_integrity_check": "state_packet",
    "time_validation": "bridge",
}

_RESOLVE_CHECKPOINT_STEP_MAP: dict[str, str] = {
    "write_now": "plan_checkpoint",
    "edit_plan_and_write": "plan_checkpoint",
    "resume_from_progress": "plan_checkpoint",
    "regenerate_plan": "plan_checkpoint",
    "regenerate_plan_with_notes": "plan_checkpoint",
    "state_packet": "plan_checkpoint",
    "writing_mode": "plan_checkpoint",
    "context_compress": "plan_checkpoint",
    "context_compress_failed": "plan_checkpoint",
    "bridge": "plan_checkpoint",
    "bridge_coherence_warning": "plan_checkpoint",
    "plan": "plan_checkpoint",
    "scene_plan_validation": "plan_checkpoint",
    "plan_checkpoint": "plan_checkpoint",
    "time_validation": "plan_checkpoint",
    "draft": "draft",
    "wave": "draft",
    "draft_scene_group": "draft",
    "draft_scene": "draft",
    "scene_stitch": "draft",
    "draft_short_text_warning": "draft",
    "adjudicate_character_introduction_start": "draft",
    "adjudicate_character_introduction_done": "draft",
    "adjudicate_character_introduction_failed": "draft",
    "introduce_character_detected": "draft",
    "introduce_character_done": "draft",
    "introduce_character_failed": "draft",
    "edit_": "draft",
    "edit_budget_trimmed": "draft",
    "edit_early_stop": "draft",
    "chapter_repair": "draft",
    "precheck_early_stop": "draft",
    "precheck_prompt_leak": "draft",
    "prompt_leak_patch_repair_start": "draft",
    "prompt_leak_patch_repair_complete": "draft",
    "prompt_leak_deterministic_fallback": "draft",
    "precheck_skip_empty_similarity": "draft",
    "opening_guard_prescreen": "draft",
    "opening_guard_repair": "draft",
    "opening_guard_issues_merged": "pre_alignment",
    # input_integrity_check fires at three points: planning_input, review_input,
    # and extract_input.  Mapping to draft (30%) keeps the indicator on the draft
    # step during review startup; the high-water mark ensures later phases do not
    # regress.  Previously mapped to post_alignment (50%), which caused the UI to
    # jump from plan_checkpoint (10%) straight to post_alignment (50%) at the
    # start of the review phase.
    "input_integrity_check": "draft",
    "alignment_threshold_check": "alignment_repair",
    "alignment": "pre_alignment",
    "alignment_cached": "pre_alignment",
    "alignment_recheck_skipped": "alignment_repair",
    "alignment_recheck_cache_bypassed": "alignment_repair",
    "alignment_literal_patch_attempt": "alignment_repair",
    "alignment_literal_patch_result": "alignment_repair",
    "alignment_repair_attempt": "alignment_repair",
    "alignment_repair_edit": "alignment_repair",
    "alignment_repair_candidate_accepted": "alignment_repair",
    "alignment_repair_candidate_rolled_back": "alignment_repair",
    "alignment_repair_gateway_error": "alignment_repair",
    "alignment_missing_main_warn": "alignment_repair",
    "alignment_moderate_pass": "alignment_repair",
    "alignment_after_self_repair": "alignment_repair",
    "alignment_critical_escalation": "alignment_repair",
    "continuity_eval": "pre_alignment",
    "continuity_artifact_repair": "continuity_repair",
    "continuity_repair": "continuity_repair",
    "continuity_repair_gateway_error": "continuity_repair",
    "continuity_repair_internal_error": "continuity_repair",
    "continuity_recheck_gateway_error": "continuity_repair",
    "continuity_recheck_internal_error": "continuity_repair",
    "alignment_after_repair": "continuity_repair",
    "archive_policy_block": "alignment_repair",
    "continuity_eval_after_repair": "continuity_repair",
    "chapter_repair_after_alignment": "pre_alignment",
    "chapter_repair_recheck_skipped": "continuity_repair",
    "chapter_repair_recheck_mode": "continuity_repair",
    "chapter_repair_after_continuity": "continuity_repair",
    "opening_dedup": "post_alignment",
    # Pre-alignment audit steps — CriticAgent runs BEFORE continuity_eval and
    # alignment, so they must use a progress key below alignment (66%).
    "audit_context_preparing": "pre_alignment",
    "audit_context_ready": "pre_alignment",
    "critique_completed": "pre_alignment",
    "continuity_eval_fallback": "pre_alignment",
    "continuity_escalation_note_added": "continuity_repair",
    "continuity_memory_guidance_added": "continuity_repair",
    "pre_repair_alignment_warning": "continuity_repair",
    "quality_check_short_text_warning": "pre_alignment",
    "check_editorial": "pre_alignment",
    "chapter_quality_repair_started": "pre_alignment",
    "chapter_quality_repair_done": "pre_alignment",
    "wave_integrity_residual_after_repair": "pre_alignment",
    "quality_gate": "alignment_repair",
    "quality_gate_regression_detected": "alignment_repair",
    "quality_gate_rollback": "alignment_repair",
    "quality_gate_rollback_applied": "alignment_repair",
    "quality_gate_secondary_repair_requested": "alignment_repair",
    # Post-quality text-polish steps — happen after all alignment/continuity
    # checks complete but before the guard checkpoint decision.
    "best_effort_accepted": "post_alignment",
    "early_stop": "post_alignment",
    "self_repetition_check": "post_alignment",
    "pronoun_check": "post_alignment",
    "pronoun_repair": "post_alignment",
    "mechanical_pronoun_fix": "post_alignment",
    "non_cjk_cleanup": "post_alignment",
    "causal_validation": "post_alignment",
    "causal_repair_start": "post_alignment",
    "causal_repair": "post_alignment",
    "causal_repair_done": "post_alignment",
    "causal_repair_skipped": "post_alignment",
    "causal_validation_gateway_error": "post_alignment",
    "causal_validation_internal_error": "post_alignment",
    "causal_validation_warning": "post_alignment",
    "causal_repair_recheck": "post_alignment",
    "causal_repair_attempt": "post_alignment",
    "causal_repair_edit": "post_alignment",
    "causal_repair_gateway_error": "post_alignment",
    "causal_repair_internal_error": "post_alignment",
    "causal_recheck_gateway_error": "post_alignment",
    "causal_recheck_internal_error": "post_alignment",
    "causal_memory_guidance_added": "post_alignment",
    "causal_repair_memory_results_recorded": "post_alignment",
    "semantic_drift_detected": "post_alignment",
    "causal_issue_ledger": "post_alignment",
    "causal_repair_rollback": "post_alignment",
    "causal_repair_stagnated": "post_alignment",
    "causal_repair_stagnation_detected": "post_alignment",
    "causal_best_effort_accepted": "post_alignment",
    "causal_eval_after_polish": "polish_reextract_canon",
    "continuity_eval_after_text_change": "polish_reextract_canon",
    "causal_post_repair_regression_start": "post_alignment",
    "causal_post_repair_regression_done": "post_alignment",
    "causal_post_repair_regression_failed": "post_alignment",
    "continuity_eval_after_polish": "polish_reextract_canon",
    "quality_reports_refresh_after_text_change": "polish_reextract_canon",
    "alignment_refresh_failed_fallback": "polish_reextract_canon",
    "continuity_refresh_failed_fallback": "polish_reextract_canon",
    "causal_refresh_failed_fallback": "polish_reextract_canon",
    "alignment_refresh_failed_blocking": "polish_reextract_canon",
    "continuity_refresh_failed_blocking": "polish_reextract_canon",
    "causal_refresh_failed_blocking": "polish_reextract_canon",
    "reading_power_refresh_after_text_change_failed_blocking": "polish_reextract_canon",
    "alignment_refresh_gather_exception": "polish_reextract_canon",
    "continuity_refresh_gather_exception": "polish_reextract_canon",
    "causal_refresh_gather_exception": "polish_reextract_canon",
    "continuity_repair_effect": "continuity_repair",
    "continuity_repair_force_patch_only": "continuity_repair",
    "continuity_repair_memory_results_recorded": "continuity_repair",
    "continuity_repair_rollback_continue": "continuity_repair",
    "continuity_repair_rollback_limit_reached": "continuity_repair",
    "continuity_repair_stagnated": "continuity_repair",
    "cross_dimension_causal_reduced": "post_alignment",
    "cross_dimension_causal_skipped": "post_alignment",
    "cross_dimension_regression": "post_alignment",
    "cross_dimension_rp_skipped": "post_alignment",
    "guard_constraint_compliance_check": "post_alignment",
    "polish_triggered": "post_guard_repair",
    "post_polish_reaudit_skipped": "polish_reextract_canon",
    "post_polish_reaudit_warning": "polish_reextract_canon",
    "progressive_repair_narrowed": "post_alignment",
    "reading_power_best_effort_accepted": "post_alignment",
    "reading_power_memory_issues_indexed": "post_alignment",
    "reading_power_memory_guidance_added": "post_alignment",
    "reading_power_repair_memory_results_recorded": "post_alignment",
    "reading_power_prerepair_eval": "post_alignment",
    "reading_power_repair_tickets_loaded": "post_alignment",
    "reading_power_repair_skipped": "post_alignment",
    "reading_power_repair_no_op": "post_alignment",
    "reading_power_repair_gateway_error": "post_alignment",
    "reading_power_repair_internal_error": "post_alignment",
    "reading_power_change_budget_exceeded": "post_alignment",
    "reading_power_recheck_gateway_error": "post_alignment",
    "reading_power_recheck_internal_error": "post_alignment",
    "reading_power_repair_recheck": "post_alignment",
    "reading_power_issue_ledger": "post_alignment",
    "reading_power_repair_rollback": "post_alignment",
    "reading_power_repair_early_exit": "post_alignment",
    "reading_power_repair_warning": "post_alignment",
    "reading_power_post_repair_checks_start": "post_alignment",
    "reading_power_post_repair_checks_done": "post_alignment",
    "reading_power_post_repair_checks_failed": "post_alignment",
    "reading_power_post_repair_causal_done": "post_alignment",
    "reading_power_post_repair_causal_failed": "post_alignment",
    "reading_power_eval": "post_alignment",
    "reading_power_eval_warning": "post_alignment",
    "reading_power_eval_after_repair": "post_alignment",
    "reading_power_final_eval": "post_alignment",
    "reading_power_repair_loop_complete": "post_alignment",
    "reading_power_repair_stagnated": "post_alignment",
    "humanize_scan": "post_alignment",
    "humanize_revision_diff": "post_alignment",
    "repair_metrics": "post_alignment",
    "reading_power_critical": "evaluate",
    "reading_power_eval_fallback": "evaluate",
    "reading_power_next_chapter_constraints": "volume_audit",
    "reading_power_trend": "evaluate",
    "reading_power_warning": "evaluate",
    "reading_power_window_alert": "evaluate",
    "reading_power_window_init_failed": "evaluate",
    "repair_dimension_skipped_total_cap": "post_alignment",
    "repair_prescreen_failed": "post_alignment",
    "repair_rollback": "post_alignment",
    "repair_suggestion_warnings": "post_alignment",
    "revelation_density_warning": "post_alignment",
    "word_count_warning": "post_alignment",
    "word_count_archive_gate": "post_guard_repair",
    "word_count_archive_gate_skipped": "post_guard_repair",
    "word_count_restructure_applied": "post_guard_repair",
    "word_count_restructure_guard_verify": "post_guard_repair",
    "post_repair_checks_skipped": "post_alignment",
    "change_budget_exceeded": "post_alignment",
    "continuity_issue_ledger": "continuity_repair",
    "continuity_repair_rollback": "continuity_repair",
    "anchor_recalibration": "continuity_repair",
    "post_repair_chapter_repair_skipped_moderate_change": "post_alignment",
    "extract_canon_start": "guard_checkpoint",
    "extract_canon": "guard_checkpoint",
    "extract_canon_normalized": "guard_checkpoint",
    "candidate_state_deltas": "persist",
    "state_delta_adjudication": "persist",
    "state_adjudication_rescue": "persist",
    "contract_coverage_report": "persist",
    "state_adjudication_pre_block_recheck": "persist",
    "final_state_adjudication": "persist",
    "state_adjudication_skipped": "persist",
    "state_adjudication_non_blocking_repair_remaining": "persist",
    "repair_adjudicated_issue": "persist",
    "contract_execution_audit": "persist",
    "contract_execution_audit_skipped": "persist",
    "contract_execution_repair_start": "post_guard_repair",
    "contract_execution_repair_skipped": "post_guard_repair",
    "contract_execution_repair_complete": "post_guard_repair",
    "contract_execution_repair_checkpoint": "guard_checkpoint",
    "progression_ledger_updated": "persist",
    "state_ledger_prepared": "persist",
    "state_ledger_written": "persist",
    "kernel_persist_deferred": "persist",
    "creative_report": "guard_checkpoint",
    "consistency_warnings": "guard_checkpoint",
    "legacy_canon_validation_advisory": "guard_checkpoint",
    "canon_extract_retry": "guard_checkpoint",
    "canon_extract_retry_ok": "guard_checkpoint",
    "evaluate": "evaluate",
    "guard_checkpoint": "guard_checkpoint",
    "alignment_repair_complete": "post_guard_repair",
    "guard_compliance_recheck": "post_guard_repair",
    "guard_ticket_alignment_best_version_restored": "post_guard_repair",
    "guard_ticket_alignment_retry_checkpoint": "post_guard_repair",
    "guard_ticket_repair_complete": "post_guard_repair",
    "polish_reextract_canon": "polish_reextract_canon",
    "persist": "persist",
    "memory_updated": "memory_updated",
    # Memory context diagnostic events — map to their actual pipeline stages to
    # avoid being caught by the "memory_": "persist" prefix below.  Without these,
    # memory_planning_context and memory_draft_context would incorrectly display
    # as "正文落盘" progress.
    "memory_planning_context": "plan_checkpoint",
    "memory_draft_context": "draft",
    "memory_finalize_context": "evaluate",
    "memory_": "memory_updated",
    # Guard finalization option IDs — emitted as the first step event so the
    # progress bar immediately jumps to "归档决策" (88%) instead of staying at 3%.
    "accept_and_finalize": "guard_checkpoint",
    "apply_repairs_and_finalize": "guard_checkpoint",
    "adjust_outline_and_finalize": "guard_checkpoint",
    # Explicit repair-phase marker — emitted at start of apply_repairs_and_finalize
    "post_guard_repair_start": "post_guard_repair",
    # chapter_compact fires inside persist_results (text archive phase), not the
    # repair phase.  Map to "persist" so:
    # • accept_and_finalize: bar goes directly to 正文落盘, dot 5 (归档前修复) never
    #   lights up when no repairs were actually applied.
    # • apply_repairs_and_finalize: repairs kept dot 5 lit; chapter_compact then
    #   advances to dot 6 (正文落盘) as expected.
    "chapter_compact": "persist",
    "consistency_replan": "plan_checkpoint",
    "downstream_invalidated": "persist",
    "memory_invalidated": "memory_updated",
    "macro_guard_audit": "volume_audit",
    "macro_guard_audit_failed": "volume_audit",
    "strand_weave_recorded": "volume_audit",
    "style_trend_skipped": "evaluate",
    "volume_audit": "volume_audit",
    "volume_audit_failed": "volume_audit",
    "volume_audit_completed": "volume_audit",
    "volume_audit_issues_persisted": "volume_audit",
    "volume_audit_retry": "volume_audit",
    "volume_data_archive_failed": "volume_audit",
    "volume_data_archived": "volume_audit",
    "auto_register_character_done": "memory_updated",
    "auto_register_character_start": "memory_updated",
    "element_progress_failed": "memory_updated",
    "element_progress_scheduled": "memory_updated",
    "element_progress_updated": "memory_updated",
    "enrich_introduced_done": "memory_updated",
    "enrich_introduced_failed": "memory_updated",
    # Guard finalize repair-phase events (from resolve_guard_checkpoint)
    "continuity_recheck_after_repair": "post_guard_repair",
    "step_2b_continuity_repair": "post_guard_repair",
    "prompt_artifact_cleanup": "post_guard_repair",
    "prompt_artifact_warning": "post_guard_repair",
    "polish": "post_guard_repair",
    "polish_start": "post_guard_repair",
    # Dimension prefixes keep newly added checker/repair diagnostics visible
    # without forcing every diagnostic event into a bespoke task-flow node.
    "causal_": "post_alignment",
    "continuity_": "continuity_repair",
    "cross_dimension_": "post_alignment",
    "humanize_": "post_alignment",
    "quality_gate_": "alignment_repair",
    "reading_power_": "post_alignment",
    "repair_": "post_alignment",
    "guard_": "post_guard_repair",
    "word_count_": "post_guard_repair",
    "macro_guard_": "volume_audit",
}

_RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP: dict[str, str] = {
    # In the finalize-only job these checks run after the archive decision and
    # before the explicit persist marker.  Keep them on the visible "状态提取"
    # milestone instead of resolving to the phase-1 review-only nodes.
    "alignment": "polish_reextract_canon",
    "alignment_cached": "polish_reextract_canon",
    "continuity_eval": "polish_reextract_canon",
    "continuity_eval_fallback": "polish_reextract_canon",
    "causal_validation": "polish_reextract_canon",
    "causal_validation_gateway_error": "polish_reextract_canon",
    "causal_validation_internal_error": "polish_reextract_canon",
    "causal_validation_warning": "polish_reextract_canon",
    "reading_power_eval": "polish_reextract_canon",
    "reading_power_eval_warning": "polish_reextract_canon",
    "reading_power_eval_after_repair": "polish_reextract_canon",
    "reading_power_final_eval": "polish_reextract_canon",
    "extract_canon_start": "polish_reextract_canon",
    "extract_canon": "polish_reextract_canon",
    "extract_canon_normalized": "polish_reextract_canon",
    "creative_report": "polish_reextract_canon",
    "consistency_warnings": "polish_reextract_canon",
    "legacy_canon_validation_advisory": "polish_reextract_canon",
    "canon_extract_retry": "polish_reextract_canon",
    "canon_extract_retry_ok": "polish_reextract_canon",
    "candidate_state_deltas": "polish_reextract_canon",
    "state_delta_adjudication": "polish_reextract_canon",
    "contract_coverage_report": "polish_reextract_canon",
    "state_adjudication_pre_block_recheck": "polish_reextract_canon",
    "final_state_adjudication": "polish_reextract_canon",
    "state_adjudication_skipped": "polish_reextract_canon",
    "state_adjudication_non_blocking_repair_remaining": "polish_reextract_canon",
    "repair_adjudicated_issue": "polish_reextract_canon",
    "state_ledger_prepared": "polish_reextract_canon",
    "state_ledger_written": "polish_reextract_canon",
    # In finalize-only flow this check belongs to the archive decision milestone;
    # the phase-1 checkpoint map would resolve it to hidden post_alignment.
    "guard_constraint_compliance_check": "guard_checkpoint",
}

# Progress-only sub-stages inside "post_alignment" for resolve checkpoint jobs.
# These keys are intentionally *not* exposed in summary_steps/resolve_step_key
# to keep the visible workflow concise while still allowing smoother percentages.
_RESOLVE_CHECKPOINT_PROGRESS_STEP_MAP: dict[str, str] = {
    "wave": "draft_wave",
    "continuity_eval": "pre_alignment_reports",
    "continuity_eval_fallback": "pre_alignment_reports",
    "alignment": "pre_alignment_reports",
    "alignment_cached": "pre_alignment_reports",
    "chapter_repair_after_alignment": "pre_alignment_reports",
    "check_editorial": "pre_alignment_reports",
    "chapter_quality_repair_started": "pre_alignment_reports",
    "chapter_quality_repair_done": "pre_alignment_reports",
    "wave_integrity_residual_after_repair": "pre_alignment_reports",
    "continuity_repair": "continuity_repair",
    "continuity_artifact_repair": "continuity_repair",
    "continuity_repair_effect": "continuity_repair",
    "continuity_repair_force_patch_only": "continuity_repair",
    "continuity_repair_memory_results_recorded": "continuity_repair",
    "continuity_repair_rollback_continue": "continuity_repair",
    "continuity_repair_rollback_limit_reached": "continuity_repair",
    "continuity_repair_stagnated": "continuity_repair",
    "continuity_issue_ledger": "continuity_repair",
    "continuity_repair_rollback": "continuity_repair",
    "continuity_eval_after_repair": "continuity_repair_recheck",
    "continuity_recheck_gateway_error": "continuity_repair_recheck",
    "continuity_recheck_internal_error": "continuity_repair_recheck",
    "alignment_after_repair": "continuity_repair_recheck",
    "chapter_repair_after_continuity": "continuity_repair_recheck",
    "chapter_repair_recheck_skipped": "continuity_repair_recheck",
    "chapter_repair_recheck_mode": "continuity_repair_recheck",
    "alignment_threshold_check": "alignment_repair",
    "alignment_repair_attempt": "alignment_repair",
    "alignment_repair_edit": "alignment_repair_edit",
    "alignment_repair_candidate_accepted": "alignment_repair_recheck",
    "alignment_repair_candidate_rolled_back": "alignment_repair_recheck",
    "alignment_recheck_skipped": "alignment_repair_recheck",
    "alignment_recheck_cache_bypassed": "alignment_repair_recheck",
    "alignment_literal_patch_attempt": "alignment_repair",
    "alignment_literal_patch_result": "alignment_repair_recheck",
    "alignment_after_self_repair": "alignment_repair_recheck",
    "alignment_missing_main_warn": "alignment_repair_recheck",
    "alignment_moderate_pass": "alignment_repair_recheck",
    "self_repetition_check": "post_alignment_dedup",
    "pronoun_check": "post_alignment_dedup",
    "pronoun_repair": "post_alignment_dedup",
    "mechanical_pronoun_fix": "post_alignment_dedup",
    "non_cjk_cleanup": "post_alignment_dedup",
    "causal_validation": "post_alignment_causal",
    "causal_repair_start": "post_alignment_causal",
    "causal_repair": "post_alignment_causal",
    "causal_repair_done": "post_alignment_causal",
    "causal_repair_skipped": "post_alignment_causal",
    "causal_validation_gateway_error": "post_alignment_causal",
    "causal_validation_internal_error": "post_alignment_causal",
    "causal_validation_warning": "post_alignment_causal",
    "causal_repair_recheck": "post_alignment_causal",
    "causal_repair_attempt": "post_alignment_causal",
    "causal_repair_edit": "post_alignment_causal",
    "causal_repair_gateway_error": "post_alignment_causal",
    "causal_repair_internal_error": "post_alignment_causal",
    "causal_recheck_gateway_error": "post_alignment_causal",
    "causal_recheck_internal_error": "post_alignment_causal",
    "causal_memory_guidance_added": "post_alignment_causal",
    "causal_repair_memory_results_recorded": "post_alignment_causal",
    "semantic_drift_detected": "post_alignment_causal",
    "causal_issue_ledger": "post_alignment_causal",
    "causal_repair_rollback": "post_alignment_causal",
    "causal_post_repair_regression_start": "post_alignment_causal",
    "causal_post_repair_regression_done": "post_alignment_causal",
    "causal_post_repair_regression_failed": "post_alignment_causal",
    "reading_power_memory_issues_indexed": "post_alignment_rp",
    "reading_power_memory_guidance_added": "post_alignment_rp",
    "reading_power_repair_memory_results_recorded": "post_alignment_rp",
    "reading_power_prerepair_eval": "post_alignment_rp",
    "reading_power_repair_tickets_loaded": "post_alignment_rp",
    "reading_power_repair_skipped": "post_alignment_rp",
    "reading_power_repair_no_op": "post_alignment_rp",
    "reading_power_repair_gateway_error": "post_alignment_rp",
    "reading_power_repair_internal_error": "post_alignment_rp",
    "reading_power_change_budget_exceeded": "post_alignment_rp",
    "reading_power_recheck_gateway_error": "post_alignment_rp",
    "reading_power_recheck_internal_error": "post_alignment_rp",
    "reading_power_repair_recheck": "post_alignment_rp",
    "reading_power_issue_ledger": "post_alignment_rp",
    "reading_power_repair_rollback": "post_alignment_rp",
    "reading_power_repair_early_exit": "post_alignment_rp",
    "reading_power_repair_warning": "post_alignment_rp",
    "reading_power_post_repair_checks_start": "post_alignment_rp",
    "reading_power_post_repair_checks_done": "post_alignment_rp",
    "reading_power_post_repair_checks_failed": "post_alignment_rp",
    "reading_power_eval": "post_alignment_rp",
    "reading_power_eval_warning": "post_alignment_rp",
    "reading_power_eval_after_repair": "post_alignment_rp_final",
    "reading_power_final_eval": "post_alignment_rp_final",
    "reading_power_repair_loop_complete": "post_alignment_rp_final",
    "repair_metrics": "post_alignment_rp_final",
    "word_count_warning": "post_alignment_rp_final",
    "post_repair_checks_skipped": "post_alignment_rp_final",
    "change_budget_exceeded": "post_alignment_rp_final",
    "humanize_scan": "post_alignment_humanize",
    "humanize_revision_diff": "post_alignment_humanize",
}

_INIT_LONG_STEP_ALIASES: dict[str, str] = {
    "init_foundation_starting": "profile_style",
    "init_resume_rollback": "init_resume_rollback",
    "init_story_bible_resumed": "init_story_bible",
    "init_web_research_start": "init_web_research",
    "init_web_research_skipped": "init_web_research",
    "init_web_research_failed": "init_web_research",
    "init_web_research_resumed": "init_web_research",
    "init_research_dossier": "init_web_research",
    "init_research_dossier_resumed": "init_web_research",
    "init_research_dossier_failed": "init_web_research",
    "outline_research_grounding": "init_web_research",
    "outline_research_grounding_resumed": "init_web_research",
    "outline_research_grounding_failed": "init_web_research",
    "init_character_bible_starting": "init_character_bible",
    "init_character_bible_resumed": "init_character_bible",
    "init_character_bible_json_repaired": "init_character_bible",
    "init_character_bible_repaired": "init_character_bible",
    "init_character_bible_single_character_warning": "init_character_bible",
    "init_character_system_resumed": "init_character_system",
    "init_knowledge_boundaries": "init_character_system",
    "init_knowledge_boundaries_resumed": "init_character_system",
    "init_knowledge_boundaries_failed": "init_character_system",
    "init_param_drift": "spec",
    "init_project_reset": "spec",
    "profile_style_resumed": "profile_style",
    "profile_style_skipped": "profile_style",
    "profile_style_failed": "profile_style",
    "init_entity_registry_failed": "init_entity_registry",
    "init_entity_graph_resumed": "init_entity_graph",
    "creative_director_packet_resumed": "creative_director_packet",
    "init_coherence_profile_resumed": "build_init_coherence_profile",
    "derive_init_coherence_profile_start": "build_init_coherence_profile",
    "derive_init_coherence_profile_failed": "build_init_coherence_profile",
    "refine_init_coherence_profile_start": "build_init_coherence_profile",
    "refine_init_coherence_profile_failed": "build_init_coherence_profile",
    "build_init_coherence_profile_start": "build_init_coherence_profile",
    "build_init_coherence_profile_failed": "build_init_coherence_profile",
    "extract_init_coherence_claims_start": "extract_init_coherence_claims",
    "extract_init_coherence_claims_failed": "extract_init_coherence_claims",
    "retrieve_init_conflict_candidates_start": "retrieve_init_conflict_candidates",
    "retrieve_init_conflict_candidates_failed": "retrieve_init_conflict_candidates",
    "adjudicate_init_conflict_candidates_start": "adjudicate_init_conflict_candidates",
    "adjudicate_init_conflict_candidates_failed": "adjudicate_init_conflict_candidates",
    "init_narrative_contract_failed": "init_narrative_contract",
    "init_narrative_contract_resumed": "init_narrative_contract",
    "derive_editorial_contract_resumed": "derive_editorial_contract",
    "plan_chapter_contracts_failed": "plan_chapter_contracts",
    "plan_chapter_contracts_resumed": "plan_chapter_contracts",
    "plan_chapter_contracts_split": "plan_chapter_contracts",
    "adjudicate_blueprint_coherence_failed": "adjudicate_blueprint_coherence",
    "repair_init_artifact_patch_failed": "repair_init_artifact_patch",
    "adjudicate_outline_inheritance_failed": "adjudicate_outline_inheritance",
    "adjudicate_contract_coherence_failed": "adjudicate_contract_coherence",
    "adjudicate_contract_coherence_split": "adjudicate_contract_coherence",
    "init_claim_contract_coverage": "init_claim_contract_coverage",
    "init_claim_contract_coverage_failed": "init_claim_contract_coverage",
    "init_source_artifacts": "init_source_artifacts",
    "init_source_artifacts_resume": "init_source_artifacts",
    "init_source_artifacts_repair": "init_source_artifacts",
    "init_source_artifacts_repaired": "init_source_artifacts",
    "init_source_artifacts_repair_gate": "init_source_artifacts",
    "source_artifacts_repair_targeted": "init_source_artifacts",
    "init_repair_reaudit_started": "adjudicate_contract_coherence",
    "init_repair_reaudit_stage": "adjudicate_contract_coherence",
    "init_repair_reaudit_passed": "adjudicate_contract_coherence",
    "init_repair_reaudit_failed": "adjudicate_contract_coherence",
    "plan_blueprint_elements_resumed": "plan_blueprint_elements",
    "plan_blueprint_resumed": "plan_blueprint",
    "plan_blueprint_fragments": "plan_blueprint_fragments",
    "plan_blueprint_validated": "plan_blueprint_validated",
    "plan_blueprint_repaired": "plan_blueprint_repaired",
    "plan_blueprint_subplot_weave_validation": "plan_blueprint_validated",
    "derive_init_coherence_profile": "build_init_coherence_profile",
    "refine_init_coherence_profile": "build_init_coherence_profile",
    "build_init_coherence_profile": "build_init_coherence_profile",
    "extract_init_coherence_claims": "extract_init_coherence_claims",
    "retrieve_init_conflict_candidates": "retrieve_init_conflict_candidates",
    "adjudicate_init_conflict_candidates": "adjudicate_init_conflict_candidates",
    "adjudicate_blueprint_coherence": "adjudicate_blueprint_coherence",
    "repair_init_artifact_patch": "repair_init_artifact_patch",
    "repair_init_artifact_patch_degraded": "repair_init_artifact_patch",
    "repair_init_artifact_targets": "repair_init_artifact_patch",
    "repair_init_artifact_patch_no_effect": "repair_init_artifact_patch",
    "repair_init_artifact_patch_skipped": "repair_init_artifact_patch",
    "plan_outline_starting": "plan_outline",
    "plan_outline_resumed": "plan_outline",
    "plan_outline_cache_rejected": "plan_outline",
    "plan_outline_reveal_guard_manifest_repaired": "plan_outline",
    "plan_outline_resume_rejected": "plan_outline",
    "plan_outline_polish": "plan_outline",
    "plan_outline_polish_resumed": "plan_outline",
    "plan_outline_polish_skipped": "plan_outline",
    "outline_title_repair_applied": "plan_outline",
    "init_creative_refinement": "adjudicate_blueprint_coherence",
    "init_creative_refinement_resumed": "adjudicate_blueprint_coherence",
    "init_creative_refinement_skipped": "adjudicate_blueprint_coherence",
    "init_coherence_report_resumed": "adjudicate_blueprint_coherence",
    "adjudicate_outline_inheritance": "adjudicate_outline_inheritance",
    "init_readiness": "init_readiness",
    "canon_resumed": "canon_state",
    "canon_init": "canon_state",
    "story_kernel_build_failed": "canon_state",
    "story_kernel_gate": "canon_state",
    "story_kernel_gate_blocked": "canon_state",
    "story_kernel_saved": "canon_state",
    "started": "",
}

_INIT_LONG_STEP_PREFIX_ALIASES: tuple[tuple[str, str], ...] = (
    ("plan_blueprint_overview", "plan_blueprint"),
    ("plan_blueprint_phases", "plan_blueprint"),
    ("plan_blueprint_turning_points", "plan_blueprint"),
    ("plan_blueprint_character_arcs", "plan_blueprint"),
    ("plan_blueprint_subplots", "plan_blueprint"),
    ("plan_blueprint_suspense", "plan_blueprint"),
    ("plan_blueprint_ending", "plan_blueprint"),
    ("plan_outline_batch_", "plan_outline"),
    ("plan_outline_continue_", "plan_outline"),
    ("plan_outline_repair_", "plan_outline"),
    ("plan_outline_retry_", "plan_outline"),
    ("plan_chapter_contracts_batch_", "plan_chapter_contracts"),
    ("adjudicate_contract_coherence_batch_", "adjudicate_contract_coherence"),
)

_COMMON_STEP_ALIASES: dict[str, str] = {
    "started": "",
}


def _resolve_prefixed_alias(
    raw_key: str,
    aliases: tuple[tuple[str, str], ...],
) -> str | None:
    for prefix, mapped in aliases:
        if raw_key.startswith(prefix):
            return mapped
    return None


def _normalize_progress_step(kind: str, raw_key: str) -> str:
    """Normalize step aliases for percentage calculation only."""
    if raw_key in _COMMON_STEP_ALIASES:
        return _COMMON_STEP_ALIASES[raw_key]
    if kind in {"tts_synthesize", "tts_full_pipeline", "tts_post_archive"}:
        if raw_key in _TTS_STEP_ALIASES:
            return _TTS_STEP_ALIASES[raw_key]
        mapped_tts = _resolve_prefixed_alias(raw_key, _TTS_STEP_PREFIX_ALIASES)
        if mapped_tts is not None:
            return mapped_tts
    if kind == "run_chapter":
        if raw_key in {"memory_planning_context"}:
            return "state_packet"
        if raw_key in {"memory_draft_context"}:
            return "bridge"
        if raw_key == "memory_finalize_context":
            return "extract_canon"
        if raw_key == "memory_updated" or raw_key.startswith("memory_"):
            return "memory_updated"
        if raw_key in {"edit_budget_trimmed", "edit_early_stop"}:
            return "wave"
        if raw_key.startswith("edit_"):
            return "wave"
        if raw_key not in _PROGRESS_CHAPTER:
            if raw_key in _RUN_CHAPTER_STEP_MAP:
                return _RUN_CHAPTER_STEP_MAP[raw_key]
            for prefix, mapped in _RUN_CHAPTER_PREFIX_GROUPS:
                if raw_key.startswith(prefix):
                    return mapped
    # prepare_chapter: all raw keys have direct entries in _PROGRESS_PREPARE_CHAPTER,
    # so no normalization needed here — doing so would make context_compress (22%) and
    # plan (66%) entries unreachable since they'd collapse to their parent keys.
    if kind == "resolve_chapter_checkpoint_finalize":
        if raw_key in _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP:
            return _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP[raw_key]
        for prefix, mapped in _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP.items():
            if prefix.endswith("_") and raw_key.startswith(prefix):
                return mapped
    if kind in {"resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"}:
        if raw_key in _RESOLVE_CHECKPOINT_PROGRESS_STEP_MAP:
            return _RESOLVE_CHECKPOINT_PROGRESS_STEP_MAP[raw_key]
        for prefix, mapped in _RESOLVE_CHECKPOINT_PROGRESS_STEP_MAP.items():
            if prefix.endswith("_") and raw_key.startswith(prefix):
                return mapped
        if raw_key in _RESOLVE_CHECKPOINT_STEP_MAP:
            return _RESOLVE_CHECKPOINT_STEP_MAP[raw_key]
        for prefix, mapped in _RESOLVE_CHECKPOINT_STEP_MAP.items():
            if prefix.endswith("_") and raw_key.startswith(prefix):
                return mapped
    if kind == "run_short" and raw_key in _SHORT_STEP_ALIASES:
        return _SHORT_STEP_ALIASES[raw_key]
    if kind == "run_short":
        mapped_alias = _resolve_prefixed_alias(raw_key, _SHORT_STEP_PREFIX_ALIASES)
        if mapped_alias is not None:
            return mapped_alias
    if kind == "init_long":
        if raw_key in {
            "outline_research_grounding",
            "outline_research_grounding_resumed",
            "outline_research_grounding_failed",
        }:
            return raw_key
        if raw_key in {
            "plan_outline_starting",
            "plan_outline_cache_rejected",
            "plan_outline_resume_rejected",
        }:
            return "plan_outline_starting"
        if raw_key in _INIT_LONG_STEP_ALIASES:
            return _INIT_LONG_STEP_ALIASES[raw_key]
        init_mapped_alias: str | None = _resolve_prefixed_alias(
            raw_key,
            _INIT_LONG_STEP_PREFIX_ALIASES,
        )
        if init_mapped_alias is not None:
            return init_mapped_alias
    return raw_key


_PROGRESS_SHORT: dict[str, int] = {
    "short_resume_rollback": 6,
    "spec": 6,
    "spec_resumed": 6,
    "chapter_research": 9,
    "short_blueprint_elements": 12,
    "short_blueprint_elements_resumed": 12,
    "short_blueprint": 18,
    "short_profile_style": 23,
    "beats": 25,
    "short_execution_plan": 32,
    "short_segment_plan": 38,
    "draft": 45,
    "edit_1": 55,
    "edit_2": 63,
    "edit_3": 70,
    "edit_4": 76,
    "early_stop": 78,
    "short_completeness_check": 80,
    "evaluate": 85,
    "creative_summary": 95,
}

_PROGRESS_INIT_LONG: dict[str, int] = {
    "init_resume_rollback": 8,
    "spec": 8,
    "spec_resumed": 8,
    "init_web_research": 12,
    "init_story_bible": 20,
    "init_upstream_health": 22,
    "plan_blueprint_elements": 20,
    "plan_blueprint_elements_resumed": 20,
    "init_character_bible": 38,
    "init_character_system": 48,
    "profile_style": 64,
    "profile_style_resumed": 64,
    "init_entity_registry": 66,
    "init_entity_graph": 67,
    "creative_director_packet": 70,
    "plan_blueprint": 76,
    "plan_blueprint_validated": 77,
    "plan_blueprint_repaired": 79,
    "plan_blueprint_subplot_weave_validation": 80,
    "build_init_coherence_profile": 79,
    "extract_init_coherence_claims": 79,
    "retrieve_init_conflict_candidates": 79,
    "adjudicate_init_conflict_candidates": 79,
    "adjudicate_blueprint_coherence": 79,
    "repair_init_artifact_patch": 80,
    "derive_editorial_contract": 80,
    "derive_editorial_contract_resumed": 80,
    "plan_blueprint_fragments": 81,
    "plan_chapter_design_matrix": 82,
    "plan_outline_starting": 82,
    "plan_outline_cache_rejected": 82,
    "plan_outline_reveal_guard_manifest_repaired": 82,
    "plan_outline_resume_rejected": 82,
    "plan_outline": 92,
    "adjudicate_outline_inheritance": 93,
    "outline_research_grounding": 93,
    "outline_research_grounding_resumed": 93,
    "outline_research_grounding_failed": 93,
    "init_narrative_contract": 94,
    "init_narrative_contract_failed": 94,
    "plan_chapter_contracts": 95,
    "plan_chapter_contracts_failed": 95,
    "plan_chapter_contracts_split": 95,
    "adjudicate_contract_coherence": 96,
    "adjudicate_contract_coherence_failed": 96,
    "adjudicate_contract_coherence_split": 96,
    "init_claim_contract_coverage": 97,
    "init_claim_contract_coverage_failed": 97,
    "init_source_artifacts": 98,
    "init_readiness": 98,
    "canon_state": 99,
}

_PROGRESS_CHAPTER: dict[str, int] = {
    "state_packet": 5,
    "chapter_research": 12,
    "context_compress": 10,
    "adjudicate_character_introduction_start": 11,
    "adjudicate_character_introduction_done": 12,
    "adjudicate_character_introduction_failed": 12,
    "introduce_character_detected": 12,
    "introduce_character_done": 14,
    "introduce_character_failed": 14,
    "input_integrity_check": 5,
    "bridge": 18,
    "plan": 28,
    "draft": 42,
    "edit_budget_trimmed": 47,
    "edit_early_stop": 50,
    "precheck_early_stop": 50,
    "precheck_prompt_leak": 48,
    "prompt_leak_patch_repair_start": 49,
    "prompt_leak_patch_repair_complete": 50,
    "prompt_leak_deterministic_fallback": 50,
    "chapter_repair": 52,
    "wave": 54,
    "alignment_threshold_check": 58,
    "consistency_replan": 59,
    "audit_context_preparing": 59,
    "audit_context_ready": 59,
    "critique_completed": 60,
    "alignment": 60,
    "alignment_cached": 60,
    "alignment_repair_attempt": 62,
    "continuity_eval": 67,
    "continuity_eval_fallback": 67,
    "continuity_artifact_repair": 70,
    "continuity_repair": 73,
    "continuity_repair_gateway_error": 73,
    "continuity_repair_internal_error": 73,
    "continuity_repair_effect": 75,
    "alignment_after_repair": 79,
    "chapter_repair_after_alignment": 79,
    "chapter_repair_recheck_skipped": 80,
    "chapter_repair_recheck_mode": 80,
    "chapter_repair_after_continuity": 81,
    "continuity_eval_after_repair": 83,
    "continuity_recheck_gateway_error": 83,
    "continuity_recheck_internal_error": 83,
    "change_budget_exceeded": 84,
    "continuity_issue_ledger": 84,
    "continuity_repair_rollback": 84,
    "opening_dedup": 85,
    "post_repair_checks_skipped": 86,
    "alignment_repair_edit": 86,
    "alignment_repair_candidate_accepted": 86,
    "alignment_repair_candidate_rolled_back": 86,
    "alignment_after_self_repair": 86,
    "self_repetition_check": 84,
    "pronoun_check": 85,
    "humanize_scan": 90,
    "knowledge_boundary_verification": 90,
    "pronoun_repair": 85,
    "mechanical_pronoun_fix": 85,
    "non_cjk_cleanup": 85,
    "causal_validation": 86,
    "causal_repair_start": 86,
    "causal_repair": 87,
    "causal_repair_done": 87,
    "causal_repair_skipped": 86,
    "causal_validation_gateway_error": 86,
    "causal_validation_internal_error": 86,
    "causal_repair_recheck": 87,
    "causal_recheck_gateway_error": 87,
    "causal_recheck_internal_error": 87,
    "causal_validation_warning": 87,
    "causal_repair_attempt": 87,
    "causal_repair_edit": 87,
    "causal_repair_gateway_error": 87,
    "causal_repair_internal_error": 87,
    "causal_memory_guidance_added": 87,
    "causal_repair_memory_results_recorded": 87,
    "semantic_drift_detected": 87,
    "causal_issue_ledger": 87,
    "causal_repair_rollback": 87,
    "causal_post_repair_regression_start": 87,
    "causal_post_repair_regression_done": 87,
    "causal_post_repair_regression_failed": 87,
    "reading_power_memory_issues_indexed": 88,
    "reading_power_memory_guidance_added": 88,
    "reading_power_repair_memory_results_recorded": 88,
    "reading_power_prerepair_eval": 88,
    "reading_power_repair_tickets_loaded": 88,
    "reading_power_repair_skipped": 88,
    "reading_power_repair_no_op": 88,
    "reading_power_repair_gateway_error": 88,
    "reading_power_repair_internal_error": 88,
    "reading_power_change_budget_exceeded": 88,
    "reading_power_recheck_gateway_error": 88,
    "reading_power_recheck_internal_error": 88,
    "reading_power_repair_recheck": 88,
    "reading_power_issue_ledger": 88,
    "reading_power_repair_rollback": 88,
    "reading_power_repair_early_exit": 88,
    "reading_power_repair_warning": 88,
    "reading_power_post_repair_checks_start": 88,
    "reading_power_post_repair_checks_done": 88,
    "reading_power_post_repair_checks_failed": 88,
    "reading_power_eval_after_repair": 89,
    "reading_power_final_eval": 89,
    "reading_power_repair_loop_complete": 89,
    "repair_metrics": 89,
    "word_count_warning": 87,
    "extract_canon_normalized": 95,
    "extract_canon": 95,
    "creative_report": 92,
    "consistency_warnings": 93,
    "canon_extract_retry": 93,
    "canon_extract_retry_ok": 93,
    "chapter_compact": 95,
    "persist": 97,
    "downstream_invalidated": 97,
    "memory_invalidated": 99,
    "evaluate": 98,
    "volume_audit": 98,
    "volume_audit_failed": 98,
    "volume_compact": 99,
    "enrich_introduced_done": 99,
    "enrich_introduced_failed": 99,
    "memory_updated": 99,
    # Engine-owned visible chapter milestones.  These keys are also used by
    # workflow projections after raw events are normalized, so they must stay
    # monotonic with ``summary_steps("run_chapter")``.
    "opening_guard": 57,
    "alignment_repair": 81,
    "guard_review": 85,
    "reading_power_repair": 89,
    "polish": 92,
    "humanize": 94,
}

_PROGRESS_PREPARE_CHAPTER: dict[str, int] = {
    "state_packet": 14,
    "context_compress": 22,
    "bridge": 44,
    "plan": 66,
    "plan_checkpoint": 82,
    "input_integrity_check": 8,
}

_PROGRESS_RESOLVE_CHECKPOINT: dict[str, int] = {
    # Phase 1: Planning (5-15%)
    "plan_checkpoint": 5,
    "resume_from_progress": 10,
    # Phase 2: Draft generation (20-35%)
    "draft": 25,
    "draft_wave": 35,
    # Phase 3: Quality checks (38-45%)
    "pre_alignment": 40,
    "pre_alignment_reports": 44,
    # Phase 4: Continuity repair (47-52%)
    "continuity_repair": 48,
    "continuity_repair_recheck": 52,
    # Phase 5: Alignment repair (55-60%)
    "alignment_repair": 55,
    "alignment_repair_edit": 58,
    "alignment_repair_recheck": 60,
    # Phase 6: Post-alignment text polish (62-72%)
    "post_alignment": 62,
    "post_alignment_dedup": 63,
    "post_alignment_causal": 65,
    "post_alignment_rp": 67,
    "post_alignment_rp_final": 68,
    "post_alignment_humanize": 70,
    # Phase 7: Guard checkpoint decision (72%)
    "guard_checkpoint": 72,
    # Phase 8: Apply repairs before archiving (78-85%)
    "post_guard_repair": 80,
    # Phase 9: Polish and re-extract canon (88-90%)
    "polish_reextract_canon": 88,
    # Phase 10: Final archiving (92-94%)
    "persist": 92,
    # Phase 10: Quality evaluation (96%)
    "evaluate": 96,
    # Phase 11: Volume audit (97%)
    "volume_audit": 97,
    # Phase 12: Post-archive memory update (99%)
    "memory_updated": 99,
}

_PROGRESS_RESOLVE_CHECKPOINT_FINALIZE: dict[str, int] = {
    # Finalize-only flow (accept/apply-repairs/adjust-outline): starts at the
    # guard checkpoint and skips draft + quality stages already completed in a
    # prior write task.  Progress is re-mapped to the 6 visible steps so the
    # bar advances proportionally within the narrowed scope.
    "guard_checkpoint": 10,
    "post_guard_repair": 30,
    "polish_reextract_canon": 52,
    "persist": 70,
    "evaluate": 82,
    "volume_audit": 92,
    "memory_updated": 99,
}

_STEP_DISPLAY_NAMES: dict[str, str] = {
    "init_story_bible_starting": "世界观生成启动",
    "plan_blueprint_elements_starting": "叙事要素选择启动",
    "init_foundation_starting": "风格、实体与知识边界生成",
    "humanize_start": "拟人化清理启动",
    "humanize_skipped": "拟人化清理已跳过",
    "humanize_rolled_back": "拟人化清理已回滚",
    "future_planning": "后续章节规划",
    "plan_outline_resume_identity_migrated": "大纲恢复实体引用已迁移",
    "plan_outline_resume_identity_rejected": "大纲恢复实体引用校验失败",
    "started": "任务启动中",
    "completed": "已完成",
    "failed": "执行失败",
    "archive_hard_quality_block": "归档硬性质量阻断",
    "book_consistency_legacy_repair_delegated": "全书一致性旧修复入口已委派",
    "chapter_contract_cast_plan_synced": "章节契约角色计划已同步",
    "chapter_contract_item_ids_normalized": "章节契约物品标识已规范化",
    "chapter_publication_pending": "章节发布待完成",
    "chapter_followup_pending": "章节已归档，后续任务待重试",
    "finalization_phase_reused": "终局阶段结果已复用",
    "final_text_hash_verified": "终稿文本指纹已验证",
    "short_adaptive_diagnosis": "短篇自适应修订诊断",
    "short_adaptive_revision_resumed": "短篇自适应修订已恢复",
    "short_adaptive_revision_rollback": "短篇自适应修订已回滚",
    "short_adaptive_revision_round": "短篇自适应修订轮次",
    "chapter_research_start": "章节研究已启动",
    "chapter_research_cache_hit": "章节研究命中缓存",
    "chapter_research_ready": "章节证据包已就绪",
    "chapter_research_skipped": "章节研究已跳过",
    "init_character_system_cache_rejected": "角色系统缓存已拒绝",
    "init_contract_coherence_resumed": "初始化契约一致性已恢复",
    "init_resume_contract_identity_migrated": "恢复契约标识已迁移",
    "init_resume_outline_identity_migrated": "恢复大纲标识已迁移",
    "json_stream_partial_recovery_started": "JSON 流部分恢复已启动",
    "json_stream_partial_recovery_succeeded": "JSON 流部分恢复成功",
    "json_stream_partial_recovery_rejected": "JSON 流部分恢复已拒绝",
    "plan_chapter_contracts_transport_deferred": "章节契约传输已延后",
    "json_stream_fallback_to_route": "流式输出路由回退",
    "outline_polish_start": "大纲润色开始",
    "outline_polish_persisted": "大纲润色结果已保存",
    "tts_script_completeness_failed": "配音脚本完整度未通过",
    "tts_speaker_repair_persist_failed": "说话人修复结果保存失败",
    "tts_style_reference_analysis_start": "参考配音风格分析开始",
    "tts_style_reference_analysis_complete": "参考配音风格分析完成",
    "book_consistency_incremental_plan": "全书一致性增量范围确认",
    "chapter_publication_ready": "章节终稿已发布",
    "post_humanize_semantic_verification": "Humanize 后语义复核",
    "post_humanize_semantic_verification_blocked": "Humanize 后语义复核阻断",
    "post_humanize_semantic_repair_complete": "Humanize 后语义修复完成",
    "post_humanize_semantic_repair_skipped": "Humanize 后语义修复已跳过",
    "post_humanize_state_verification_degraded": "Humanize 后状态复核降级",
    "terminal_humanize_read_only_validation_blocked": "Humanize 最终只读校验阻断",
    "terminal_humanize_rolled_back": "终端 AI 去痕已回滚",
    "retrieve": "检索 Canon 上下文",
    "sync_start": "章节契约同步启动",
    "sync_chapter_loading": "加载章节同步上下文",
    "sync_chapter_llm": "章节契约同步生成",
    "sync_chapter_saving": "保存章节同步结果",
    "sync_marking_stale": "标记章节需要重写",
    "sync_milestone_rebuilding": "重建章节里程碑",
    "sync_scope_required": "需要选择同步范围",
    "sync_llm_error": "章节契约同步模型失败",
    "sync_done": "章节契约同步完成",
    "writing_mode": "写作模式确认",
    "spec": "故事规格确认",
    "spec_resumed": "规格（已缓存）",
    "format_validation_success": "格式校验通过",
    "init_web_research": "资料检索",
    "init_web_research_start": "资料检索启动",
    "init_web_research_skipped": "资料检索已跳过",
    "init_web_research_failed": "资料检索失败（已继续）",
    "init_web_research_resumed": "资料检索（已恢复）",
    "init_research_dossier": "资料分析",
    "init_research_dossier_resumed": "资料分析（已恢复）",
    "init_research_dossier_failed": "资料分析失败（已继续）",
    "outline_research_grounding": "大纲资料校准",
    "outline_research_grounding_resumed": "大纲资料校准（已恢复）",
    "outline_research_grounding_failed": "大纲资料校准失败（已继续）",
    "init_upstream_health": "上游健康门",
    "blueprint_resumed": "叙事蓝图（已恢复）",
    "beats": "节拍结构生成",
    "draft": "草稿生成",
    "draft_prompt_diagnostics": "草稿提示词诊断",
    "wave": "WAVE 场景编织",
    "draft_scene_group": "场景草稿分组",
    "draft_scene": "场景草稿",
    "scene_stitch": "全章拼接",
    "scene_plan_validation": "场景计划验证",
    "early_stop": "质量达标，提前结束",
    "evaluate": "综合评分",
    "creative_summary": "创作分析",
    "short_blueprint_elements": "叙事要素选择",
    "short_blueprint_elements_resumed": "叙事要素选择（已恢复）",
    "short_blueprint": "叙事蓝图",
    "short_resume_rollback": "短篇断点回溯清理",
    "init_story_bible": "世界观设定",
    "init_story_bible_resumed": "世界观设定（已恢复）",
    "init_character_bible": "角色设定",
    "init_character_bible_starting": "角色设定",
    "init_character_bible_resumed": "角色设定（已恢复）",
    "init_character_bible_json_repaired": "角色设定 JSON 已修复",
    "init_character_bible_repaired": "角色设定结构已修复",
    "init_character_bible_single_character_warning": "角色设定疑似单角色输出警告",
    "init_character_system": "角色系统审计",
    "init_character_system_resumed": "角色系统审计（已恢复）",
    "init_resume_anchor": "断点恢复锚点",
    "init_resume_rollback": "断点回溯清理",
    "profile_style": "风格规范生成",
    "profile_style_resumed": "风格规范（已恢复）",
    "profile_style_failed": "风格规范生成失败（已跳过）",
    "init_entity_registry": "实体注册表",
    "init_entity_registry_failed": "实体注册表失败（已兜底）",
    "init_entity_graph": "实体图谱",
    "init_entity_graph_resumed": "实体图谱（已恢复）",
    "creative_director_packet": "创作导演包",
    "creative_director_packet_resumed": "创作导演包（已恢复）",
    "init_creative_candidates": "初始化创意候选生成",
    "init_creative_exploration_fallback": "创意方向探索失败（已安全降级）",
    "creative_direction_selected": "初始化创意方向已选定",
    "init_feature_flag_fallback": "初始化功能开关回退",
    "init_param_drift_requires_confirmation": "初始化参数变更待确认",
    "init_failed_step_retry": "初始化失败步骤重试",
    "init_research_evidence_pack": "初始化研究证据包",
    "spec_user_intent_restored": "用户明确意图已恢复",
    "planning_horizon_advanced": "规划水位已推进",
    "planning_horizon_start": "补齐后续大纲与契约",
    "planning_horizon_queued": "后续规划已排队（章节已归档）",
    "planning_horizon_failed": "后续规划未发布（保留原规划）",
    "prompt_context_optional_trimmed": "提示词可选上下文已裁剪",
    "init_creative_refinement": "初始化创意精修",
    "init_creative_refinement_resumed": "初始化创意精修（已恢复）",
    "init_creative_refinement_skipped": "初始化创意精修已跳过",
    "init_narrative_contract": "LLM 叙事契约",
    "init_narrative_contract_resumed": "LLM 叙事契约（已恢复）",
    "init_narrative_contract_failed": "LLM 叙事契约失败（已跳过）",
    "init_narrative_contract_llm_failed": "LLM 叙事契约调用失败（已跳过）",
    "init_narrative_contract_skipped": "LLM 叙事契约已关闭",
    "plan_chapter_contracts": "章节契约",
    "plan_chapter_contracts_resumed": "章节契约（已恢复）",
    "plan_chapter_contracts_failed": "章节契约失败",
    "plan_chapter_contracts_split": "章节契约批次拆分重试",
    "plan_chapter_contracts_repaired": "章节契约已修复",
    "derive_editorial_contract": "编辑质量契约",
    "derive_editorial_contract_resumed": "编辑质量契约（已恢复）",
    "editorial_contract_repair_retry": "编辑契约修复重试",
    "editorial_contract_repair_stopped": "编辑契约修复停止",
    "tts_metadata_extracted": "TTS 元数据提取",
    "tts_auto_trigger_queued": "自动配音已排队",
    "tts_auto_trigger_started": "自动配音已启动",
    "tts_auto_trigger_skipped": "自动配音已跳过",
    "tts_automation_mode": "配音推进策略确认",
    "tts_manual_prerequisites_ready": "配音前置产物已就绪",
    "tts_narrator_start": "准备旁白声音",
    "tts_narrator_llm_call": "分析旁白声线",
    "tts_narrator_done": "旁白声音已就绪",
    "tts_narrator_profile_ready": "旁白声音画像已就绪",
    "tts_narrator_rule_fallback": "旁白声线已本地降级",
    "narrator_voice_ready": "旁白音色已就绪",
    "narrator_voice_fallback": "旁白音色已降级",
    "tts_voice_team_reused": "复用配音团队",
    "tts_voice_team_narrator_only": "旁白配音团队已就绪",
    "tts_voice_team_confirmed": "配音团队已确认",
    "build_voice_team_start": "开始组建配音团队",
    "build_voice_team_progress": "配音团队组建进度",
    "build_voice_team_done": "配音团队已就绪",
    "voice_team_runtime_check_start": "检查配音运行环境",
    "voice_team_runtime_check_done": "配音运行环境已就绪",
    "voice_catalog_sync_start": "同步音色目录",
    "voice_catalog_sync_done": "音色目录同步完成",
    "voice_semantic_retrieval_start": "检索角色匹配音色",
    "voice_semantic_retrieval_done": "角色音色检索完成",
    "voice_assignment_batch_start": "并行分配角色音色",
    "character_voice_assignment_started": "分配角色音色",
    "character_voice_assigned": "角色音色已分配",
    "character_voice_recast": "角色音色已重配",
    "voice_design_unavailable": "音色设计不可用",
    "voice_design_fallback": "音色设计已降级",
    "voice_library_hit": "命中项目音色库",
    "voice_catalog_hit": "命中可复用音色",
    "voice_library_saved": "音色已保存至项目库",
    "voice_preview_batch_start": "并行生成音色试听",
    "voice_preview_started": "生成音色试听",
    "voice_preview_batch_done": "音色试听生成完成",
    "minimax_voice_activation_batch_start": "并行激活 MiniMax 音色",
    "minimax_voice_activation_started": "激活 MiniMax 音色",
    "minimax_voice_activation_progress": "MiniMax 音色激活进度",
    "minimax_voice_activated": "MiniMax 音色已激活",
    "minimax_voice_activation_failed": "MiniMax 音色激活失败",
    "minimax_voice_activation_batch_done": "MiniMax 音色激活完成",
    "tts_voice_llm_adjudication_start": "音色智能复核",
    "tts_voice_llm_adjudication_done": "音色智能复核完成",
    "tts_voice_llm_adjudication_skipped": "音色智能复核已跳过",
    "tts_script_start": "开始改写配音脚本",
    "tts_script_llm_call": "分析台词与情绪",
    "tts_script_llm_batch_start": "配音脚本分批改写",
    "tts_script_llm_batch_complete": "配音脚本分批完成",
    "tts_script_llm_rejected": "配音脚本模型结果未通过保真检查",
    "tts_script_rule_fallback": "配音脚本已本地降级",
    "tts_script_llm_review_start": "配音脚本专业审校",
    "tts_script_llm_review_complete": "配音脚本审校完成",
    "tts_script_llm_review_skipped": "配音脚本审校已跳过",
    "tts_script_llm_review_unavailable": "配音脚本审校不可用",
    "tts_script_professional_review_complete": "配音脚本专业审校完成",
    "tts_script_segment_adjudication_start": "复核配音脚本分段",
    "tts_script_segment_adjudication_complete": "配音脚本分段复核完成",
    "tts_script_segment_adjudication_unavailable": "配音脚本分段复核不可用",
    "tts_soundscape_auto_designed": "环境声设计已自动补全",
    "tts_script_parsed": "配音脚本已解析",
    "tts_script_done": "配音脚本已就绪",
    "tts_script_reused": "复用配音脚本",
    "tts_spoken_rewrite_batch_start": "口语改写批次开始",
    "tts_spoken_rewrite_batch_failed": "口语改写批次失败",
    "tts_spoken_rewrite_complete": "口语改写完成",
    "tts_synthesis_start": "并行合成音频片段",
    "tts_segment": "音频片段合成",
    "tts_synthesis_complete": "音频片段合成完成",
    "tts_synthesis_reused": "复用已合成音频",
    "tts_alignment_start": "语音时间线对齐",
    "tts_alignment_repair_start": "修复语音对齐",
    "tts_alignment_repair_complete": "语音对齐修复完成",
    "tts_alignment_complete": "语音时间线已对齐",
    "tts_assembly_start": "装配章节音频",
    "tts_assembly_complete": "章节音频装配完成",
    "tts_assembly_partial": "章节音频部分装配",
    "tts_assembly_failed": "章节音频装配失败",
    "tts_quality_complete": "音频质量检查完成",
    "tts_auto_trigger_completed": "自动配音交付完成",
    "tts_auto_trigger_partial": "自动配音可续跑",
    "tts_auto_trigger_failed": "自动配音失败",
    "tts_auto_trigger_retry_scheduled": "自动配音延迟重试已排程",
    "tts_speaker_repair_start": "正在修复未指定角色的对白",
    "tts_speaker_repair_done": "说话人修复完成",
    "tts_speaker_repair_failed": "说话人修复失败",
    "tts_speaker_repair_no_progress": "说话人修复未能解决",
    "alignment_fallback": "对齐检查已降级",
    "alignment_fallback_exempt": "对齐降级豁免已确认",
    "alignment_report_stale_skipped": "跳过过期对齐报告",
    "archive_eval_unavailable_degraded": "归档评估不可用，已降级",
    "archive_quality_refresh_failed_blocking": "归档质量刷新失败，已阻断",
    "archive_quality_report_freshness_block": "归档质量报告过期，已阻断",
    "chapter_quality_eval_fallback": "章节质量评估已降级",
    "claim_semantic_repair_failed": "语义声明修复失败",
    "claim_semantic_repair_requested": "语义声明修复已请求",
    "claim_semantic_repair_retry": "语义声明修复重试",
    "claim_semantic_repair_succeeded": "语义声明修复完成",
    "context_overflow_preserved": "上下文溢出内容已保留",
    "draft_plan_coverage": "草稿计划覆盖检查",
    "format_repair_skipped": "格式修复已跳过",
    "format_retry_budget_held": "格式重试预算已保留",
    "init_claim_contract_coverage_resumed": "声明契约覆盖检查（已恢复）",
    "init_claim_entity_adjudication_cache_hit": "实体声明裁决命中缓存",
    "init_claim_entity_adjudication_degraded": "实体声明裁决已降级",
    "init_narrative_evidence_sync": "叙事证据同步",
    "mutation_refresh_shadow": "变更后影子刷新",
    "plan_chapter_contracts_partial_resumed": "章节契约部分恢复",
    "plan_structure_audit": "章节计划结构审计",
    "polish_auto_trigger_skipped": "自动润色已跳过",
    "prepared_runtime_memory_rehydrate_failed": "运行时记忆恢复失败",
    "prepared_runtime_reading_power_rehydrate_failed": "阅读力状态恢复失败",
    "derive_init_coherence_profile": "初始化一致性画像",
    "derive_init_coherence_profile_failed": "初始化一致性画像失败",
    "refine_init_coherence_profile": "初始化一致性画像精炼",
    "refine_init_coherence_profile_failed": "初始化一致性画像精炼失败",
    "build_init_coherence_profile_start": "初始化一致性画像生成中",
    "build_init_coherence_profile": "初始化一致性画像",
    "build_init_coherence_profile_failed": "初始化一致性画像失败",
    "extract_init_coherence_claims": "初始化一致性 Claims 抽取",
    "extract_init_coherence_claims_failed": "初始化一致性 Claims 抽取失败",
    "retrieve_init_conflict_candidates": "初始化冲突候选检索",
    "retrieve_init_conflict_candidates_failed": "初始化冲突候选检索失败",
    "adjudicate_init_conflict_candidates": "初始化冲突候选裁判",
    "adjudicate_init_conflict_candidates_failed": "初始化冲突候选裁判失败",
    "init_coherence_report_resumed": "初始化一致性报告（已恢复）",
    "adjudicate_blueprint_coherence": "蓝图自洽裁判",
    "adjudicate_blueprint_coherence_failed": "蓝图自洽裁判失败",
    "repair_init_artifact_patch": "初始化局部修复",
    "repair_init_artifact_patch_failed": "初始化局部修复失败",
    "repair_init_artifact_patch_degraded": "初始化局部修复降级",
    "adjudicate_outline_inheritance": "大纲继承裁判",
    "adjudicate_outline_inheritance_failed": "大纲继承裁判失败",
    "adjudicate_contract_coherence": "契约自洽裁判",
    "adjudicate_contract_coherence_failed": "契约自洽裁判失败",
    "adjudicate_contract_coherence_split": "契约自洽裁判拆分重试",
    "plan_blueprint_elements": "叙事要素选择",
    "plan_blueprint_elements_resumed": "叙事要素选择（已恢复）",
    "plan_blueprint_resumed": "叙事蓝图（已恢复）",
    "plan_blueprint_spine": "叙事蓝图：骨架规划",
    "plan_blueprint_spine_resumed": "叙事蓝图：骨架规划（已恢复）",
    "plan_blueprint_fragment_precheck": "叙事蓝图分块预检",
    "plan_blueprint_overview": "叙事蓝图：全书概述",
    "plan_blueprint_overview_resumed": "叙事蓝图：全书概述（已恢复）",
    "plan_blueprint_phases": "叙事蓝图：叙事阶段",
    "plan_blueprint_phases_resumed": "叙事蓝图：叙事阶段（已恢复）",
    "plan_blueprint_turning_points": "叙事蓝图：关键转折",
    "plan_blueprint_turning_points_resumed": "叙事蓝图：关键转折（已恢复）",
    "plan_blueprint_character_arcs": "叙事蓝图：角色弧光",
    "plan_blueprint_character_arcs_resumed": "叙事蓝图：角色弧光（已恢复）",
    "plan_blueprint_subplots": "叙事蓝图：支线规划",
    "plan_blueprint_subplots_resumed": "叙事蓝图：支线规划（已恢复）",
    "plan_blueprint_subplot_matrix": "叙事蓝图：支线执行矩阵",
    "plan_blueprint_suspense": "叙事蓝图：悬念规划",
    "plan_blueprint_suspense_resumed": "叙事蓝图：悬念规划（已恢复）",
    "plan_blueprint_ending": "叙事蓝图：收束策略",
    "plan_blueprint_ending_resumed": "叙事蓝图：收束策略（已恢复）",
    "plan_blueprint_fragments": "叙事蓝图分块",
    "plan_blueprint_validated": "叙事蓝图（验证通过）",
    "plan_blueprint_repaired": "叙事蓝图（已修复）",
    "plan_blueprint_subplot_weave_validation": "叙事蓝图：支线交织验证",
    "plan_blueprint": "叙事蓝图",
    "plan_outline": "章节大纲",
    "plan_chapter_design_matrix": "章节设计矩阵",
    "plan_outline_resumed": "章节大纲（已恢复）",
    "plan_outline_cache_rejected": "章节大纲缓存已失效",
    "plan_outline_reveal_guard_manifest_repaired": "章节大纲缓存已验证并恢复",
    "plan_outline_resume_rejected": "章节大纲断点已失效",
    "plan_outline_polish": "章节大纲润色",
    "plan_outline_polish_resumed": "章节大纲润色（已恢复）",
    "plan_outline_polish_skipped": "章节大纲润色已跳过",
    "outline_title_repair_applied": "章节标题补救已应用",
    "init_claim_contract_coverage": "一致性 Claims 契约覆盖审计",
    "init_claim_contract_coverage_failed": "一致性 Claims 契约覆盖审计失败",
    "init_source_artifacts": "源头 artifact 准入",
    "init_source_artifacts_resume": "源头准入断点恢复",
    "init_source_artifacts_repair": "源头准入修复",
    "init_source_artifacts_repaired": "源头准入修复完成",
    "init_source_artifacts_repair_gate": "源头准入修复裁决",
    "source_artifacts_repair_targeted": "源头准入定向修复",
    "init_repair_reaudit_started": "修复后定向复审开始",
    "init_repair_reaudit_stage": "修复后定向复审",
    "init_repair_reaudit_passed": "修复后定向复审通过",
    "init_repair_reaudit_failed": "修复后定向复审失败",
    "init_readiness": "初始化准入",
    "canon_state": "规范状态初始化",
    "canon_init": "规范状态初始化",
    "canon_resumed": "规范状态（已恢复）",
    "profile_style_skipped": "风格规范（已跳过）",
    "state_packet": "构建章节上下文",
    "context_compress": "压缩上下文",
    "adjudicate_character_introduction_start": "裁决新角色候选",
    "adjudicate_character_introduction_done": "新角色候选裁决完成",
    "adjudicate_character_introduction_failed": "新角色候选裁决失败（已跳过）",
    "character_intro_alias_recorded": "新角色别名已记录",
    "character_intro_pending_exhausted": "新角色候选处理已穷尽",
    "introduce_character_detected": "检测到新出场角色",
    "introduce_character_done": "角色骨架档案生成完成",
    "introduce_character_failed": "角色建档失败（已跳过）",
    "bridge": "生成章节桥接",
    "bridge_prompt_diagnostics": "章节桥接提示词诊断",
    "plan": "生成章节计划",
    "plan_prompt_diagnostics": "章节计划提示词诊断",
    "plan_prompt_diagnostics_retry": "章节计划提示词诊断（重试）",
    "guidance_plan_audit_block": "章节计划审计未通过",
    "chapter_repair": "初步修复",
    "edit_budget_trimmed": "编辑预算收缩",
    "edit_early_exit": "编辑提前退出",
    "edit_early_stop": "编辑提前收束",
    "edit_prompt_diagnostics": "章节编辑提示词诊断",
    "alignment": "大纲对齐检查",
    "continuity_eval": "连贯性检查",
    "guidance_contract_audit": "指导契约审计",
    "knowledge_boundary_verification": "知识边界验证",
    "knowledge_boundary_blocked": "知识边界阻断",
    "check_editorial": "章节编辑契约检查",
    "guidance_report_recheck": "指导报告复查",
    "guidance_report_recheck_failed": "指导报告复查失败",
    "continuity_artifact_repair": "连贯性产物修复",
    "continuity_repair": "连续性修复",
    "alignment_repair": "对齐修复",
    "step_2b_continuity_repair": "连贯性修复",
    "alignment_after_repair": "修复后对齐复查",
    "continuity_eval_after_repair": "修复后连贯性复查",
    "continuity_eval_after_text_change": "文本变更后连贯性复查",
    "continuity_eval_guidance_recheck": "指导报告后连贯性复查",
    "causal_eval_guidance_recheck": "指导报告后因果链复查",
    "continuity_recheck_context": "连贯性复查上下文",
    "continuity_repair_unresolved_after_recheck": "连贯性修复后仍有未解决问题",
    "opening_dedup": "去除开头重复",
    "opening_guard_issues_merged": "开场承接问题已并入修复",
    "pre_alignment": "质量检查",
    "pre_alignment_reports": "质量检查报告",
    "post_alignment": "文本精修",
    "extract_canon": "提取剧情状态",
    "extract_canon_start": "开始提取剧情状态",
    "candidate_state_deltas": "抽取候选状态变化",
    "state_delta_adjudication": "LLM 裁判状态变化",
    "contract_coverage_report": "契约覆盖报告",
    "state_adjudication_pre_block_recheck": "阻断前状态复核",
    "final_state_adjudication": "LLM 最终状态裁判",
    "state_adjudication_skipped": "状态裁判已跳过",
    "state_adjudication_non_blocking_repair_remaining": "状态裁判剩余修复建议",
    "repair_adjudicated_issue": "裁判问题修复",
    "contract_execution_audit": "契约执行审计",
    "contract_execution_audit_skipped": "契约执行审计已跳过",
    "contract_execution_repair_start": "契约执行问题修复开始",
    "contract_execution_repair_skipped": "契约执行问题修复已跳过",
    "contract_execution_repair_complete": "契约执行问题修复完成",
    "contract_execution_repair_checkpoint": "契约执行修复待确认",
    "contract_execution_audit_unrepairable": "契约执行审计存在不可自动修复问题",
    "progression_ledger_updated": "剧情推进账本已更新",
    "state_ledger_prepared": "叙事状态账本待写入",
    "state_ledger_written": "写入叙事状态账本",
    "kernel_persist_deferred": "内核持久化延后（非阻塞）",
    "creative_report": "生成创作报告",
    "chapter_compact": "压缩 Canon 状态",
    "persist": "正文与 Canon 落盘",
    "test": "一致性检查",
    "volume_audit": "卷末审计",
    "volume_audit_failed": "卷末审计失败（已跳过）",
    "volume_compact": "卷末存档压缩",
    "enrich_introduced_done": "新角色档案精化完成",
    "enrich_introduced_failed": "角色档案精化失败（已跳过）",
    "memory_indexing_started": "记忆索引中",
    "memory_episodic_done": "记忆索引完成",
    "memory_indexing_complete": "记忆索引完成",
    "memory_summary_scheduled": "摘要任务排队中",
    "memory_summary_generated": "摘要生成完成",
    "memory_motifs_extracted": "母题任务排队中",
    "memory_motifs_completed": "母题提取完成",
    "critic_report_persisted": "独立评审报告已保存",
    "memory_concurrent_tasks_started": "记忆并发任务执行中",
    "memory_concurrent_tasks_done": "记忆并发任务完成",
    "memory_flush": "记忆异步任务收尾",
    "memory_updated": "归档后记忆保存完成",
    # 阶段记忆上下文诊断事件
    "memory_planning_context": "规划阶段记忆上下文",
    "rewrite_strategy": "重写策略已应用",
    "memory_draft_context": "起草阶段记忆上下文",
    "memory_finalize_context": "收束阶段记忆上下文",
    # P0-1: 记忆补偿和失败事件显示名称
    "memory_gap_compensated": "记忆缺口已补偿",
    "memory_update_failed": "记忆更新失败（已标记待补偿）",
    # 后处理通知事件
    "budget_status": "预算状态通知",
    "task_output_normalized": "模型输出字段已规范化",
    "style_drift_warning": "风格趋势下降警告",
    "plan_checkpoint": "章节方案待确认",
    "regenerate_plan": "重新生成方案",
    "regenerate_plan_with_notes": "根据备注重新生成方案",
    "write_now": "方案已确认，开始生成正文",
    "edit_plan_and_write": "方案调整确认，开始生成正文",
    "input_integrity_check": "输入完整性检查",
    "post_guard_repair_start": "正在应用归档前修复",
    "post_guard_repair": "归档前修复",
    "accept_and_finalize": "归档执行：直接归档",
    "apply_repairs_and_finalize": "归档前修复：修复后归档",
    "adjust_outline_and_finalize": "归档前调整：调整大纲后归档",
    "guard_checkpoint": "归档决策待确认",
    "alignment_threshold_check": "大纲对齐阈值检查",
    "consistency_replan": "一致性未通过，正在重新规划并重写",
    "chapter_repair_recheck_skipped": "修复后复查已跳过",
    "chapter_repair_recheck_mode": "修复后复查模式",
    "alignment_repair_attempt": "大纲对齐修复尝试",
    "alignment_repair_candidate_accepted": "对齐修复候选已接受",
    "alignment_repair_candidate_rolled_back": "对齐修复候选已回滚",
    "alignment_repair_complete": "大纲对齐修复完成",
    "causal_repair_attempt": "因果链修复尝试",
    "causal_repair_edit": "因果链修复编辑",
    "change_budget_exceeded": "修复改动超预算",
    "continuity_issue_ledger": "连贯性问题台账",
    "continuity_repair_rollback": "连贯性修复回滚",
    "semantic_drift_detected": "语义漂移检测",
    "causal_issue_ledger": "因果问题台账",
    "causal_repair_rollback": "因果链修复回滚",
    "causal_post_repair_regression_start": "因果修复后跨维度回归检查",
    "causal_post_repair_regression_done": "因果修复后回归检查完成",
    "resume_from_progress": "从断点恢复（跳过已完成阶段）",
    "word_count_warning": "字数偏差警告",
    "precheck_early_stop": "编辑预检提前收束",
    "extract_canon_normalized": "剧情状态章号校正",
    "consistency_warnings": "一致性警告",
    "legacy_canon_validation_advisory": "旧 Canon 校验提示",
    "canon_extract_retry": "剧情状态提取重试",
    "downstream_invalidated": "下游章节数据已失效",
    "memory_invalidated": "记忆数据已失效",
    "repair_issues": "独立修复完成",
    "reevaluate_start": "准备章节重新评估",
    "reevaluate_chapter": "章节重新评估完成",
    "post_repair_checks_skipped": "跳过修复后复查",
    "post_repair_checks_skipped_minor_change": "跳过修复后复查（轻微变动）",
    "post_repair_precision_patch": "修复后精确补丁",
    "humanize_scan": "AI 去痕扫描",
    "humanize_revision_diff": "AI 去痕修订差异",
    "polish_revision_diff": "精修修订差异",
    "post_polish_reaudit_blocked": "精修后复审阻断",
    "reading_power_hash_mismatch": "追读力报告文本版本不匹配",
    "upstream_revision_changed": "上游正文已变化",
    "mechanical_pronoun_fix": "机械人称修复",
    "non_cjk_cleanup": "清理非中文字符",
    "repair_continuity_start": "准备连贯性修复",
    "repair_continuity": "连贯性修复",
    "continuity_eval_after_repair_start": "修复后连贯性复查",
    "repair_causal_start": "准备因果链修复",
    "repair_causal": "因果链修复完成",
    "causal_eval_after_repair_start": "因果链修复后复查",
    "causal_eval_after_repair": "因果链复查完成",
    "causal_eval_after_repair_warning": "因果链复查失败（已跳过）",
    "polish_start": "准备精修润色",
    "polish": "精修润色",
    "book_consistency_start": "准备全书一致性审计",
    "book_consistency_issue_pool_ready": "问题池锚点已就绪",
    "book_consistency_two_phase_start": "智能漏斗摘要筛查开始",
    "book_consistency_two_phase_summary_reused": "复用智能漏斗摘要筛查",
    "book_consistency_two_phase_summary_done": "智能漏斗摘要筛查完成",
    "book_consistency_two_phase_summary_only": "摘要筛查未发现需深审章节",
    "book_consistency_two_phase_targeted_capped": "智能漏斗目标章节已收敛",
    "book_consistency_two_phase_expanded": "已扩展至全部命中章节",
    "book_consistency_two_phase_targeted_start": "定向全文深审开始",
    "book_consistency_chunk_progress": "全书分批审计推进中",
    "book_consistency": "全书一致性审计",
    "book_consistency_audit_saved": "审计报告已存档",
    "book_consistency_checkpoint_result_reused": "复用审计检查点结果",
    "book_consistency_verify_start": "逐章验证审计问题",
    "book_consistency_verify_progress": "逐章验证审计问题",
    "book_consistency_verify_done": "审计验证完成",
    "book_consistency_repair_start": "全书修复任务排程",
    "book_consistency_repair_classification": "修复任务已分流",
    "book_consistency_repair_progress": "全书逐章修复",
    "book_consistency_repair_review": "修复后复核",
    "book_consistency_post_audit_start": "修复后二次小审计开始",
    "book_consistency_post_audit_done": "修复后二次小审计完成",
    "book_consistency_rollback": "修复失败，已回滚",
    "book_consistency_auto_continue": "继续修复剩余章节",
    "book_consistency_stuck": "续修无新增进展",
    "book_consistency_auto_continue_done": "自动续修结束",
    "global_repair_queue_done": "全书修复队列已完成",
    "book_consistency_report_written": "全书审计报告已归档",
    "book_consistency_repair_report_written": "全书修复报告已归档",
    "book_consistency_backup_created": "全书审计修复备份已创建",
    "book_editorial_audit_start": "准备全书编辑审计",
    "book_editorial_audit": "全书编辑审计",
    "book_editorial_audit_report_written": "全书编辑审计报告已归档",
    "export": "导出",
    # 质量审计阶段
    "audit_context_preparing": "正在准备审计上下文",
    "audit_context_ready": "审计上下文已就绪",
    "critique_completed": "批注评审完成",
    "continuity_eval_fallback": "连贯性检查（降级模式）",
    "alignment_cached": "大纲对齐（已缓存）",
    "chapter_repair_after_alignment": "大纲对齐后修复初稿",
    "continuity_repair_effect": "评估连贯性修复效果",
    "chapter_repair_after_continuity": "连贯性修复后重写章节",
    "self_repetition_check": "检查自我重复",
    "causal_validation": "校验因果逻辑",
    "causal_repair_start": "准备因果链修复",
    "causal_repair": "因果链修复",
    "causal_repair_done": "因果链修复完成",
    "causal_repair_skipped": "因果链修复已跳过",
    "causal_validation_warning": "因果链问题待复核（修复已跳过）",
    "causal_repair_recheck": "因果链修复后再次校验",
    "pronoun_check": "检查人称代词一致性",
    "pronoun_repair": "修复人称代词",
    "alignment_repair_edit": "大纲对齐修复编辑",
    "alignment_after_self_repair": "自修复后对齐复查",
    "alignment_critical_escalation": "对齐临界分数升级",
    # 收尾阶段
    "canon_extract_retry_ok": "剧情状态提取重试成功",
    "precheck_prompt_leak": "检测提示词泄漏",
    "prompt_leak_patch_repair_start": "提示词泄漏补丁修复",
    "prompt_leak_patch_repair_complete": "提示词泄漏修复完成",
    "prompt_leak_deterministic_fallback": "提示词泄漏兜底清理",
    # 短篇恢复步骤
    "beats_resumed": "节拍结构（已恢复）",
    "short_execution_plan": "短篇执行方案",
    "draft_resumed": "初稿（已恢复）",
    "short_completeness_check": "完整性检查",
    # 关系重新提取步骤
    "reextract_start": "准备关系重新提取",
    "reextract_chapter": "提取章节关系",
    "reextract_error": "章节关系提取失败（已跳过）",
    "reextract_done": "关系提取完成，正在存档",
    # 修复后处理步骤
    "chapter_postprocess": "修复后处理",
    "causal_issues_exhausted": "因果链问题已穷尽",
    # TaskType enum values used in token tracking
    "plan_chapter": "章节规划",
    "bridge_chapter": "章节桥接",
    "draft_chapter": "章节写作",
    "wave_chapter": "初稿成章",
    "edit_chapter": "章节编辑",
    "check_chapter": "章节校验",
    "check_alignment": "对齐审计",
    "validate_causal": "因果链验证",
    "plot_guard_judge": "护栏决策",
    "extract_motifs": "母题提取",
    "critic_continuity": "连续性评审",
    "critic_strengths": "优点识别",
    "critic_character": "角色评审",
    "critic_causal": "因果评审",
    "summarize_chapter": "章节摘要",
    "summarize_volume": "卷摘要",
    "summarize_arc": "弧线摘要",
    "spec_enrich": "故事规格丰富",
    "blueprint_element_select": "叙事要素选择",
    "patch_chapter": "章节补丁",
    "adjust_outline": "调整大纲",
    "enrich_character": "角色丰化",
    "introduce_character": "角色建档",
    "adaptive_compress": "自适应压缩",
    "generate_config": "生成配置",
    "polish_config": "润色配置",
    "polish_chapter": "精修润色",
    "verify_compression": "验证压缩",
    "verify_compression_failed": "验证压缩失败",
    "upstream_compass": "上游罗盘门",
    "short_creative_summary": "短篇创作分析",
    "motif_history_repair_start": "母题历史修补启动",
    "motif_repair_layer1_start": "重建母题缓存统计",
    "motif_repair_layer1_done": "母题缓存统计完成",
    "motif_repair_layer2_start": "开始重新提取母题",
    "motif_repair_layer2_scanning": "母题扫描章节文件",
    "motif_repair_layer2_progress": "母题逐章处理",
    "motif_repair_layer2_done": "母题重新提取完成",
    "motif_history_repair_done": "母题历史修补完成",
    "motif_history_repair_error": "母题历史修补失败",
    "memory_vector_rebuild_start": "向量索引重建启动",
    "memory_vector_rebuild_episodic_start": "重建主记忆向量",
    "memory_vector_rebuild_episodic_done": "重建主记忆向量",
    "memory_vector_rebuild_expression_start": "重建表达通道向量",
    "memory_vector_rebuild_expression_done": "重建表达通道向量",
    "memory_vector_rebuild_expression_skipped": "表达通道向量已跳过",
    "memory_vector_rebuild_done": "向量索引重建完成",
    # 大纲对齐细节
    "alignment_missing_main_warn": "大纲对齐：主要章节缺失警告",
    "alignment_moderate_pass": "大纲对齐：基本通过",
    "alignment_repair_gateway_error": "大纲对齐修复：模型调用失败（已跳过）",
    # 自动角色注册
    "auto_register_character_start": "准备自动角色注册",
    "auto_register_character_done": "自动角色注册完成",
    # 因果链细节
    "causal_issues_exhausted_override": "因果问题已穷尽（强制退出）",
    "causal_post_repair_regression_failed": "因果修复后回归检查失败（已回滚）",
    "causal_post_repair_regression_skipped": "跳过因果修复后回归检查",
    "causal_recheck_gateway_error": "因果链复查：模型调用失败（已跳过）",
    "causal_recheck_internal_error": "因果链复查：内部错误（已回滚）",
    "causal_repair_gateway_error": "因果链修复：模型调用失败（已跳过）",
    "causal_repair_internal_error": "因果链修复：内部错误（已跳过）",
    "causal_validation_gateway_error": "因果链验证：模型调用失败（已跳过）",
    "causal_validation_internal_error": "因果链验证：内部错误（已跳过）",
    "causal_validation_unavailable": "因果链验证不可用（已跳过）",
    # 追读力修复细节
    "reading_power_prerepair_eval": "追读力修复前评估",
    "reading_power_memory_issues_indexed": "追读力问题已写入记忆",
    "reading_power_memory_guidance_added": "追读力记忆提示已加入",
    "reading_power_repair_memory_results_recorded": "追读力修复记忆结果已记录",
    "reading_power_repair_tickets_loaded": "追读力修复票据载入",
    "reading_power_repair_skipped": "追读力修复已跳过",
    "reading_power_repair_no_op": "追读力修复未产生改动",
    "reading_power_repair_gateway_error": "追读力修复：模型调用失败（已跳过）",
    "reading_power_repair_internal_error": "追读力修复：内部错误（已跳过）",
    "reading_power_change_budget_exceeded": "追读力修复改动超预算",
    "reading_power_recheck_gateway_error": "追读力复查：模型调用失败（已跳过）",
    "reading_power_recheck_internal_error": "追读力复查：内部错误（已回滚）",
    "reading_power_repair_recheck": "追读力修复后复评",
    "reading_power_issue_ledger": "追读力问题台账",
    "reading_power_repair_rollback": "追读力修复回滚",
    "reading_power_repair_early_exit": "追读力修复提前完成",
    "reading_power_repair_warning": "追读力修复警告",
    "reading_power_post_repair_checks_start": "追读力修复后跨维度检查",
    "reading_power_post_repair_checks_done": "追读力修复后跨维度检查完成",
    "reading_power_post_repair_checks_failed": "追读力修复后跨维度检查失败（已跳过）",
    "reading_power_eval": "追读力复评",
    "reading_power_eval_warning": "追读力复评失败（已跳过）",
    "reading_power_eval_after_repair": "追读力修复后评估",
    "reading_power_final_eval": "最终追读力归档",
    "reading_power_repair_loop_complete": "追读力修复流程完成",
    # 上下文处理
    "context_compress_failed": "上下文压缩失败（已跳过）",
    # 连贯性细节
    "continuity_eval_after_repair_warning": "修复后连贯性复查警告",
    "continuity_recheck_via_critic": "通过批注进行连贯性复查",
    "continuity_repair_early_exit": "连贯性修复提前退出",
    "continuity_repair_exhausted": "连贯性修复已达最大次数",
    "continuity_repair_gateway_error": "连贯性修复：模型调用失败（已跳过）",
    "continuity_repair_internal_error": "连贯性修复：内部错误（已跳过）",
    "continuity_recheck_gateway_error": "连贯性复查：模型调用失败（已跳过）",
    "continuity_recheck_internal_error": "连贯性复查：内部错误（已回滚）",
    # 要素进度
    "element_progress_scheduled": "要素进度更新已排队",
    "element_progress_updated": "要素进度已更新",
    "element_progress_failed": "要素进度更新失败（已跳过）",
    # 评分
    "evaluate_warning": "综合评分警告",
    # 初始化
    "init_character_bible_fallback": "角色设定（降级模式）",
    "init_param_drift": "初始化参数漂移检测",
    # 开头护栏
    "opening_guard_prescreen": "开头护栏预筛查",
    "opening_guard_repair": "开头护栏修复",
    # 大纲
    "plan_outline_starting": "开始生成章节大纲",
    # 精修
    "polish_reextract_canon": "精修后重新提取剧情状态",
    # 修复后处理
    "post_repair_chapter_repair_skipped_moderate_change": "跳过修复后章节重写（中等变动）",
    # 提示词清理
    "prompt_artifact_cleanup": "清理提示词遗留痕迹",
    "prompt_artifact_warning": "检测到提示词遗留痕迹",
    # 锚点再校准（修复轮次中重新定位问题段落）
    "anchor_recalibration": "锚点再校准",
    # 人称
    "pronoun_repair_recheck": "人称修复后复查",
    # 错误重试
    "retry_transient_error": "瞬时错误重试中",
    "format_repaired": "格式错误已本地修复",
    "format_repair_strategy_miss": "格式修复模块请求中",
    "format_repair_local_fallback": "缺失冒号分隔符本地兜底修复",
    "format_retry": "格式错误重试中",
    "format_retry_exhausted": "格式重试已用尽",
    # 短篇风格
    "short_profile_style": "短篇风格规范生成",
    "short_profile_style_resumed": "短篇风格规范（已恢复）",
    "short_profile_style_skipped": "短篇风格规范（已跳过）",
    "short_profile_style_failed": "短篇风格规范生成失败（已跳过）",
    "short_segment_plan": "短篇分段计划",
    # 章节链路细粒度诊断事件
    "alignment_recheck_skipped": "对齐复查已跳过",
    "best_effort_accepted": "最佳努力结果已接受",
    "bridge_coherence_warning": "桥接契约一致性警告",
    "causal_best_effort_accepted": "因果链最佳努力结果已接受",
    "causal_eval_after_polish": "精修后因果链复查",
    "causal_memory_guidance_added": "因果链记忆提示已加入",
    "causal_repair_memory_results_recorded": "因果链修复记忆结果已记录",
    "causal_repair_stagnated": "因果链修复停滞",
    "causal_repair_stagnation_detected": "因果链修复停滞检测",
    "continuity_escalation_note_added": "连贯性升级提示已加入",
    "continuity_eval_after_polish": "精修后连贯性复查",
    "continuity_memory_guidance_added": "连贯性记忆提示已加入",
    "continuity_recheck_after_repair": "归档前连贯性复查",
    "continuity_repair_force_patch_only": "连贯性修复限制为补丁模式",
    "continuity_repair_memory_results_recorded": "连贯性修复记忆结果已记录",
    "continuity_repair_rollback_continue": "连贯性修复回滚后继续",
    "continuity_repair_rollback_limit_reached": "连贯性修复回滚达到上限",
    "continuity_repair_stagnated": "连贯性修复停滞",
    "cross_dimension_causal_reduced": "跨维因果问题已降低",
    "cross_dimension_causal_skipped": "跨维因果复查已跳过",
    "cross_dimension_regression": "跨维回归风险",
    "cross_dimension_rp_skipped": "跨维追读力复查已跳过",
    "draft_short_text_warning": "初稿字数过短警告",
    "guard_compliance_recheck": "护栏合规复查",
    "guard_constraint_compliance_check": "护栏约束合规检查",
    "guard_ticket_repair_complete": "护栏票据修复完成",
    "guard_ticket_alignment_followup_start": "护栏票据对齐跟进开始",
    "guard_ticket_alignment_followup_complete": "护栏票据对齐跟进完成",
    "guard_ticket_alignment_best_version_restored": "护栏票据最佳版本恢复",
    "guard_ticket_alignment_retry_checkpoint": "护栏票据对齐待继续",
    "repair_attempt_guidance": "修复策略换向",
    "repair_round_focus": "本轮修复定位到单点问题",
    "init_project_reset": "初始化项目重置",
    "macro_guard_audit": "宏观护栏审计",
    "macro_guard_audit_failed": "宏观护栏审计失败（已跳过）",
    "archive_policy_block": "归档策略阻断",
    "polish_triggered": "触发精修重整",
    "post_polish_reaudit_skipped": "精修后重审已跳过",
    "post_polish_reaudit_warning": "精修后重审警告",
    "pre_repair_alignment_warning": "修复前对齐警告",
    "precheck_skip_empty_similarity": "空相似度预检已跳过",
    "progressive_repair_narrowed": "渐进修复范围已收窄",
    "quality_check_short_text_warning": "质检字数过短警告",
    "quality_reports_refresh_after_text_change": "质量报告已刷新",
    "quality_gate": "质量闸门",
    "quality_gate_regression_detected": "质量闸门检测到回归",
    "quality_gate_rollback": "质量闸门回滚",
    "quality_gate_rollback_applied": "质量闸门回滚已应用",
    "quality_gate_secondary_repair_requested": "质量闸门请求二次修复",
    "reading_power_best_effort_accepted": "追读力最佳努力结果已接受",
    "reading_power_critical": "追读力严重告警",
    "reading_power_eval_fallback": "追读力评估降级",
    "reading_power_next_chapter_constraints": "追读力续章约束",
    "reading_power_post_repair_causal_done": "追读力修复后因果复查完成",
    "reading_power_post_repair_causal_failed": "追读力修复后因果复查失败（已跳过）",
    "reading_power_repair_stagnated": "追读力修复停滞",
    "reading_power_trend": "追读力趋势记录",
    "reading_power_warning": "追读力警告",
    "reading_power_window_alert": "追读力窗口告警",
    "reading_power_window_init_failed": "追读力窗口初始化失败（已跳过）",
    "repair_dimension_skipped_total_cap": "修复维度因总上限跳过",
    "repair_dimension_rounds_capped": "修复维度轮次已按上限截断",
    "repair_metrics": "修复指标已汇总",
    "repair_prescreen_failed": "修复预筛失败（已跳过）",
    "repair_rollback": "修复回滚",
    "repair_suggestion_warnings": "修复建议警告",
    "revelation_density_warning": "揭示密度警告",
    "strand_weave_recorded": "线索编织记录完成",
    "style_trend_skipped": "风格趋势审计已跳过",
    "time_validation": "时间线校验",
    "word_count_archive_gate": "归档前字数闸门",
    "word_count_archive_gate_bypassed": "归档前字数闸门已放行",
    "word_count_archive_gate_limit_reached": "归档前字数闸门达到上限",
    "word_count_archive_gate_skipped": "归档前字数闸门已跳过",
    "word_count_polish_applied": "字数终端润色已应用",
    "word_count_polish_continuity_check_error": "字数精修连贯性检查异常",
    "word_count_polish_continuity_rollback": "字数精修连贯性回滚",
    "word_count_restructure_applied": "字数结构重整已应用",
    "word_count_restructure_guard_verify": "重整后护栏复核",
    # Token 升级
    "token_escalation": "Token 额度升级",
    # 故事内核 (StoryKernel)
    "character_relationship_sync": "角色关系同步",
    "character_relationship_sync_failed": "角色关系同步失败",
    "story_kernel_build_failed": "故事内核构建失败",
    "story_kernel_gate": "故事内核质检",
    "story_kernel_gate_blocked": "故事内核质检未通过",
    "story_kernel_saved": "故事内核已保存",
    "story_kernel_updated": "故事内核已更新",
    "story_kernel_short_updated": "短篇内核状态已更新",
    "preflight_token_estimate": "请求前 Token 估算",
    "init_knowledge_boundaries": "知识边界初始化",
    "init_knowledge_boundaries_resumed": "知识边界初始化（已恢复）",
    "init_knowledge_boundaries_failed": "知识边界初始化失败",
    "extract_knowledge_deltas": "抽取知识边界增量",
    "extract_knowledge_deltas_failed": "抽取知识边界增量失败",
    "knowledge_boundary_downgraded_to_warning": "知识边界降级为警告",
    "knowledge_boundary_repair_attempt": "知识边界修复尝试",
    "knowledge_boundary_repair_attempted": "知识边界修复已尝试",
    "knowledge_boundary_repair_completed": "知识边界修复完成",
    "knowledge_boundary_repair_empty": "知识边界修复（无遗漏）",
    "knowledge_boundary_repair_failed": "知识边界修复失败",
    "knowledge_boundary_repair_recheck": "知识边界修复后复查",
    "short_quality_gate": "短篇质量门",
    "short_quality_gate_best_effort": "短篇质量门（尽力学）",
    "short_quality_gate_pass_after_repair": "短篇质量门（修复后通过）",
    "short_checkpoint_invalidated": "短篇检查点已作废",
    "plan_outline_batch_incomplete": "章节大纲批次不完整",
    "plan_outline_batch_quality_failed": "章节大纲批次质量未达标",
    "plan_outline_batch_quality_retry": "章节大纲批次质量重试",
    "repair_init_artifact_chunked": "初始化产物分块修复",
    "repair_init_artifact_degraded": "初始化产物降级修复",
    "repair_init_artifact_precise_required": "初始化产物需要精准定位",
    "repair_init_artifact_patch_skipped": "初始化产物跳过局部修复",
    "repair_init_artifact_pruned": "初始化产物裁剪修复",
    "init_claim_coverage_backfilled": "一致性 Claims 契约覆盖已回填",
    "volume_memory_finalize": "卷末记忆收尾",
    "archive_hard_quality_diagnostics": "归档硬性质量诊断",
    "archive_quality_block_orphan_saved": "归档质量阻断孤儿产物已留存",
    "chapter_repair_report_dropped_on_resume": "恢复时已丢弃失效的章节修复报告",
    "chapter_repair_report_stale_skipped": "已跳过陈旧的章节修复报告",
    # 细粒度进度事件
    "archive_eval_report_refresh_required": "归档评估报告需刷新",
    "archive_quality_reports_refresh_required": "归档质量报告需刷新",
    "archive_stale_reports_refresh": "归档过期报告刷新",
    "archive_stale_reports_refresh_failed": "归档过期报告刷新失败",
    "archive_quality_retry_checkpoint": "归档质量重试待确认",
    "archive_quality_retry_diagnosis": "归档质量重试诊断",
    "archive_retry_refresh_reports": "归档重试：刷新验证报告",
    "audit_result_update": "审计结果已更新",
    "auto_register_character_label_deferred": "角色标签自动注册已延后",
    "book_audit_marker_written": "全书审计标记已写入",
    "carry_forward_archive_bypass": "续章承接归档放行",
    "carry_forward_repair_complete": "续章承接修复完成",
    "carry_forward_repair_skipped": "续章承接修复已跳过",
    "carry_forward_repair_start": "续章承接修复开始",
    "carry_forward_retry_checkpoint": "续章承接重试待确认",
    "causal_repair_skipped_high_score": "因果链评分较高，修复已跳过",
    "chapter_quality_repair_done": "章节质量修复完成",
    "chapter_quality_repair_started": "章节质量修复开始",
    "world_rule_review": "世界规则审查",
    "world_rule_plan_coverage": "世界规则计划覆盖检查",
    "world_rule_patch_repair": "世界规则精确补丁修复",
    "world_rule_patch_degraded": "世界规则补丁已降级到受限修复",
    "world_rule_patch_recheck": "世界规则补丁后复检",
    "world_rule_patch_recheck_rollback": "世界规则补丁复检失败并回滚",
    "world_rule_repair_recheck": "世界规则受限修复后复检",
    "world_rule_post_quality_patch": "质量修复后的世界规则补丁",
    "world_rule_post_quality_patch_recheck": "质量修复后的世界规则复检",
    "world_rule_post_quality_rollback": "世界规则回归已回滚",
    "world_rule_compliance_check": "最终世界规则合规检查",
    "world_rule_finalize_gate": "世界规则归档闸门",
    "coherence_auto_repair_applied": "一致性自动修复已应用",
    "continuity_repair_pre_snapshot_missing": "连贯性修复前快照缺失",
    "contract_runtime_repair_complete": "运行时契约修复完成",
    "contract_runtime_repair_failed": "运行时契约修复失败",
    "contract_runtime_repair_review_required": "运行时契约修复需复核",
    "contract_runtime_repair_skipped": "运行时契约修复已跳过",
    "contract_runtime_repair_start": "运行时契约修复开始",
    "draft_scene_local_validation_failed": "场景草稿本地校验失败",
    "draft_scene_reused": "场景草稿已复用",
    "entity_reconciliation_applied": "实体调和补丁已应用",
    "entity_reconciliation_complete": "实体调和完成",
    "entity_reconciliation_error": "实体调和失败",
    "entity_reconciliation_integration_error": "实体调和集成失败",
    "entity_reconciliation_start": "实体调和开始",
    "evaluate_reused": "综合评分已复用",
    "extend_outline_done": "章节大纲扩展完成",
    "extend_outline_start": "章节大纲扩展开始",
    "guard_report_stale_skipped": "陈旧护栏报告已跳过",
    "human_decision_requested": "等待人工决策",
    "humanize_candidate_saved": "AI 去痕候选已保存",
    "init_claim_coverage_repaired": "契约覆盖已修复",
    "init_coherence_focus_chapters": "初始化一致性聚焦章节",
    "init_coherence_repair_loop_stopped": "初始化一致性修复循环已停止",
    "init_copilot_characters_applied": "初始化角色协作改动已应用",
    "init_copilot_outline_applied": "初始化大纲协作改动已应用",
    "init_entity_graph_sensory_rules_synced": "实体图谱感知规则已同步",
    "init_resume_classified": "初始化断点已分类",
    "init_resume_fallback": "初始化断点恢复降级",
    "init_source_artifacts_contracts_rebuilt": "源头章节契约已重建",
    "kernel_persist_pending_detected": "检测到内核待持久化",
    "knowledge_boundary_verification_skipped": "知识边界验证已跳过",
    "llm_stream_delta": "模型流式输出片段",
    "llm_stream_end": "模型流式输出结束",
    "llm_stream_error": "模型流式输出错误",
    "llm_stream_restart": "模型流式输出重启",
    "llm_stream_start": "模型流式输出开始",
    "llm_stream_validation": "模型输出校验与恢复",
    "macro_guard_replan_marker_downgraded": "宏观护栏重规划标记降级",
    "macro_guard_replan_required": "宏观护栏要求重规划",
    "manual_revision_done": "人工修订完成",
    "manual_revision_noop": "人工修订无变化",
    "manual_revision_start": "人工修订开始",
    "plan_chapter_contracts_partial_resume": "章节契约部分断点恢复",
    "plan_outline_partial_cache_quarantined": "章节大纲部分缓存已隔离",
    "plan_outline_session_resume_preferred": "章节大纲会话断点优先恢复",
    "pov_drift_audit": "POV 漂移审计",
    "pov_drift_continuity_injected": "POV 漂移已注入连贯性复查",
    "quality_reports_rebound_after_text_change": "文本变更后质量报告已回补",
    "quality_reports_refresh_start": "质量报告刷新开始",
    "quality_reports_refresh_warning": "质量报告刷新失败（已跳过）",
    "alignment_refresh_failed_fallback": "对齐报告刷新失败（已回退）",
    "continuity_refresh_failed_fallback": "连贯性报告刷新失败（已回退）",
    "causal_refresh_failed_fallback": "因果报告刷新失败（已回退）",
    "alignment_refresh_gather_exception": "对齐报告并发刷新异常",
    "continuity_refresh_gather_exception": "连贯性报告并发刷新异常",
    "causal_refresh_gather_exception": "因果报告并发刷新异常",
    "archive_quality_refresh_failed_non_blocking": "归档质量报告刷新失败（非阻塞）",
    "quality_trend_record_failed": "质量趋势记录失败",
    "quality_trend_recorded": "质量趋势已记录",
    "reading_power_refresh_after_text_change_failed": "文本变更后追读力刷新失败",
    "reading_power_repair_report_refresh_failed": "追读力修复报告刷新失败",
    "repair_init_artifact_issue_focus": "初始化修复问题聚焦",
    "repair_init_artifact_patch_no_effect": "初始化局部修复未生效",
    "repair_init_artifact_point_batch": "初始化修复点批次",
    "repair_init_artifact_scoped": "初始化修复范围已限定",
    "repair_init_artifact_targets": "初始化修复目标已确定",
    "repair_metrics_report_persist_failed": "修复指标报告保存失败",
    "retrieval_eval_planning": "检索评估规划",
    "scene_plan_repair": "场景计划修复",
    "snapshots_pruned": "历史快照已清理",
    "state_adjudication_downgraded_to_warning": "状态裁判降级为警告",
    "state_adjudication_repair_quality_regressed": "状态裁判修复质量回退",
    "sync_source_artifacts_refreshed": "源头 artifact 同步已刷新",
    "terminal_humanize_applied": "终端 AI 去痕已应用",
    "text_changed_before_archive": "归档前文本已变更",
    "theme_arc_alignment_check": "主题弧线对齐检查",
    "time_marker_normalized": "时间标记已规范化",
    "token_budget_normalized": "Token 预算已规范化",
    "upstream_compass_location_judgment": "上游罗盘位置判定",
    "upstream_compass_location_judgment_failed": "上游罗盘位置判定失败",
    "wave_integrity_blocking": "WAVE 完整性阻断",
    "wave_integrity_residual_after_repair": "WAVE 修复后仍有残留问题",
    # 小说修复、归档与配音模块的完整事件映射。
    "alignment_literal_patch_attempt": "大纲字面约束补丁修复",
    "alignment_literal_patch_result": "大纲字面约束补丁修复结果",
    "alignment_recheck_cache_bypassed": "对齐复查已绕过缓存",
    "alignment_refresh_failed_blocking": "大纲对齐报告刷新失败（已阻断归档）",
    "causal_refresh_failed_blocking": "因果链报告刷新失败（已阻断归档）",
    "continuity_refresh_failed_blocking": "连贯性报告刷新失败（已阻断归档）",
    "reading_power_refresh_after_text_change_failed_blocking": "追读力报告刷新失败（已阻断归档）",
    "repair_audit_event": "修复审计事件",
    "repair_audit_summary": "修复审计汇总",
    "repair_repeated_issue_guard": "修复重复问题守卫",
    "repair_strategy_diagnosis": "修复策略诊断",
    "repair_strategy_diagnosis_failed": "修复策略诊断失败",
    "repair_v2_doom_loop_guard": "修复任务循环保护触发",
    "repair_v2_mission_complete": "修复任务完成",
    "repair_v2_mission_start": "修复任务启动",
    "repair_v2_plan": "修复任务计划生成",
    "repair_v2_retry_scheduled": "修复任务已安排重试",
    "repair_v2_rollback": "修复任务已回滚",
    "repair_v2_target_committed": "修复目标已提交",
    "repair_v2_target_failed": "修复目标失败",
    "repair_v2_target_skipped": "修复目标已跳过",
    "state_adjudication_rescue": "状态裁判补救",
    "summary_drift_check_failed": "摘要漂移检查失败",
    "summary_drift_checked": "摘要漂移检查完成",
    "tts_script_persisted": "配音脚本已存档",
    "voice_preview_failed": "角色音色试听生成失败",
    "voice_preview_ready": "角色音色试听已生成",
    "volume_audit_completed": "卷末审计完成",
    "volume_audit_issues_persisted": "卷末审计问题已存档",
    "volume_audit_retry": "卷末审计重试",
    "volume_data_archive_failed": "卷级数据归档失败",
    "volume_data_archived": "卷级数据已归档",
}

_DIMENSION_DISPLAY_NAMES: dict[str, str] = {
    "alignment": "大纲对齐",
    "causal": "因果链",
    "continuity": "连贯性",
    "cross_dimension": "跨维检查",
    "guard": "护栏",
    "quality_gate": "质量闸门",
    "reading_power": "追读力",
    "repair": "修复",
    "word_count": "字数",
    "world_rule": "世界规则",
}

_DYNAMIC_STEP_SUFFIX_DISPLAY_NAMES: tuple[tuple[str, str], ...] = (
    ("_best_effort_accepted", "{dimension}最佳努力结果已接受"),
    ("_issue_ledger", "{dimension}问题台账"),
    ("_orchestrator_timeout", "{dimension}修复编排超时"),
    ("_recheck_gateway_error", "{dimension}复查模型调用失败（已跳过）"),
    ("_recheck_internal_error", "{dimension}复查内部错误（已回滚）"),
    ("_repair_early_exit", "{dimension}修复提前收束"),
    ("_repair_exhausted", "{dimension}修复轮次耗尽"),
    ("_repair_gateway_error", "{dimension}修复模型调用失败（已跳过）"),
    ("_repair_internal_error", "{dimension}修复内部错误（已跳过）"),
    ("_repair_recheck", "{dimension}修复后复查"),
    ("_repair_rollback", "{dimension}修复回滚"),
    ("_repair_skipped", "{dimension}修复已跳过"),
    ("_repair_stagnated", "{dimension}修复停滞"),
)


def _display_dynamic_dimension_step(raw_key: str) -> str | None:
    """Translate generated dimension diagnostics like ``causal_repair_recheck``."""
    for suffix, template in _DYNAMIC_STEP_SUFFIX_DISPLAY_NAMES:
        if not raw_key.endswith(suffix):
            continue
        stem = raw_key[: -len(suffix)]
        dimension = _DIMENSION_DISPLAY_NAMES.get(stem)
        if dimension:
            return template.format(dimension=dimension)
    return None


_PROGRESS_REPAIR_CONTINUITY: dict[str, int] = {
    "repair_continuity_start": 10,
    "continuity_eval_after_repair_start": 70,
    "continuity_eval_after_repair": 90,
    "chapter_postprocess": 93,
    "repair_continuity": 95,
}

_PROGRESS_REPAIR_CAUSAL: dict[str, int] = {
    "repair_causal_start": 10,
    "causal_eval_after_repair_start": 65,
    "causal_eval_after_repair": 88,
    "causal_eval_after_repair_warning": 88,
    "causal_issues_exhausted": 90,
    "chapter_postprocess": 93,
    "repair_causal": 95,
}

_PROGRESS_REPAIR_ISSUES: dict[str, int] = {
    "repair_continuity_start": 15,
    "repair_continuity": 40,
    "continuity_eval_after_repair_start": 50,
    "continuity_eval_after_repair": 55,
    "chapter_postprocess": 60,
    "repair_causal_start": 65,
    "causal_issues_exhausted": 67,
    "causal_eval_after_repair_start": 75,
    "causal_eval_after_repair": 82,
    "causal_eval_after_repair_warning": 82,
    "repair_causal": 88,
    "memory_updated": 92,
    "repair_issues": 95,
}

_PROGRESS_REEVALUATE_CHAPTER: dict[str, int] = {
    "reevaluate_start": 10,
    "evaluate": 35,
    "evaluate_warning": 35,
    "continuity_eval_after_repair_start": 55,
    "continuity_eval_after_repair": 70,
    "continuity_eval_after_repair_warning": 70,
    "causal_eval_after_repair_start": 80,
    "causal_eval_after_repair": 92,
    "causal_eval_after_repair_warning": 92,
    "reading_power_eval": 92,
    "reading_power_eval_warning": 92,
    "reevaluate_chapter": 95,
}


_POLISH_STEP_MAP: dict[str, str] = {
    "polish_start": "polish_start",
    "polish": "polish",
    "evaluate": "polish",
    "evaluate_warning": "polish",
    "continuity_eval_after_repair_start": "polish",
    "continuity_eval_after_repair": "polish",
    "continuity_eval_after_repair_warning": "polish",
    "causal_eval_after_repair_start": "polish",
    "causal_eval_after_repair": "polish",
    "causal_eval_after_repair_warning": "polish",
    "chapter_postprocess": "polish",
}


_REPAIR_CONTINUITY_STEP_MAP: dict[str, str] = {
    "resume_from_progress": "repair_continuity_start",
    "repair_continuity_start": "repair_continuity_start",
    "continuity_eval_after_repair_start": "continuity_eval_after_repair",
    "continuity_eval_after_repair": "continuity_eval_after_repair",
    "continuity_eval_after_repair_warning": "continuity_eval_after_repair",
    "chapter_postprocess": "repair_continuity",
    "repair_continuity": "repair_continuity",
}


_REPAIR_CAUSAL_STEP_MAP: dict[str, str] = {
    "resume_from_progress": "repair_causal_start",
    "repair_causal_start": "repair_causal_start",
    "causal_issues_exhausted": "repair_causal_start",
    "causal_issues_exhausted_override": "repair_causal_start",
    "causal_eval_after_repair_start": "causal_eval_after_repair",
    "causal_eval_after_repair": "causal_eval_after_repair",
    "causal_eval_after_repair_warning": "causal_eval_after_repair",
    "chapter_postprocess": "repair_causal",
    "repair_causal": "repair_causal",
}


_REPAIR_ISSUES_STEP_MAP: dict[str, str] = {
    "repair_continuity_start": "repair_continuity_start",
    "repair_continuity": "repair_continuity_start",
    "continuity_eval_after_repair_start": "repair_continuity_start",
    "continuity_eval_after_repair": "repair_continuity_start",
    "repair_causal_start": "repair_causal_start",
    "causal_issues_exhausted": "repair_causal_start",
    "causal_eval_after_repair_start": "repair_causal_start",
    "causal_eval_after_repair": "repair_causal_start",
    "causal_eval_after_repair_warning": "repair_causal_start",
    "repair_causal": "repair_causal_start",
    "memory_updated": "repair_issues",
    "repair_issues": "repair_issues",
}

_REEXTRACT_STEP_MAP: dict[str, str] = {
    "reextract_start": "reextract_start",
    "reextract_chapter": "reextract_chapter",
    "reextract_error": "reextract_chapter",
    "reextract_done": "reextract_done",
}

_REEVALUATE_CHAPTER_STEP_MAP: dict[str, str] = {
    "reevaluate_start": "reevaluate_start",
    "evaluate_warning": "evaluate",
    "continuity_eval_after_repair_start": "continuity_eval_after_repair",
    "continuity_eval_after_repair_warning": "continuity_eval_after_repair",
    "causal_eval_after_repair_start": "causal_eval_after_repair",
    "causal_eval_after_repair_warning": "causal_eval_after_repair",
    "reading_power_eval_warning": "reading_power_eval",
}

_BOOK_CONSISTENCY_STEP_MAP: dict[str, str] = {
    "book_consistency_start": "book_consistency_start",
    "book_consistency_issue_pool_ready": "book_consistency_start",
    "book_consistency_two_phase_start": "book_consistency",
    "book_consistency_two_phase_summary_reused": "book_consistency",
    "book_consistency_two_phase_summary_done": "book_consistency",
    "book_consistency_two_phase_summary_only": "book_consistency",
    "book_consistency_two_phase_targeted_capped": "book_consistency",
    "book_consistency_two_phase_expanded": "book_consistency",
    "book_consistency_two_phase_targeted_start": "book_consistency",
    "book_consistency_chunk_progress": "book_consistency",
    "book_consistency": "book_consistency",
    "book_consistency_audit_saved": "book_consistency",
    "book_consistency_checkpoint_result_reused": "book_consistency",
    "book_consistency_verify_start": "book_consistency",
    "book_consistency_verify_progress": "book_consistency",
    "book_consistency_verify_done": "book_consistency",
    "book_consistency_repair_start": "book_consistency_repair_",
    "book_consistency_repair_classification": "book_consistency_repair_",
    "book_consistency_repair_progress": "book_consistency_repair_",
    "book_consistency_repair_review": "book_consistency_repair_",
    "book_consistency_post_audit_start": "book_consistency_repair_",
    "book_consistency_post_audit_done": "book_consistency_repair_",
    "book_consistency_rollback": "book_consistency_repair_",
    "book_consistency_auto_continue": "book_consistency_repair_",
    "book_consistency_stuck": "book_consistency_repair_",
    "book_consistency_auto_continue_done": "book_consistency_repair_",
    "book_consistency_backup_created": "book_consistency_repair_",
    "book_consistency_report_written": "book_consistency_report_written",
    "book_consistency_repair_report_written": "book_consistency_report_written",
    # auto-repair inner steps (from repair_issues / continuity / causal)
    "repair_continuity_start": "book_consistency_repair_",
    "repair_continuity": "book_consistency_repair_",
    "continuity_eval_after_repair_start": "book_consistency_repair_",
    "continuity_eval_after_repair": "book_consistency_repair_",
    "repair_causal_start": "book_consistency_repair_",
    "repair_causal": "book_consistency_repair_",
    "causal_eval_after_repair_start": "book_consistency_repair_",
    "causal_eval_after_repair": "book_consistency_repair_",
    "causal_eval_after_repair_warning": "book_consistency_repair_",
    "repair_issues": "book_consistency_repair_",
    "causal_issues_exhausted": "book_consistency_repair_",
}

_PROGRESS_POLISH: dict[str, int] = {
    "polish_start": 15,
    "polish": 90,
    "evaluate": 90,
    "evaluate_warning": 90,
    "continuity_eval_after_repair_start": 90,
    "continuity_eval_after_repair": 90,
    "continuity_eval_after_repair_warning": 90,
    "causal_eval_after_repair_start": 90,
    "causal_eval_after_repair": 90,
    "causal_eval_after_repair_warning": 90,
    "chapter_postprocess": 95,
}

_PROGRESS_BOOK_CONSISTENCY: dict[str, int] = {
    "book_consistency_start": 8,
    "book_consistency_issue_pool_ready": 18,
    "book_consistency_two_phase_start": 22,
    "book_consistency_two_phase_summary_reused": 32,
    "book_consistency_two_phase_summary_done": 32,
    "book_consistency_two_phase_summary_only": 45,
    "book_consistency_two_phase_targeted_capped": 36,
    "book_consistency_two_phase_expanded": 38,
    "book_consistency_two_phase_targeted_start": 40,
    "book_consistency_chunk_progress": 42,
    "book_consistency": 48,
    "book_consistency_audit_saved": 50,
    "book_consistency_checkpoint_result_reused": 50,
    "book_consistency_verify_": 58,
    "book_consistency_verify_start": 52,
    "book_consistency_verify_progress": 58,
    "book_consistency_verify_done": 65,
    "book_consistency_repair_start": 68,
    "book_consistency_repair_classification": 72,
    "book_consistency_repair_progress": 78,
    "book_consistency_repair_review": 92,
    "book_consistency_post_audit_start": 93,
    "book_consistency_post_audit_done": 94,
    "book_consistency_rollback": 92,
    "book_consistency_auto_continue": 84,
    "book_consistency_stuck": 92,
    "book_consistency_auto_continue_done": 94,
    "book_consistency_backup_created": 94,
    "book_consistency_report_written": 96,
    "book_consistency_repair_report_written": 98,
}

_PROGRESS_EXPORT: dict[str, int] = {
    "export": 90,
}

_PROGRESS_REEXTRACT: dict[str, int] = {
    "reextract_start": 5,
    "reextract_chapter": 20,  # placeholder; desktop/progress.py overrides with payload ratio
    "reextract_error": 20,
    "reextract_done": 95,
}

_PROGRESS_REPAIR_MOTIF_HISTORY: dict[str, int] = {
    "motif_history_repair_start": 10,
    "motif_repair_layer1_start": 15,
    "motif_repair_layer1_done": 35,
    "motif_repair_layer2_start": 38,
    "motif_repair_layer2_scanning": 40,
    "motif_repair_layer2_progress": 45,
    "motif_repair_layer2_done": 85,
    "motif_history_repair_done": 95,
    "motif_history_repair_error": 95,
}

_CLI_STEP_LABELS: dict[str, dict[str, str]] = {
    "run_short": {
        "short_resume_rollback": "↩ 短篇断点缓存已隔离，准备回溯续跑",
        "spec": "✅ 故事规格确认",
        "chapter_research_start": "🔎 章节研究进行中",
        "chapter_research_cache_hit": "⏩ 章节证据包已复用",
        "chapter_research_ready": "✅ 章节证据包已就绪",
        "chapter_research_skipped": "⏭ 章节研究已跳过",
        "short_blueprint_elements": "✅ 叙事要素选择完成",
        "short_blueprint_elements_resumed": "⏩ 叙事要素选择（已恢复）",
        "short_blueprint": "✅ 叙事蓝图生成",
        "short_profile_style": "✅ 风格规范生成",
        "short_profile_style_resumed": "⏩ 风格规范（已恢复）",
        "short_profile_style_skipped": "⏩ 风格规范（已跳过）",
        "beats": "✅ 节拍结构生成",
        "beats_resumed": "⏩ 节拍结构（已恢复）",
        "short_execution_plan": "✅ 短篇执行方案生成",
        "short_segment_plan": "✅ 短篇分段计划生成",
        "draft": "✅ 初稿完成",
        "short_completeness_check": "✅ 完整性检查完成",
        "evaluate": "✅ 质量评估完成",
        "creative_summary": "✅ 创作分析完成",
        "early_stop": "⭐ 质量优秀，提前完成",
    },
    "init_long": {
        "init_resume_rollback": "↩ 断点缓存已隔离，准备回溯续跑",
        "spec": "✅ 故事规格确认",
        "spec_resumed": "⏩ 故事规格（已恢复）",
        "init_web_research": "✅ 资料检索完成",
        "init_web_research_start": "▶ 资料检索中",
        "init_web_research_skipped": "⏩ 资料检索已跳过",
        "init_web_research_failed": "⚠️ 资料检索失败（已继续）",
        "init_web_research_resumed": "⏩ 资料检索（已恢复）",
        "init_research_dossier": "✅ 资料分析完成",
        "init_research_dossier_resumed": "⏩ 资料分析（已恢复）",
        "init_research_dossier_failed": "⚠️ 资料分析失败（已继续）",
        "outline_research_grounding": "✅ 大纲资料校准完成",
        "outline_research_grounding_resumed": "⏩ 大纲资料校准（已恢复）",
        "outline_research_grounding_failed": "⚠️ 大纲资料校准失败（已继续）",
        "init_upstream_health": "✅ 上游健康门记录完成",
        "init_story_bible": "✅ 世界观设定生成完成",
        "init_story_bible_resumed": "⏩ 世界观设定（已恢复）",
        "init_character_bible_starting": "▶ 角色设定生成中",
        "init_character_bible": "✅ 角色设定生成完成",
        "init_character_bible_resumed": "⏩ 角色设定（已恢复）",
        "plan_blueprint_elements": "✅ 叙事要素选择完成",
        "plan_blueprint_elements_resumed": "⏩ 叙事要素选择（已恢复）",
        "profile_style": "✅ 风格规范生成完成",
        "profile_style_resumed": "⏩ 风格规范（已恢复）",
        "profile_style_skipped": "⏩ 风格规范（已跳过）",
        "profile_style_failed": "⚠️ 风格规范生成失败（已跳过）",
        "plan_blueprint": "✅ 叙事蓝图生成完成",
        "plan_blueprint_resumed": "⏩ 叙事蓝图（已恢复）",
        "plan_blueprint_validated": "✅ 叙事蓝图验证通过",
        "plan_blueprint_repaired": "✅ 叙事蓝图修复完成",
        "plan_blueprint_subplot_weave_validation": "✅ 叙事蓝图支线交织验证完成",
        "derive_init_coherence_profile_start": "▶ 初始化一致性画像生成中",
        "derive_init_coherence_profile": "✅ 初始化一致性画像完成",
        "init_coherence_profile_resumed": "⏩ 初始化一致性画像（已恢复）",
        "refine_init_coherence_profile_start": "▶ 初始化一致性画像精炼中",
        "refine_init_coherence_profile": "✅ 初始化一致性画像精炼完成",
        "build_init_coherence_profile_start": "▶ 初始化一致性画像生成中",
        "build_init_coherence_profile": "✅ 初始化一致性画像完成",
        "extract_init_coherence_claims_start": "▶ 初始化一致性 Claims 抽取中",
        "extract_init_coherence_claims": "✅ 初始化一致性 Claims 抽取完成",
        "retrieve_init_conflict_candidates_start": "▶ 初始化冲突候选检索中",
        "retrieve_init_conflict_candidates": "✅ 初始化冲突候选检索完成",
        "adjudicate_init_conflict_candidates_start": "▶ 初始化冲突候选裁判中",
        "adjudicate_init_conflict_candidates": "✅ 初始化冲突候选裁判完成",
        "init_coherence_report_resumed": "⏩ 初始化一致性报告（已恢复）",
        "adjudicate_blueprint_coherence": "✅ 蓝图自洽裁判完成",
        "repair_init_artifact_patch": "🛠️ 初始化局部修复完成",
        "repair_init_artifact_patch_degraded": "初始化局部修复降级",
        "plan_outline": "✅ 故事大纲规划完成",
        "plan_outline_resumed": "⏩ 故事大纲（已恢复）",
        "plan_outline_cache_rejected": "↩ 章节大纲缓存已失效，将重新生成",
        "plan_outline_reveal_guard_manifest_repaired": "⏩ 章节大纲缓存已验证并恢复",
        "plan_outline_resume_rejected": "↩ 章节大纲断点已失效，将重新生成",
        "adjudicate_outline_inheritance": "✅ 大纲继承裁判完成",
        "init_narrative_contract_resumed": "⏩ LLM 叙事契约（已恢复）",
        "init_narrative_contract": "✅ 叙事契约生成完成",
        "init_narrative_contract_failed": "⚠️ 叙事契约生成失败（已跳过）",
        "plan_chapter_contracts_resumed": "⏩ 章节契约（已恢复）",
        "plan_chapter_contracts": "✅ 章节契约生成完成",
        "plan_chapter_contracts_failed": "⚠️ 章节契约生成失败",
        "adjudicate_contract_coherence": "✅ 契约自洽裁判完成",
        "init_claim_contract_coverage": "✅ 一致性 Claims 契约覆盖审计完成",
        "init_source_artifacts": "✅ 源头 artifact 准入完成",
        "init_source_artifacts_resume": "⏩ 源头准入断点恢复",
        "init_source_artifacts_repair": "🔧 源头准入修复",
        "init_source_artifacts_repaired": "✅ 源头准入修复完成",
        "source_artifacts_repair_targeted": "🔧 源头准入定向修复",
        "init_readiness": "✅ 初始化准入检查完成",
        "canon_init": "✅ 规范状态初始化完成",
        "canon_resumed": "⏩ 规范状态（已恢复）",
        "canon_state": "✅ 规范状态初始化完成",
    },
    "run_chapter": {
        "retrieve": "✅ Canon 上下文检索完成",
        "state_packet": "✅ 章节状态包已组装",
        "chapter_research_start": "🔎 章节研究进行中",
        "chapter_research_cache_hit": "⏩ 章节证据包已复用",
        "chapter_research_ready": "✅ 章节证据包已就绪",
        "chapter_research_skipped": "⏭ 章节研究已跳过",
        "adjudicate_character_introduction_start": "👤 裁决新角色候选中…",
        "adjudicate_character_introduction_done": "✅ 新角色候选裁决完成",
        "adjudicate_character_introduction_failed": "⚠️  新角色候选裁决失败（已跳过）",
        "introduce_character_detected": "👤 检测到新出场角色，建立骨架档案中…",
        "introduce_character_done": "✅ 新角色档案生成完成",
        "introduce_character_failed": "⚠️  角色建档失败（已跳过）",
        "bridge": "🌉 章节桥接契约生成完成",
        "upstream_compass": "🧭 上游罗盘门完成",
        "plan": "✅ 章节规划完成",
        "draft": "✅ DRAFT 原稿生成完成",
        "wave": "✅ 初稿成章完成",
        "edit_budget_trimmed": "🪶 编辑轮次已按问题轻重收缩",
        "edit_early_stop": "🛑 编辑收益递减，提前收束",
        "context_compress": "🗜️ 上下文压缩完成",
        "extract_canon": "✅ Canon 增量提取完成",
        "evaluate": "✅ 质量评估完成",
        "alignment": "🧭 大纲对齐检查完成",
        "alignment_after_repair": "🧭 修正后再次对齐检查完成",
        "alignment_repair": "🛠️  触发对齐修复编辑",
        "continuity_eval": "🪢 跨章连贯性检查完成",
        "continuity_eval_after_repair": "🪢 修复后再次连贯性检查完成",
        "continuity_repair": "🩹 连贯性定向修复完成",
        "reading_power_prerepair_eval": "📈 追读力修复前评估完成",
        "reading_power_repair_tickets_loaded": "🎫 追读力修复票据已载入",
        "reading_power_repair_recheck": "📈 追读力修复后复评完成",
        "reading_power_eval_after_repair": "📈 追读力修复后评估完成",
        "reading_power_repair_skipped": "⏩ 追读力修复已跳过",
        "reading_power_repair_no_op": "⏸️ 追读力修复未产生改动",
        "reading_power_repair_rollback": "↩ 追读力修复已回滚",
        "reading_power_repair_warning": "⚠️  追读力修复仍有问题待复核",
        "reading_power_post_repair_checks_done": "✅ 追读力修复后跨维度检查完成",
        "reading_power_final_eval": "✅ 最终追读力报告已归档",
        "humanize_scan": "🔎 AI 去痕扫描完成",
        "reading_power_repair_loop_complete": "✅ 追读力修复流程完成",
        "test": "✅ 一致性检查通过",
        "persist": "✅ Canon 状态更新完成",
        "chapter_compact": "🧹 章节级 Canon 压缩完成",
        "creative_report": "📊 创作报告生成",
        "volume_audit": "📚 卷末审计完成",
        "volume_compact": "🧹 Canon 压缩完成",
        "volume_audit_failed": "⚠️  卷末审计失败（已跳过）",
        "enrich_introduced_done": "✨ 新角色档案已基于正文精化",
        "enrich_introduced_failed": "⚠️  角色档案精化失败（已跳过）",
        "memory_indexing_started": "🧠 记忆索引启动",
        "memory_indexing_complete": "🧠 记忆索引完成",
        "memory_summary_generated": "📝 章节摘要完成",
        "memory_motifs_completed": "🎯 母题提取完成",
        "memory_concurrent_tasks_started": "🧠 记忆并发任务执行中",
        "memory_concurrent_tasks_done": "✅ 记忆并发任务完成",
        "memory_flush": "🧵 记忆异步任务收尾",
        "memory_updated": "💾 记忆状态保存完成",
        # P0-1: 记忆补偿和失败事件
        "memory_gap_compensated": "✅ 记忆缺口已补偿",
        "memory_update_failed": "⚠️  记忆更新失败（已标记，待下章补偿）",
        "budget_status": "💰 预算状态通知",
        "style_drift_warning": "📉 风格趋势下降警告",
        "early_stop": "⭐ 质量优秀，提前完成编辑",
    },
    "polish_chapter": {
        "polish_start": "🔍 准备精修上下文",
        "polish": "✨ 精修润色完成",
    },
    "book_consistency": {
        "book_consistency_start": "🔍 收集全书上下文",
        "book_consistency_issue_pool_ready": "🧭 问题池锚点已就绪",
        "book_consistency_two_phase_start": "🔎 智能漏斗摘要筛查开始",
        "book_consistency_two_phase_summary_reused": "⏩ 复用智能漏斗摘要筛查",
        "book_consistency_two_phase_summary_done": "🔎 智能漏斗摘要筛查完成",
        "book_consistency_two_phase_summary_only": "✅ 摘要筛查未发现需深审章节",
        "book_consistency_two_phase_targeted_capped": "🎯 智能漏斗目标章节已收敛",
        "book_consistency_two_phase_expanded": "🎯 已扩展至全部命中章节",
        "book_consistency_two_phase_targeted_start": "📖 定向全文深审开始",
        "book_consistency_chunk_progress": "📖 全书分批审计推进中",
        "book_consistency": "📋 全书一致性审计推进中",
        "book_consistency_audit_saved": "🗂️ 审计结果已归档",
        "book_consistency_checkpoint_result_reused": "⏩ 复用审计检查点结果",
        "book_consistency_verify_start": "🔍 逐章验证审计问题开始",
        "book_consistency_verify_progress": "🔍 逐章验证审计问题推进中",
        "book_consistency_verify_done": "✅ 审计问题验证完成",
        "book_consistency_repair_start": "🛠️ 全书修复任务排程完成",
        "book_consistency_repair_classification": "🧭 修复任务已分流",
        "book_consistency_repair_progress": "🧩 全书逐章修复推进中",
        "book_consistency_repair_review": "🔎 修复后复核完成",
        "book_consistency_post_audit_start": "🔎 修复后二次小审计开始",
        "book_consistency_post_audit_done": "✅ 修复后二次小审计完成",
        "book_consistency_rollback": "↩️ 修复失败，已回滚",
        "book_consistency_auto_continue": "🔁 继续修复剩余章节",
        "book_consistency_stuck": "⚠️ 续修无新增进展",
        "book_consistency_auto_continue_done": "✅ 自动续修结束",
        "book_consistency_backup_created": "💾 全书审计修复备份已创建",
        "book_consistency_report_written": "🗂️ 全书审计报告已归档",
        "book_consistency_repair_report_written": "📚 全书修复报告已归档",
    },
    "reevaluate_chapter": {
        "reevaluate_start": "🔁 准备章节重新评估",
        "evaluate": "✅ 质量评估完成",
        "continuity_eval_after_repair": "✅ 连贯性复评完成",
        "causal_eval_after_repair": "✅ 因果链复评完成",
        "reading_power_eval": "✅ 追读力复评完成",
        "reading_power_eval_warning": "⚠️ 追读力复评失败（已跳过）",
        "reevaluate_chapter": "✅ 报告已刷新",
    },
    "repair_causal": {
        "repair_causal_start": "🛠️ 准备因果链修复",
        "causal_eval_after_repair_start": "🧪 因果修复后复查",
        "causal_eval_after_repair": "✅ 因果修复复查完成",
        "causal_eval_after_repair_warning": "⚠️ 因果修复复查失败（已跳过）",
        "repair_causal": "✅ 因果链修复完成",
    },
    "export_book": {
        "export": "📤 导出完成",
    },
    "repair_motif_history": {
        "motif_history_repair_start": "🧩 母题历史修补启动",
        "motif_repair_layer1_start": "📊 重建缓存统计",
        "motif_repair_layer1_done": "✅ 缓存统计完成",
        "motif_repair_layer2_start": "📖 开始重新提取母题",
        "motif_repair_layer2_scanning": "🔍 扫描章节文件",
        "motif_repair_layer2_progress": "⏳ 逐章处理",
        "motif_repair_layer2_done": "✅ 重新提取完成",
        "motif_history_repair_done": "✅ 母题历史修补完成",
        "motif_history_repair_error": "⚠️ 母题历史修补失败",
    },
}


def summary_steps(kind: str) -> list[StepVisualSpec]:
    """Return shared milestone definitions for a job kind."""
    return list(_SUMMARY_STEPS.get(kind, ()))


def resolve_step_key(kind: str, raw_key: str) -> str:
    """Map raw step events to shared milestone keys."""
    if raw_key in _COMMON_STEP_ALIASES:
        return _COMMON_STEP_ALIASES[raw_key]
    if kind in {"tts_synthesize", "tts_full_pipeline", "tts_post_archive"}:
        if raw_key in _TTS_STEP_ALIASES:
            return _TTS_STEP_ALIASES[raw_key]
        mapped_tts = _resolve_prefixed_alias(raw_key, _TTS_STEP_PREFIX_ALIASES)
        return mapped_tts if mapped_tts is not None else raw_key
    if kind == "run_chapter":
        if raw_key in _RUN_CHAPTER_STEP_MAP:
            return _RUN_CHAPTER_STEP_MAP[raw_key]
        for prefix, mapped in _RUN_CHAPTER_PREFIX_GROUPS:
            if raw_key.startswith(prefix):
                return mapped
        return raw_key
    if kind == "prepare_chapter":
        if raw_key in _PREPARE_CHAPTER_STEP_MAP:
            return _PREPARE_CHAPTER_STEP_MAP[raw_key]
        return raw_key
    if kind == "resolve_chapter_checkpoint_finalize":
        if raw_key in _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP:
            return _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP[raw_key]
        for prefix, mapped in _RESOLVE_CHECKPOINT_FINALIZE_STEP_MAP.items():
            if prefix.endswith("_") and raw_key.startswith(prefix):
                return mapped
    if kind in {"resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"}:
        if raw_key in _RESOLVE_CHECKPOINT_STEP_MAP:
            return _RESOLVE_CHECKPOINT_STEP_MAP[raw_key]
        for prefix, mapped in _RESOLVE_CHECKPOINT_STEP_MAP.items():
            if prefix.endswith("_") and raw_key.startswith(prefix):
                return mapped
        return raw_key
    if kind == "run_short" and raw_key in _SHORT_STEP_ALIASES:
        return _SHORT_STEP_ALIASES[raw_key]
    if kind == "run_short":
        mapped_alias = _resolve_prefixed_alias(raw_key, _SHORT_STEP_PREFIX_ALIASES)
        if mapped_alias is not None:
            return mapped_alias
    if kind == "init_long":
        if raw_key in _INIT_LONG_STEP_ALIASES:
            return _INIT_LONG_STEP_ALIASES[raw_key]
        init_mapped_alias: str | None = _resolve_prefixed_alias(
            raw_key,
            _INIT_LONG_STEP_PREFIX_ALIASES,
        )
        if init_mapped_alias is not None:
            return init_mapped_alias
    if kind == "repair_continuity":
        if raw_key in _REPAIR_CONTINUITY_STEP_MAP:
            return _REPAIR_CONTINUITY_STEP_MAP[raw_key]
    if kind == "repair_causal":
        if raw_key in _REPAIR_CAUSAL_STEP_MAP:
            return _REPAIR_CAUSAL_STEP_MAP[raw_key]
    if kind == "repair_issues":
        if raw_key in _REPAIR_ISSUES_STEP_MAP:
            return _REPAIR_ISSUES_STEP_MAP[raw_key]
    if kind == "reevaluate_chapter":
        if raw_key in _REEVALUATE_CHAPTER_STEP_MAP:
            return _REEVALUATE_CHAPTER_STEP_MAP[raw_key]
    if kind == "polish_chapter":
        if raw_key in _POLISH_STEP_MAP:
            return _POLISH_STEP_MAP[raw_key]
    if kind == "book_consistency":
        if raw_key in _BOOK_CONSISTENCY_STEP_MAP:
            return _BOOK_CONSISTENCY_STEP_MAP[raw_key]
    if kind == "reextract_relationships":
        if raw_key in _REEXTRACT_STEP_MAP:
            return _REEXTRACT_STEP_MAP[raw_key]
    return raw_key


def _format_step_span(raw_key: str, prefix: str) -> str:
    """Extract chapter range span from a prefixed step key like 'plan_outline_batch_1_5'."""
    suffix = raw_key[len(prefix) :]
    parts = suffix.split("_", 1)
    if len(parts) != 2:
        return ""
    try:
        start = int(parts[0])
        end = int(parts[1])
    except ValueError:
        return ""
    return f"{start}-{end}章"


def display_step_name(raw_key: str) -> str:
    """Human-readable display name for a raw step key."""
    if raw_key.startswith("edit_"):
        try:
            round_num = int(raw_key.split("_", 1)[1])
        except (IndexError, ValueError):
            if raw_key == "edit_":
                return "自适应修订"
        else:
            return f"自适应修订（第 {round_num} 轮）"
    if raw_key == "book_consistency_verify_":
        return "逐章验证"
    for prefix, label in (
        ("plan_outline_batch_", "章节大纲分批生成"),
        ("plan_outline_continue_", "章节大纲续写"),
        ("plan_outline_repair_", "章节大纲缺失修复"),
        ("plan_chapter_contracts_batch_", "章节契约分批生成"),
        ("adjudicate_contract_coherence_batch_", "契约裁判分批检查"),
    ):
        if raw_key.startswith(prefix):
            span = _format_step_span(raw_key, prefix)
            return f"{label}（{span}）" if span else label
    for prefix, label in (
        ("draft_segment_", "分段初稿生成"),
        ("short_segment_bridge_", "分段桥接生成"),
    ):
        if raw_key.startswith(prefix):
            try:
                segment_num = int(raw_key.removeprefix(prefix))
            except ValueError:
                return label
            return f"{label}（第 {segment_num} 段）"
    if raw_key in _STEP_DISPLAY_NAMES:
        return _STEP_DISPLAY_NAMES[raw_key]
    dynamic_label = _display_dynamic_dimension_step(raw_key)
    if dynamic_label:
        return dynamic_label
    return raw_key


# ── Engine-facing step label formatting ────────────────────────────────────────
# Single source of truth for user-facing step labels on engine surfaces
# (app_service / API projections).  desktop/progress.py imports these tables and
# keeps its own formatters in sync with the helpers below.

INIT_COHERENCE_STAGE_LABELS: dict[str, str] = {
    "blueprint_coherence": "叙事蓝图",
    "outline_inheritance": "章节大纲",
    "contract_coherence": "章节契约",
}

INIT_COHERENCE_VERDICT_LABELS: dict[str, str] = {
    "accept": "通过",
    "defer": "延后",
    "ambiguous": "需确认",
    "needs_repair": "需修复",
    "reject": "未通过",
}

INIT_COHERENCE_ARTIFACT_LABELS: dict[str, str] = {
    "spec": "故事规格",
    "story_bible": "世界观设定",
    "character_bible": "角色设定",
    "character_system": "角色系统",
    "entity_registry": "实体注册表",
    "entity_graph": "实体图谱",
    "creative_director_packet": "创作导演包",
    "blueprint": "叙事蓝图",
    "outline": "章节大纲",
    "narrative_contract": "叙事契约",
    "chapter_contracts": "章节契约",
}


def _looks_like_raw_step_label(label: str) -> bool:
    """Return True when a display label still looks like an untranslated step key."""
    stripped = label.strip()
    if not stripped:
        return False
    return all(ch.isascii() and (ch.isalnum() or ch in {"_", "-", ":", "."}) for ch in stripped)


def summary_label_for_kind(kind: str, step_key: str) -> str:
    """Return the visible task-flow label for a normalized summary step key."""
    if not step_key:
        return ""
    for spec in summary_steps(kind):
        if spec.key == step_key:
            return spec.label
        if spec.is_prefix and step_key.startswith(spec.key):
            return spec.label
    return ""


def _coherence_batch_progress(payload: dict[str, Any]) -> tuple[object, object]:
    """Return displayed completed batches and total for init-coherence events.

    Claim extraction runs concurrently, so the raw chunk index can complete out
    of order.  ``batches_done`` is the monotonic completed count; legacy events
    fall back to ``batch``.
    """
    return payload.get("batches_done", payload.get("batch")), payload.get("batch_total")


def _coherence_artifact_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return INIT_COHERENCE_ARTIFACT_LABELS.get(raw, raw)


def _coherence_stage_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return INIT_COHERENCE_STAGE_LABELS.get(raw, raw)


def _coherence_verdict_label(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return INIT_COHERENCE_VERDICT_LABELS.get(raw, raw)


def format_init_long_claims_step(base: str, payload: dict[str, Any]) -> str:
    """Append claim-extraction detail (batch / artifact / counts) to *base*."""
    parts: list[str] = []
    batch, batch_total = _coherence_batch_progress(payload)
    if (
        isinstance(batch, (int, float))
        and isinstance(batch_total, (int, float))
        and batch_total > 0
    ):
        parts.append(f"{int(batch)} / {int(batch_total)}")
    artifact = _coherence_artifact_label(payload.get("artifact"))
    if artifact:
        parts.append(f"当前检查：{artifact}")
    claims = payload.get("claims")
    if isinstance(claims, (int, float)):
        claim_label = (
            "本批抽取"
            if str(payload.get("extraction_mode") or "").strip() == "stream"
            else "已抽取"
        )
        parts.append(f"{claim_label} {int(claims)} 条")
    fallback_claims = payload.get("fallback_claims")
    if isinstance(fallback_claims, (int, float)) and fallback_claims > 0:
        parts.append(f"本地兜底 {int(fallback_claims)} 条")
    filtered_claims = payload.get("filtered_claims")
    if isinstance(filtered_claims, (int, float)) and filtered_claims > 0:
        parts.append(f"过滤低信号 {int(filtered_claims)} 条")
    max_parallel = payload.get("max_parallel")
    if isinstance(max_parallel, (int, float)) and max_parallel > 1:
        parts.append(f"并发 {int(max_parallel)}")
    if payload.get("cached"):
        source = str(payload.get("source") or "").strip()
        parts.append("已复用账本" if source == "claim_ledger" else "已复用缓存")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def format_init_long_candidate_retrieval_step(base: str, payload: dict[str, Any]) -> str:
    """Append conflict-candidate retrieval detail (stage / claims / candidates)."""
    parts: list[str] = []
    stage = _coherence_stage_label(payload.get("stage"))
    if stage:
        parts.append(f"当前层：{stage}")
    active_claims = payload.get("active_claims", payload.get("claims"))
    extracted_claims = payload.get("extracted_claims")
    if isinstance(active_claims, (int, float)):
        if isinstance(extracted_claims, (int, float)) and int(extracted_claims) != int(
            active_claims
        ):
            parts.append(
                f"活跃一致性 Claims {int(active_claims)} / 抽取 {int(extracted_claims)}"
            )
        else:
            parts.append(f"一致性 Claims {int(active_claims)}")
    candidates = payload.get("candidates")
    if isinstance(candidates, (int, float)):
        parts.append(f"候选 {int(candidates)}")
    if payload.get("degraded_memory"):
        parts.append("语义召回降级")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def format_init_long_candidate_adjudication_step(base: str, payload: dict[str, Any]) -> str:
    """Append candidate adjudication detail (stage / batch / verdict)."""
    parts: list[str] = []
    stage = _coherence_stage_label(payload.get("stage"))
    if stage:
        parts.append(f"当前层：{stage}")
    batch = payload.get("batch")
    batch_total = payload.get("batch_total")
    if (
        isinstance(batch, (int, float))
        and isinstance(batch_total, (int, float))
        and batch_total > 0
    ):
        parts.append(f"批次 {int(batch)} / {int(batch_total)}")
    max_parallel = payload.get("max_parallel")
    if isinstance(max_parallel, (int, float)) and max_parallel > 1:
        parts.append(f"并发 {int(max_parallel)}")
    candidates = payload.get("candidates")
    if isinstance(candidates, (int, float)):
        parts.append(f"候选 {int(candidates)}")
    issues = payload.get("issues", payload.get("issue_count"))
    if isinstance(issues, (int, float)):
        parts.append(f"问题 {int(issues)}")
    high_or_critical = payload.get("high_or_critical")
    if isinstance(high_or_critical, (int, float)) and high_or_critical > 0:
        parts.append(f"高危 {int(high_or_critical)}")
    verdict = _coherence_verdict_label(payload.get("verdict"))
    if verdict:
        parts.append(f"判定：{verdict}")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def format_init_long_recheck_step(base: str, payload: dict[str, Any]) -> str:
    """Append coherence recheck chunk detail (chunk index / focus chapters)."""
    parts: list[str] = []
    chunk_index = payload.get("chunk_index")
    chunk_count = payload.get("chunk_count")
    if (
        isinstance(chunk_index, (int, float))
        and isinstance(chunk_count, (int, float))
        and chunk_count > 0
    ):
        parts.append(f"分块 {int(chunk_index)} / {int(chunk_count)}")
    stage = _coherence_stage_label(payload.get("stage"))
    if stage:
        parts.append(f"当前层：{stage}")
    focus_chapters = payload.get("focus_chapters")
    if isinstance(focus_chapters, list) and focus_chapters:
        chapter_text = "、".join(str(number) for number in focus_chapters[:6])
        parts.append(f"聚焦第 {chapter_text} 章")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def format_step_label(
    kind: str,
    step: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Return a user-facing step label with per-kind detail for engine surfaces.

    Mirrors desktop/progress.py ``display_step_name_for_job`` semantics for the
    init-long coherence family (claims / retrieval / adjudication / recheck);
    other steps fall back to ``display_step_name``.
    """
    raw_step = str(step or "").strip()
    if not raw_step:
        return ""
    step_payload = payload if isinstance(payload, dict) else {}
    base = display_step_name(raw_step)
    resolved = resolve_step_key(kind, raw_step)
    summary_label = summary_label_for_kind(kind, resolved)
    if summary_label and _looks_like_raw_step_label(base):
        base = summary_label
    if (
        summary_label
        and summary_label != base
        and summary_label not in base
        and not base.startswith(f"{summary_label} · ")
    ):
        base = f"{summary_label} · {base}"
    if kind == "init_long":
        if raw_step == "extract_init_coherence_claims":
            return format_init_long_claims_step(base, step_payload)
        if raw_step == "retrieve_init_conflict_candidates":
            return format_init_long_candidate_retrieval_step(base, step_payload)
        if raw_step.startswith("adjudicate_init_conflict_candidates"):
            return format_init_long_candidate_adjudication_step(base, step_payload)
        if raw_step.startswith("init_coherence_recheck"):
            return format_init_long_recheck_step(base, step_payload)
    return base


def cli_step_labels(kind: str) -> dict[str, str]:
    """Return CLI progress labels for a job kind."""
    return dict(_CLI_STEP_LABELS.get(kind, {}))


def compute_progress_percent(kind: str, status: str, current_step: str) -> int:
    """Estimate a 0-100 completion percentage for a job."""
    if status == "succeeded":
        return 100
    if status == "queued":
        return 0
    if status == "paused":
        status = "running"
    current_step = _normalize_progress_step(kind, current_step)
    if not current_step:
        return 3 if status == "running" else 10
    if kind == "run_short":
        if current_step.startswith("edit_"):
            try:
                round_num = int(current_step.split("_", 1)[1])
            except (IndexError, ValueError):
                return 65
            return _PROGRESS_SHORT.get(f"edit_{round_num}", 70)
        return _PROGRESS_SHORT.get(current_step, 50)
    if kind == "init_long":
        return _PROGRESS_INIT_LONG.get(current_step, 50)
    if kind == "run_chapter":
        return _PROGRESS_CHAPTER.get(current_step, 50)
    if kind == "prepare_chapter":
        return _PROGRESS_PREPARE_CHAPTER.get(current_step, 50)
    if kind == "resolve_chapter_checkpoint":
        return _PROGRESS_RESOLVE_CHECKPOINT.get(current_step, 50)
    if kind == "resolve_chapter_checkpoint_finalize":
        return _PROGRESS_RESOLVE_CHECKPOINT_FINALIZE.get(current_step, 50)
    if kind == "repair_continuity":
        return _PROGRESS_REPAIR_CONTINUITY.get(current_step, 50)
    if kind == "repair_causal":
        return _PROGRESS_REPAIR_CAUSAL.get(current_step, 50)
    if kind == "repair_issues":
        return _PROGRESS_REPAIR_ISSUES.get(current_step, 50)
    if kind == "reevaluate_chapter":
        return _PROGRESS_REEVALUATE_CHAPTER.get(current_step, 50)
    if kind == "polish_chapter":
        return _PROGRESS_POLISH.get(current_step, 50)
    if kind == "book_consistency":
        return _PROGRESS_BOOK_CONSISTENCY.get(current_step, 50)
    if kind == "export_book":
        return _PROGRESS_EXPORT.get(current_step, 90)
    if kind == "reextract_relationships":
        return _PROGRESS_REEXTRACT.get(current_step, 10)
    if kind == "repair_motif_history":
        return _PROGRESS_REPAIR_MOTIF_HISTORY.get(current_step, 50)
    if kind in {"tts_synthesize", "tts_full_pipeline", "tts_post_archive"}:
        return _PROGRESS_TTS.get(current_step, 10)
    return 10
