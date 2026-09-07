"""Tests for shared CLI/Desktop progress metadata."""

from __future__ import annotations

import ast
from pathlib import Path

from novel_forge.pipeline.progress import (
    cli_step_labels,
    compute_progress_percent,
    display_step_name,
    parallel_groups_for,
    resolve_step_key,
    summary_steps,
)

_STEP_EVENT_CALL_NAMES = {"_on_step", "on_step", "on_step_progress"}
_STEP_EVENT_ATTR_NAMES = {"_on_step", "on_step", "on_step_progress"}


def _literal_strings(node: ast.AST) -> set[str]:
    """Return literal strings embedded in a simple expression tree."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body) | _literal_strings(node.orelse)
    return set()


_SUMMARY_JOB_KINDS = (
    "run_short",
    "init_long",
    "run_chapter",
    "prepare_chapter",
    "resolve_chapter_checkpoint",
    "resolve_chapter_checkpoint_finalize",
    "polish_chapter",
    "repair_continuity",
    "repair_causal",
    "repair_issues",
    "reevaluate_chapter",
    "book_consistency",
    "export_book",
    "reextract_relationships",
    "repair_motif_history",
    "rebuild_memory_vectors",
    "tts_synthesize",
    "tts_full_pipeline",
    "tts_post_archive",
)


def _literal_pipeline_step_events() -> set[str]:
    """Collect literal desktop progress events from novel and TTS execution paths.

    The task-flow UI consumes events from the shared pipeline, workspace
    adapters, and the TTS pipeline. Keep this inventory close to the display
    mapping so a new event cannot silently expose its internal snake_case key.
    """
    search_roots = (
        Path("novel_forge/pipeline"),
        Path("novel_forge/workspace"),
        Path("novel_forge/tts"),
    )
    step_events: set[str] = set()
    for root in search_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if isinstance(func, ast.Name):
                    matched = func.id in _STEP_EVENT_CALL_NAMES
                elif isinstance(func, ast.Attribute):
                    matched = func.attr in _STEP_EVENT_ATTR_NAMES
                else:
                    matched = False
                if not matched:
                    if isinstance(func, ast.Name) and func.id == "_emit_tts_progress":
                        if len(node.args) >= 2:
                            step_events.update(_literal_strings(node.args[1]))
                    elif isinstance(func, ast.Name) and func.id == "_emit":
                        if len(node.args) >= 2:
                            step_events.update(_literal_strings(node.args[1]))
                    elif isinstance(func, ast.Attribute) and func.attr == "_emit":
                        step_events.update(_literal_strings(node.args[0]))
                    elif isinstance(func, ast.Name) and func.id == "forward_step_event":
                        if len(node.args) >= 3:
                            step_events.update(_literal_strings(node.args[2]))
                    elif isinstance(func, ast.Name) and func.id == "log_prompt_diagnostics":
                        for keyword in node.keywords:
                            if keyword.arg == "event":
                                step_events.update(_literal_strings(keyword.value))
                    continue
                step_events.update(_literal_strings(node.args[0]))
    return step_events


def test_summary_steps_returns_expected_milestones() -> None:
    steps = summary_steps("run_chapter")

    assert [step.key for step in steps] == [
        "state_packet",
        "chapter_research",
        "bridge",
        "plan",
        "draft",
        "wave",
        "opening_guard",
        "alignment",
        "continuity_repair",
        "alignment_repair",
        "guard_review",
        "causal_repair",
        "reading_power_repair",
        "polish",
        "humanize",
        "extract_canon",
        "persist",
        "memory_updated",
    ]


def test_run_short_summary_tracks_parallel_style_and_quality_steps() -> None:
    steps = summary_steps("run_short")

    assert [step.key for step in steps] == [
        "spec",
        "chapter_research",
        "short_blueprint_elements",
        "short_blueprint",
        "short_profile_style",
        "beats",
        "short_execution_plan",
        "draft",
        "edit_",
        "short_completeness_check",
        "evaluate",
        "creative_summary",
    ]
    assert ("short_profile_style", "beats") in parallel_groups_for("run_short")


def test_init_long_summary_order_matches_parallel_execution() -> None:
    steps = summary_steps("init_long")

    assert [step.key for step in steps] == [
        "spec",
        "init_web_research",
        "init_story_bible",
        "plan_blueprint_elements",
        "init_character_bible",
        "init_character_system",
        "profile_style",
        "init_entity_registry",
        "init_entity_graph",
        "creative_director_packet",
        "plan_blueprint",
        "plan_blueprint_validated",
        "plan_blueprint_repaired",
        "build_init_coherence_profile",
        "extract_init_coherence_claims",
        "retrieve_init_conflict_candidates",
        "adjudicate_init_conflict_candidates",
        "adjudicate_blueprint_coherence",
        "repair_init_artifact_patch",
        "derive_editorial_contract",
        "plan_blueprint_fragments",
        "plan_chapter_design_matrix",
        "plan_outline",
        "adjudicate_outline_inheritance",
        "init_narrative_contract",
        "plan_chapter_contracts",
        "adjudicate_contract_coherence",
        "init_claim_contract_coverage",
        "init_source_artifacts",
        "init_readiness",
        "canon_state",
    ]
    assert ("init_story_bible", "plan_blueprint_elements") in parallel_groups_for("init_long")
    assert ("profile_style", "init_entity_registry") in parallel_groups_for("init_long")
    assert ("profile_style", "init_entity_graph") in parallel_groups_for("init_long")
    assert ("plan_blueprint_suspense", "plan_blueprint_ending") not in parallel_groups_for(
        "init_long"
    )
    assert ("profile_style", "plan_blueprint") not in parallel_groups_for("init_long")


def test_targeted_repair_summaries_follow_runtime_event_order() -> None:
    assert [step.key for step in summary_steps("repair_continuity")] == [
        "repair_continuity_start",
        "continuity_eval_after_repair",
        "repair_continuity",
    ]
    assert [step.key for step in summary_steps("repair_causal")] == [
        "repair_causal_start",
        "causal_eval_after_repair",
        "repair_causal",
    ]


def test_tts_summary_exposes_visible_pipeline_and_maps_raw_events() -> None:
    assert [step.key for step in summary_steps("tts_post_archive")] == [
        "tts_prepare",
        "tts_script",
        "tts_synthesis",
        "tts_post",
        "tts_delivery",
    ]
    assert resolve_step_key("tts_post_archive", "tts_script_llm_call") == "tts_script"
    assert resolve_step_key("tts_post_archive", "voice_catalog_sync_start") == "tts_prepare"
    assert resolve_step_key("tts_post_archive", "tts_segment_7") == "tts_synthesis"
    assert resolve_step_key("tts_post_archive", "tts_assembly_complete") == "tts_post"
    assert resolve_step_key("tts_post_archive", "tts_auto_trigger_completed") == "tts_delivery"
    assert display_step_name("tts_script_llm_call") == "分析台词与情绪"
    assert compute_progress_percent("tts_post_archive", "running", "tts_script_llm_call") == 28


def test_resolve_step_key_maps_run_chapter_detail_steps() -> None:
    assert resolve_step_key("run_chapter", "chapter_research_start") == "chapter_research"
    assert resolve_step_key("run_chapter", "chapter_research_cache_hit") == "chapter_research"
    assert resolve_step_key("run_chapter", "continuity_eval_after_repair") == "continuity_repair"
    assert resolve_step_key("run_chapter", "reading_power_prerepair_eval") == "reading_power_repair"
    assert resolve_step_key("run_chapter", "reading_power_repair_loop_complete") == "reading_power_repair"
    assert resolve_step_key("run_chapter", "guard_ticket_repair_complete") == "guard_review"
    assert resolve_step_key("run_chapter", "word_count_archive_gate") == "persist"
    assert resolve_step_key("run_chapter", "element_progress_scheduled") == "bridge"
    assert resolve_step_key("run_chapter", "wave") == "wave"
    assert resolve_step_key("run_chapter", "element_progress_updated") == "memory_updated"
    assert resolve_step_key("run_chapter", "macro_guard_audit") == "persist"
    assert resolve_step_key("run_chapter", "post_polish_reaudit_skipped") == "polish"
    assert resolve_step_key("run_chapter", "reading_power_post_polish_eval") == "polish"
    assert resolve_step_key("run_chapter", "causal_eval_after_polish") == "polish"
    assert resolve_step_key("run_chapter", "continuity_eval_after_polish") == "polish"
    assert resolve_step_key("run_chapter", "continuity_eval_after_text_change") == "polish"
    assert (
        resolve_step_key("run_chapter", "quality_reports_refresh_after_text_change")
        == "polish"
    )
    assert resolve_step_key("run_chapter", "edit_2") == "wave"
    assert resolve_step_key("run_chapter", "memory_indexing_complete") == "memory_updated"
    assert resolve_step_key("run_short", "early_stop") == "edit_"
    assert resolve_step_key("run_short", "chapter_research_skipped") == "chapter_research"
    assert resolve_step_key("run_short", "short_adaptive_revision_round") == "edit_"
    assert resolve_step_key("run_short", "short_adaptive_revision_rollback") == "edit_"
    assert resolve_step_key("run_short", "short_profile_style_failed") == "short_profile_style"
    assert resolve_step_key("run_short", "short_segment_bridge_2") == "draft"
    assert resolve_step_key("run_short", "draft_segment_2") == "draft"
    assert resolve_step_key("run_short", "short_completion_repair") == "short_completeness_check"
    assert resolve_step_key("init_long", "plan_outline") == "plan_outline"
    assert (
        resolve_step_key("init_long", "derive_init_coherence_profile")
        == "build_init_coherence_profile"
    )
    assert (
        resolve_step_key("init_long", "refine_init_coherence_profile_start")
        == "build_init_coherence_profile"
    )
    assert (
        resolve_step_key("init_long", "build_init_coherence_profile_start")
        == "build_init_coherence_profile"
    )


def test_init_long_runtime_trace_maps_to_non_decreasing_summary_steps() -> None:
    order = {step.key: index for index, step in enumerate(summary_steps("init_long"))}
    events = [
        "spec",
        "init_story_bible",
        "plan_blueprint_elements",
        "init_character_bible",
        "init_character_system",
        "profile_style",
        "init_entity_registry",
        "init_entity_graph",
        "creative_director_packet",
        "plan_blueprint",
        "plan_blueprint_validated",
        "plan_blueprint_repaired",
        "build_init_coherence_profile",
        "plan_blueprint_fragments",
        "plan_outline_starting",
        "plan_outline_batch_1_5",
        "plan_outline",
        "init_narrative_contract",
        "plan_chapter_contracts",
        "adjudicate_contract_coherence_batch_1_12",
        "adjudicate_contract_coherence",
        "canon_state",
    ]

    indexes = [order[resolve_step_key("init_long", step)] for step in events]

    assert indexes == sorted(indexes)


def test_run_chapter_runtime_trace_maps_to_non_decreasing_summary_steps() -> None:
    order = {step.key: index for index, step in enumerate(summary_steps("run_chapter"))}
    events = [
        "state_packet",
        "context_compress",
        "bridge",
        "element_progress_scheduled",
        "plan",
        "draft",
        "alignment",
        "continuity_eval",
        "causal_validation",
        "reading_power_repair_loop_complete",
        "polish_reextract_canon",
        "post_polish_reaudit_skipped",
        "reading_power_post_polish_eval",
        "macro_guard_audit",
        "memory_updated",
    ]

    indexes = [order[resolve_step_key("run_chapter", step)] for step in events]

    assert indexes == sorted(indexes)
    assert (
        resolve_step_key("book_consistency", "repair_continuity_start")
        == "book_consistency_repair_"
    )
    assert (
        resolve_step_key("book_consistency", "book_consistency_two_phase_start")
        == "book_consistency"
    )
    assert (
        resolve_step_key("book_consistency", "book_consistency_chunk_progress")
        == "book_consistency"
    )
    assert (
        resolve_step_key("book_consistency", "book_consistency_auto_continue")
        == "book_consistency_repair_"
    )
    assert (
        resolve_step_key("book_consistency", "book_consistency_repair_review")
        == "book_consistency_repair_"
    )
    assert (
        resolve_step_key("book_consistency", "book_consistency_repair_report_written")
        == "book_consistency_report_written"
    )


def test_book_consistency_summary_has_four_high_level_phases() -> None:
    steps = summary_steps("book_consistency")

    assert [step.key for step in steps] == [
        "book_consistency_start",
        "book_consistency",
        "book_consistency_repair_",
        "book_consistency_report_written",
    ]
    assert steps[2].is_prefix is True  # repair step matches by prefix


def test_reevaluate_summary_tracks_parallel_evaluators() -> None:
    steps = summary_steps("reevaluate_chapter")

    assert [step.key for step in steps] == [
        "reevaluate_start",
        "evaluate",
        "continuity_eval_after_repair",
        "causal_eval_after_repair",
        "reading_power_eval",
        "reevaluate_chapter",
    ]
    assert ("evaluate", "continuity_eval_after_repair") in parallel_groups_for("reevaluate_chapter")
    assert ("evaluate", "causal_eval_after_repair") in parallel_groups_for("reevaluate_chapter")
    assert ("evaluate", "reading_power_eval") in parallel_groups_for("reevaluate_chapter")
    assert (
        resolve_step_key("reevaluate_chapter", "continuity_eval_after_repair_start")
        == "continuity_eval_after_repair"
    )
    assert (
        resolve_step_key("reevaluate_chapter", "causal_eval_after_repair_warning")
        == "causal_eval_after_repair"
    )
    assert resolve_step_key("reevaluate_chapter", "reading_power_eval") == "reading_power_eval"
    assert (
        resolve_step_key("reevaluate_chapter", "reading_power_eval_warning") == "reading_power_eval"
    )
    assert resolve_step_key("reevaluate_chapter", "reevaluate_start") == "reevaluate_start"


def test_resolve_checkpoint_summary_exposes_hidden_quality_substeps() -> None:
    steps = summary_steps("resolve_chapter_checkpoint")

    assert [step.key for step in steps] == [
        "plan_checkpoint",
        "draft",
        "pre_alignment",
        "continuity_repair",
        "alignment_repair",
        "post_alignment",
        "guard_checkpoint",
    ]


def test_resolve_checkpoint_finalize_summary_shows_only_archive_steps() -> None:
    steps = summary_steps("resolve_chapter_checkpoint_finalize")

    assert [step.key for step in steps] == [
        "guard_checkpoint",
        "post_guard_repair",
        "polish_reextract_canon",
        "persist",
        "evaluate",
        "volume_audit",
        "memory_updated",
    ]


def test_summary_step_progress_never_runs_backward() -> None:
    for kind in _SUMMARY_JOB_KINDS:
        steps = summary_steps(kind)
        progress_values = [compute_progress_percent(kind, "running", step.key) for step in steps]

        assert progress_values == sorted(progress_values), kind


def test_resolve_checkpoint_finalize_progress_is_remapped() -> None:
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize", "running", "guard_checkpoint"
        )
        == 10
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize", "running", "post_guard_repair"
        )
        == 30
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize", "running", "polish_reextract_canon"
        )
        == 52
    )
    assert (
        compute_progress_percent("resolve_chapter_checkpoint_finalize", "running", "persist") == 70
    )
    assert (
        compute_progress_percent("resolve_chapter_checkpoint_finalize", "running", "evaluate") == 82
    )
    assert (
        compute_progress_percent("resolve_chapter_checkpoint_finalize", "running", "volume_audit")
        == 92
    )
    assert (
        compute_progress_percent("resolve_chapter_checkpoint_finalize", "running", "memory_updated")
        == 99
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize",
            "running",
            "quality_reports_refresh_after_text_change",
        )
        == 52
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize",
            "running",
            "continuity_eval_after_text_change",
        )
        == 52
    )


def test_resolve_step_key_maps_finalize_checkpoint_steps() -> None:
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "accept_and_finalize")
        == "guard_checkpoint"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "apply_repairs_and_finalize")
        == "guard_checkpoint"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "post_guard_repair_start")
        == "post_guard_repair"
    )
    assert (
        resolve_step_key(
            "resolve_chapter_checkpoint_finalize",
            "quality_reports_refresh_after_text_change",
        )
        == "polish_reextract_canon"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "continuity_eval_after_text_change")
        == "polish_reextract_canon"
    )
    assert resolve_step_key("resolve_chapter_checkpoint_finalize", "persist") == "persist"
    assert resolve_step_key("resolve_chapter_checkpoint_finalize", "evaluate") == "evaluate"
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "memory_concurrent_tasks_started")
        == "memory_updated"
    )
    # guard_constraint_compliance_check must map to guard_checkpoint instead of
    # falling through to the phase-1 checkpoint map, where it resolves to the
    # finalize-invisible post_alignment step.
    assert (
        resolve_step_key("resolve_chapter_checkpoint_finalize", "guard_constraint_compliance_check")
        == "guard_checkpoint"
    )


def test_resolve_step_key_maps_checkpoint_steps() -> None:
    # audit / pre-alignment events map to the new visible pre_alignment step
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "audit_context_preparing") == "pre_alignment"
    )
    assert resolve_step_key("resolve_chapter_checkpoint", "critique_completed") == "pre_alignment"

    # quality-check report events stay in the quality stage
    assert resolve_step_key("resolve_chapter_checkpoint", "wave") == "draft"
    assert resolve_step_key("resolve_chapter_checkpoint", "alignment") == "pre_alignment"
    assert resolve_step_key("resolve_chapter_checkpoint", "continuity_eval") == "pre_alignment"
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "chapter_repair_after_alignment")
        == "pre_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "opening_guard_issues_merged")
        == "pre_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "continuity_repair") == "continuity_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "continuity_eval_after_repair")
        == "continuity_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "alignment_repair_attempt")
        == "alignment_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "alignment_after_self_repair")
        == "alignment_repair"
    )

    # post-quality polish / repair events map to the new visible post_alignment step
    assert resolve_step_key("resolve_chapter_checkpoint", "reading_power_eval") == "post_alignment"
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "reading_power_eval_warning")
        == "post_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "reading_power_prerepair_eval")
        == "post_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "reading_power_repair_recheck")
        == "post_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "reading_power_repair_loop_complete")
        == "post_alignment"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "guard_ticket_repair_complete")
        == "post_guard_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "word_count_archive_gate")
        == "post_guard_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "word_count_restructure_applied")
        == "post_guard_repair"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "element_progress_updated")
        == "memory_updated"
    )
    assert (
        resolve_step_key("resolve_chapter_checkpoint", "memory_concurrent_tasks_started")
        == "memory_updated"
    )
    assert resolve_step_key("resolve_chapter_checkpoint", "causal_repair") == "post_alignment"
    assert resolve_step_key("resolve_chapter_checkpoint", "pronoun_check") == "post_alignment"


def test_resolve_step_key_maps_repair_job_runtime_events_to_visible_order() -> None:
    assert (
        resolve_step_key("repair_continuity", "continuity_eval_after_repair_start")
        == "continuity_eval_after_repair"
    )
    assert resolve_step_key("repair_continuity", "chapter_postprocess") == "repair_continuity"
    assert (
        resolve_step_key("repair_causal", "causal_eval_after_repair_start")
        == "causal_eval_after_repair"
    )
    assert resolve_step_key("repair_causal", "chapter_postprocess") == "repair_causal"
    assert resolve_step_key("repair_issues", "chapter_postprocess") == "chapter_postprocess"


def test_resolve_step_key_maps_hidden_followup_events_to_visible_parent_steps() -> None:
    assert resolve_step_key("polish_chapter", "evaluate") == "polish"
    assert resolve_step_key("polish_chapter", "continuity_eval_after_repair") == "polish"
    assert resolve_step_key("polish_chapter", "causal_eval_after_repair") == "polish"
    assert resolve_step_key("polish_chapter", "chapter_postprocess") == "polish"
    assert resolve_step_key("reextract_relationships", "reextract_error") == "reextract_chapter"


def test_resolve_step_key_maps_init_long_resumed_steps() -> None:
    assert resolve_step_key("init_long", "init_story_bible_resumed") == "init_story_bible"
    assert resolve_step_key("init_long", "profile_style_failed") == "profile_style"
    assert (
        resolve_step_key("init_long", "plan_blueprint_elements_resumed")
        == "plan_blueprint_elements"
    )
    assert resolve_step_key("init_long", "plan_blueprint_resumed") == "plan_blueprint"
    assert resolve_step_key("init_long", "plan_blueprint_validated") == "plan_blueprint_validated"
    assert resolve_step_key("init_long", "plan_blueprint_repaired") == "plan_blueprint_repaired"
    assert resolve_step_key("init_long", "canon_resumed") == "canon_state"
    assert (
        resolve_step_key("init_long", "init_repair_reaudit_stage")
        == "adjudicate_contract_coherence"
    )
    assert resolve_step_key("init_long", "story_kernel_gate_blocked") == "canon_state"
    assert resolve_step_key("init_long", "plan_outline_batch_1_5") == "plan_outline"
    assert resolve_step_key("init_long", "plan_outline_continue_6_10") == "plan_outline"
    assert resolve_step_key("init_long", "plan_outline_repair_11_12") == "plan_outline"
    assert resolve_step_key("init_long", "plan_outline_cache_rejected") == "plan_outline"
    assert resolve_step_key("init_long", "plan_outline_resume_rejected") == "plan_outline"
    assert (
        resolve_step_key("init_long", "plan_outline_reveal_guard_manifest_repaired")
        == "plan_outline"
    )
    assert resolve_step_key("init_long", "plan_chapter_design_matrix") == (
        "plan_chapter_design_matrix"
    )
    assert (
        resolve_step_key("init_long", "plan_chapter_contracts_batch_13_16")
        == "plan_chapter_contracts"
    )
    assert resolve_step_key("init_long", "plan_chapter_contracts_split") == "plan_chapter_contracts"
    assert (
        resolve_step_key("init_long", "plan_chapter_contracts_failed") == "plan_chapter_contracts"
    )
    assert (
        resolve_step_key("init_long", "adjudicate_contract_coherence_batch_13_16")
        == "adjudicate_contract_coherence"
    )
    assert (
        resolve_step_key("init_long", "adjudicate_contract_coherence_split")
        == "adjudicate_contract_coherence"
    )
    assert (
        resolve_step_key("init_long", "adjudicate_contract_coherence_failed")
        == "adjudicate_contract_coherence"
    )
    assert resolve_step_key("init_long", "init_knowledge_boundaries_resumed") == (
        "init_character_system"
    )
    assert resolve_step_key("init_long", "derive_editorial_contract_resumed") == (
        "derive_editorial_contract"
    )
    assert resolve_step_key("init_long", "init_creative_refinement_resumed") == (
        "adjudicate_blueprint_coherence"
    )
    assert resolve_step_key("init_long", "plan_outline_polish_resumed") == "plan_outline"
    assert resolve_step_key("init_long", "started") == ""


def test_display_step_name_formats_edit_rounds() -> None:
    assert display_step_name("edit_") == "自适应修订"
    assert display_step_name("edit_3") == "自适应修订（第 3 轮）"
    assert display_step_name("book_consistency_verify_") == "逐章验证"
    assert display_step_name("book_consistency_two_phase_start") == "智能漏斗摘要筛查开始"
    assert display_step_name("book_consistency_auto_continue") == "继续修复剩余章节"
    assert display_step_name("plan_outline_batch_1_5") == "章节大纲分批生成（1-5章）"
    assert display_step_name("plan_chapter_design_matrix") == "章节设计矩阵"
    assert display_step_name("plan_outline_cache_rejected") == "章节大纲缓存已失效"
    assert (
        display_step_name("plan_outline_reveal_guard_manifest_repaired")
        == "章节大纲缓存已验证并恢复"
    )
    assert display_step_name("plan_outline_resume_rejected") == "章节大纲断点已失效"
    assert display_step_name("plan_chapter_contracts_split") == "章节契约批次拆分重试"
    assert (
        display_step_name("adjudicate_contract_coherence_batch_1_5") == "契约裁判分批检查（1-5章）"
    )
    assert display_step_name("init_story_bible_resumed") == "世界观设定（已恢复）"
    assert display_step_name("volume_audit_failed") == "卷末审计失败（已跳过）"
    assert display_step_name("persist") == "正文与 Canon 落盘"
    assert display_step_name("edit_early_stop") == "编辑提前收束"
    assert display_step_name("memory_summary_generated") == "摘要生成完成"
    assert display_step_name("plan_blueprint_elements") == "叙事要素选择"
    assert display_step_name("wave") == "WAVE 场景编织"
    assert display_step_name("wave_chapter") == "初稿成章"
    assert display_step_name("profile_style_failed") == "风格规范生成失败（已跳过）"
    assert display_step_name("init_knowledge_boundaries_resumed") == "知识边界初始化（已恢复）"
    assert display_step_name("derive_editorial_contract_resumed") == "编辑质量契约（已恢复）"
    assert display_step_name("plan_outline_polish_resumed") == "章节大纲润色（已恢复）"
    assert display_step_name("reading_power_prerepair_eval") == "追读力修复前评估"
    assert display_step_name("reading_power_eval") == "追读力复评"
    assert display_step_name("reading_power_eval_warning") == "追读力复评失败（已跳过）"
    assert display_step_name("reading_power_repair_loop_complete") == "追读力修复流程完成"
    assert display_step_name("causal_repair_gateway_error") == "因果链修复：模型调用失败（已跳过）"
    assert display_step_name("guard_ticket_repair_complete") == "护栏票据修复完成"
    assert display_step_name("continuity_eval_after_text_change") == "文本变更后连贯性复查"
    assert (
        display_step_name("continuity_recheck_internal_error") == "连贯性复查：内部错误（已回滚）"
    )
    assert display_step_name("quality_reports_refresh_after_text_change") == "质量报告已刷新"
    assert display_step_name("word_count_archive_gate") == "归档前字数闸门"
    assert display_step_name("edit_early_exit") == "编辑提前退出"
    assert display_step_name("init_character_bible_json_repaired") == "角色设定 JSON 已修复"
    assert display_step_name("continuity_orchestrator_timeout") == "连贯性修复编排超时"
    assert display_step_name("regenerate_plan") == "重新生成方案"
    assert display_step_name("motif_repair_layer2_progress") == "母题逐章处理"
    assert display_step_name("memory_vector_rebuild_episodic_done") == "重建主记忆向量"
    assert display_step_name("draft_segment_3") == "分段初稿生成（第 3 段）"


def test_literal_pipeline_step_events_have_display_names() -> None:
    event_labels = {step: display_step_name(step) for step in _literal_pipeline_step_events()}
    missing = sorted(step for step, label in event_labels.items() if label == step)
    non_chinese = sorted(
        step
        for step, label in event_labels.items()
        if not any("\u4e00" <= character <= "\u9fff" for character in label)
    )

    assert missing == []
    assert non_chinese == []


def test_cli_step_labels_are_shared_for_all_job_kinds() -> None:
    assert cli_step_labels("run_short")["draft"] == "✅ 初稿完成"
    assert cli_step_labels("init_long")["init_story_bible"] == "✅ 世界观设定生成完成"
    assert cli_step_labels("run_chapter")["wave"] == "✅ 初稿成章完成"
    assert cli_step_labels("run_chapter")["volume_audit_failed"] == "⚠️  卷末审计失败（已跳过）"
    assert cli_step_labels("run_chapter")["reading_power_final_eval"] == "✅ 最终追读力报告已归档"


def test_compute_progress_percent_uses_kind_specific_ranges() -> None:
    assert compute_progress_percent("run_short", "queued", "") == 0
    assert compute_progress_percent("run_short", "running", "edit_2") == 63
    assert compute_progress_percent("run_short", "running", "short_profile_style_failed") == 23
    assert compute_progress_percent("run_short", "running", "short_completeness_check") == 80
    assert compute_progress_percent("init_long", "running", "plan_outline") == 92
    assert compute_progress_percent("init_long", "running", "init_character_system") == 48
    assert compute_progress_percent("init_long", "running", "creative_director_packet") == 70
    assert compute_progress_percent("init_long", "running", "plan_blueprint_validated") == 77
    assert compute_progress_percent("init_long", "running", "plan_blueprint_repaired") == 79
    assert compute_progress_percent("init_long", "running", "derive_editorial_contract") == 80
    assert compute_progress_percent("init_long", "running", "plan_blueprint_fragments") == 81
    assert compute_progress_percent("init_long", "running", "plan_chapter_design_matrix") == 82
    assert compute_progress_percent("init_long", "running", "plan_outline_starting") == 82
    assert compute_progress_percent("init_long", "running", "plan_outline_cache_rejected") == 82
    assert (
        compute_progress_percent(
            "init_long",
            "running",
            "plan_outline_reveal_guard_manifest_repaired",
        )
        == 92
    )
    assert compute_progress_percent("init_long", "running", "plan_outline_resume_rejected") == 82
    assert compute_progress_percent("init_long", "running", "plan_chapter_contracts_split") == 95
    assert (
        compute_progress_percent(
            "init_long",
            "running",
            "adjudicate_contract_coherence_batch_1_12",
        )
        == 96
    )
    assert compute_progress_percent("init_long", "running", "init_claim_contract_coverage") == 97
    assert compute_progress_percent("run_chapter", "running", "edit_2") == 54
    assert compute_progress_percent("run_chapter", "running", "wave") == 54
    assert compute_progress_percent("run_chapter", "running", "element_progress_scheduled") == 18
    assert compute_progress_percent("run_chapter", "running", "persist") == 97
    assert compute_progress_percent("run_chapter", "running", "memory_indexing_started") == 99
    assert compute_progress_percent("run_chapter", "running", "guard_ticket_repair_complete") == 85
    assert compute_progress_percent("run_chapter", "running", "word_count_archive_gate") == 97
    assert (
        compute_progress_percent("run_chapter", "running", "causal_validation_gateway_error") == 86
    )
    assert compute_progress_percent("run_chapter", "running", "causal_repair_gateway_error") == 87
    assert compute_progress_percent("run_chapter", "running", "causal_recheck_gateway_error") == 87
    assert (
        compute_progress_percent("run_chapter", "running", "continuity_recheck_internal_error")
        == 83
    )
    assert compute_progress_percent("run_chapter", "running", "reading_power_prerepair_eval") == 88
    assert (
        compute_progress_percent("run_chapter", "running", "reading_power_repair_loop_complete")
        == 89
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint",
            "running",
            "wave",
        )
        == 35
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint",
            "running",
            "reading_power_eval",
        )
        == 67
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint",
            "running",
            "reading_power_repair_recheck",
        )
        == 67
    )
    assert (
        compute_progress_percent(
            "resolve_chapter_checkpoint_finalize",
            "running",
            "word_count_archive_gate",
        )
        == 30
    )
    assert (
        compute_progress_percent("book_consistency", "running", "book_consistency_issue_pool_ready")
        == 18
    )
    assert compute_progress_percent("book_consistency", "running", "book_consistency_verify_") == 58
    assert (
        compute_progress_percent("book_consistency", "running", "book_consistency_repair_review")
        == 92
    )
    assert (
        compute_progress_percent("book_consistency", "running", "book_consistency_report_written")
        == 96
    )
    assert compute_progress_percent("run_chapter", "succeeded", "persist") == 100


def test_compute_progress_percent_handles_startup_and_resumed_aliases() -> None:
    assert compute_progress_percent("init_long", "running", "started") == 3
    assert compute_progress_percent("init_long", "running", "init_story_bible_resumed") == 20
    assert compute_progress_percent("init_long", "running", "plan_blueprint_elements_resumed") == 20
    assert compute_progress_percent("init_long", "running", "init_character_bible") == 38
    assert compute_progress_percent("init_long", "running", "init_character_system_resumed") == 48
    assert (
        compute_progress_percent("init_long", "running", "init_knowledge_boundaries_resumed") == 48
    )
    assert compute_progress_percent("init_long", "running", "plan_blueprint_resumed") == 76
    assert (
        compute_progress_percent("init_long", "running", "derive_init_coherence_profile_start")
        == 79
    )
    assert compute_progress_percent("init_long", "running", "init_coherence_profile_resumed") == 79
    assert compute_progress_percent("init_long", "running", "plan_chapter_contracts_resumed") == 95
    assert compute_progress_percent("init_long", "running", "init_narrative_contract_resumed") == 94
    assert (
        compute_progress_percent("init_long", "running", "derive_editorial_contract_resumed") == 80
    )
    assert compute_progress_percent("init_long", "running", "plan_outline_polish_resumed") == 92
