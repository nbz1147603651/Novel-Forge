"""Request builders shared by desktop workflow forms and tests."""

from __future__ import annotations

from typing import Any, Literal

from novel_forge.core.domain.story_defaults import DEFAULT_GENRE, DEFAULT_TONE
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    ExportBookRequest,
    InitLongRequest,
    PolishChapterRequest,
    PrepareChapterRequest,
    ReevaluateChapterRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
)


def build_short_request(
    *,
    project_id: str,
    theme: str,
    genre: str,
    tone: str,
    length_target: int,
    max_edit_rounds: int,
    title: str,
    language: str,
    characters_hint: str,
    world_hint: str,
    conflict_hint: str,
    pov_hint: str,
    opening_style: str,
    ending_style: str,
    extra_instructions: str,
    segment_trigger_words: int | None = None,
    writing_mode: str = "auto",
    blueprint_element_preferences: dict[str, Any] | None = None,
    research_enabled: bool = False,
    research_provider: str = "auto",
    research_query_hint: str = "",
) -> RunShortRequest:
    theme_value = theme.strip()
    if not theme_value:
        raise ValueError("请填写「故事主题」。")
    segmented_mode = None
    if segment_trigger_words is not None:
        trigger_value = max(1500, int(segment_trigger_words))
        segmented_mode = "on" if length_target >= trigger_value else "off"
    return RunShortRequest(
        project_id=project_id.strip(),
        theme=theme_value,
        genre=genre or DEFAULT_GENRE,
        tone=tone or DEFAULT_TONE,
        length_target=length_target,
        segmented_mode=segmented_mode,
        writing_mode=(
            "scene_level"
            if writing_mode == "scene_level"
            else "whole_chapter"
            if writing_mode == "whole_chapter"
            else "auto"
        ),
        max_edit_rounds=max_edit_rounds,
        title=title.strip(),
        language=language or "zh",
        characters_hint=characters_hint.strip(),
        world_hint=world_hint.strip(),
        conflict_hint=conflict_hint.strip(),
        pov_hint=pov_hint.strip(),
        opening_style=opening_style.strip(),
        ending_style=ending_style.strip(),
        extra_instructions=extra_instructions.strip(),
        blueprint_element_preferences=blueprint_element_preferences or {},
        research_enabled=bool(research_enabled),
        research_provider=(research_provider or "auto").strip() or "auto",
        research_query_hint=research_query_hint.strip(),
    )


def build_init_long_request(
    *,
    project_id: str,
    premise: str,
    genre: str,
    tone: str,
    total_chapters: int,
    words_per_chapter: int,
    volume_mode: str,
    chapters_per_volume: int,
    title: str,
    language: str,
    characters_hint: str,
    world_hint: str,
    conflict_hint: str,
    pov_hint: str,
    opening_style: str,
    ending_style: str,
    extra_instructions: str,
    polish_hint: str = "",
    research_enabled: bool = False,
    research_provider: str = "auto",
    research_query_hint: str = "",
    regenerate_outline: bool = False,
    blueprint_element_preferences: dict[str, Any] | None = None,
    copilot_gates: tuple[str, ...] = (),
    creative_exploration: Literal["adaptive", "single"] = "adaptive",
    planning_commitment: Literal["progressive", "full"] = "full",
) -> InitLongRequest:
    premise_value = premise.strip()
    if not premise_value:
        raise ValueError("请填写「故事前提」。")
    normalized_volume_mode = str(volume_mode or "auto").strip().lower()
    if normalized_volume_mode not in {"auto", "on", "off"}:
        normalized_volume_mode = "auto"
    # Only forced volume mode consumes a manually requested volume size.
    # Keeping a stale number for auto/off is confusing in persisted request
    # metadata and can make a restored form imply a preference that is ignored.
    normalized_chapters_per_volume = max(0, int(chapters_per_volume))
    if normalized_volume_mode != "on":
        normalized_chapters_per_volume = 0
    return InitLongRequest(
        project_id=project_id.strip(),
        premise=premise_value,
        genre=genre or DEFAULT_GENRE,
        tone=tone or DEFAULT_TONE,
        total_chapters=total_chapters,
        words_per_chapter=words_per_chapter,
        volume_mode=normalized_volume_mode,
        chapters_per_volume=normalized_chapters_per_volume,
        title=title.strip(),
        language=language or "zh",
        characters_hint=characters_hint.strip(),
        world_hint=world_hint.strip(),
        conflict_hint=conflict_hint.strip(),
        pov_hint=pov_hint.strip(),
        opening_style=opening_style.strip(),
        ending_style=ending_style.strip(),
        extra_instructions=extra_instructions.strip(),
        polish_hint=polish_hint.strip(),
        research_enabled=bool(research_enabled),
        research_provider=(research_provider or "auto").strip() or "auto",
        research_query_hint=research_query_hint.strip(),
        regenerate_outline=regenerate_outline,
        blueprint_element_preferences=blueprint_element_preferences or {},
        copilot_gates=tuple(str(gate).strip() for gate in copilot_gates if str(gate).strip()),
        creative_exploration=creative_exploration,
        planning_commitment=planning_commitment,
    )


def build_run_chapter_request(
    *,
    project_id: str,
    chapter_number: int,
    force: bool,
    writing_mode: str = "whole_chapter",
    repair_control_mode: str | None = None,
) -> RunChapterRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return RunChapterRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        force=force,
        writing_mode="scene_level" if writing_mode == "scene_level" else "whole_chapter",
        repair_control_mode=repair_control_mode,
    )


def build_prepare_chapter_request(
    *,
    project_id: str,
    chapter_number: int,
    force: bool,
    notes: str,
    rewrite_strategy: str = "auto",
    writing_mode: str = "whole_chapter",
    repair_control_mode: str | None = None,
) -> PrepareChapterRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return PrepareChapterRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        force=force,
        notes=notes.strip(),
        rewrite_strategy=rewrite_strategy,
        writing_mode="scene_level" if writing_mode == "scene_level" else "whole_chapter",
        repair_control_mode=repair_control_mode,
    )


def build_repair_continuity_request(
    *,
    project_id: str,
    chapter_number: int,
    issue_indices: list[int],
    issue_signatures: list[str] | None = None,
    repair_control_mode: str | None = None,
) -> RepairContinuityRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return RepairContinuityRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        issue_indices=issue_indices,
        issue_signatures=issue_signatures or [],
        repair_control_mode=repair_control_mode,
    )


def build_repair_causal_request(
    *,
    project_id: str,
    chapter_number: int,
    issue_indices: list[int],
    issue_signatures: list[str] | None = None,
    allow_exhausted_retry: bool = False,
    repair_control_mode: str | None = None,
) -> RepairCausalRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return RepairCausalRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        issue_indices=issue_indices,
        issue_signatures=issue_signatures or [],
        allow_exhausted_retry=allow_exhausted_retry,
        repair_control_mode=repair_control_mode,
    )


def build_repair_issues_request(
    *,
    project_id: str,
    chapter_number: int,
    continuity_issue_indices: list[int],
    causal_issue_indices: list[int],
    continuity_issue_signatures: list[str] | None = None,
    causal_issue_signatures: list[str] | None = None,
    allow_exhausted_retry: bool = False,
    repair_control_mode: str | None = None,
) -> RepairIssuesRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return RepairIssuesRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        continuity_issue_indices=continuity_issue_indices,
        causal_issue_indices=causal_issue_indices,
        continuity_issue_signatures=continuity_issue_signatures or [],
        causal_issue_signatures=causal_issue_signatures or [],
        allow_exhausted_retry=allow_exhausted_retry,
        repair_control_mode=repair_control_mode,
    )


def build_reevaluate_chapter_request(
    *,
    project_id: str,
    chapter_number: int,
) -> ReevaluateChapterRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return ReevaluateChapterRequest(
        project_id=project_value,
        chapter_number=chapter_number,
    )


def build_repair_motif_history_request(
    *,
    project_id: str,
    chapter_number: int,
    force_re_extract: bool = False,
    start_chapter: int = 1,
    end_chapter: int | None = None,
) -> RepairMotifHistoryRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return RepairMotifHistoryRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        force_re_extract=force_re_extract,
        start_chapter=start_chapter,
        end_chapter=end_chapter,
    )


def build_resolve_chapter_checkpoint_request(
    *,
    project_id: str,
    chapter_number: int,
    checkpoint_id: str,
    option_id: str,
    notes: str,
    force: bool = False,
    repair_control_mode: str | None = None,
) -> ResolveChapterCheckpointRequest:
    project_value = project_id.strip()
    checkpoint_value = checkpoint_id.strip()
    option_value = option_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    if not checkpoint_value:
        raise ValueError("当前没有可继续的章节决策。")
    if not option_value:
        raise ValueError("请选择一个章节决策选项。")
    return ResolveChapterCheckpointRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        checkpoint_id=checkpoint_value,
        option_id=option_value,
        notes=notes.strip(),
        force=force,
        repair_control_mode=repair_control_mode,
    )


def build_polish_chapter_request(
    *,
    project_id: str,
    chapter_number: int,
    notes: str = "",
) -> PolishChapterRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return PolishChapterRequest(
        project_id=project_value,
        chapter_number=chapter_number,
        notes=notes.strip(),
    )


def build_book_consistency_request(
    *,
    project_id: str,
    chapter_range: list[int] | None = None,
    analysis_mode: str = "auto",
    prompt_hint: str = "",
    location_strictness: str = "balanced",
    max_tokens: int | None = None,
    temperature: float | None = None,
    repair_mode: str = "off",
    audit_max_chapters_per_batch: int = 12,
    audit_max_issues_per_chunk: int = 12,
    audit_issue_pool_max_items: int = 160,
    chapter_max_chars: int | None = None,
    repair_min_severity: str = "warning",
    repair_max_chapters: int = 12,
    allow_exhausted_retry: bool = False,
    use_issue_panel_pool: bool = True,
    repair_concurrency: int = 1,
    generate_repair_report: bool = True,
    panel_first_expansion: bool = True,
    verify_before_repair: bool = True,
    post_repair_targeted_audit: bool = False,
    repair_guard_enabled: bool = True,
    repair_guard_max_delta_ratio: float = 0.12,
    repair_guard_max_added_chars: int = 600,
    auto_continue: bool = False,
    continue_from_audit: bool = False,
    continue_audit_from_checkpoint: bool = False,
    reset_audit_checkpoint: bool = False,
    rollback_on_failure: bool = True,
    two_phase_enabled: bool = True,
    two_phase_threshold: float = 0.7,
    two_phase_max_target_chapters: int = 24,
    parallel_chunks: bool = True,
    parallel_dimensions: bool = True,
) -> BookConsistencyRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return BookConsistencyRequest(
        project_id=project_value,
        chapter_range=chapter_range or [],
        analysis_mode=str(analysis_mode or "auto").strip() or "auto",
        prompt_hint=prompt_hint.strip(),
        location_strictness=str(location_strictness or "balanced").strip() or "balanced",
        max_tokens=max_tokens,
        temperature=temperature,
        repair_mode="off",
        audit_max_chapters_per_batch=audit_max_chapters_per_batch,
        audit_max_issues_per_chunk=audit_max_issues_per_chunk,
        audit_issue_pool_max_items=audit_issue_pool_max_items,
        chapter_max_chars=chapter_max_chars,
        repair_min_severity=str(repair_min_severity or "warning").strip() or "warning",
        repair_max_chapters=repair_max_chapters,
        allow_exhausted_retry=allow_exhausted_retry,
        use_issue_panel_pool=use_issue_panel_pool,
        repair_concurrency=repair_concurrency,
        generate_repair_report=generate_repair_report,
        panel_first_expansion=panel_first_expansion,
        verify_before_repair=verify_before_repair,
        post_repair_targeted_audit=post_repair_targeted_audit,
        repair_guard_enabled=repair_guard_enabled,
        repair_guard_max_delta_ratio=repair_guard_max_delta_ratio,
        repair_guard_max_added_chars=repair_guard_max_added_chars,
        auto_continue=False,
        continue_from_audit=False,
        continue_audit_from_checkpoint=continue_audit_from_checkpoint,
        reset_audit_checkpoint=reset_audit_checkpoint,
        rollback_on_failure=rollback_on_failure,
        two_phase_enabled=two_phase_enabled,
        two_phase_threshold=two_phase_threshold,
        two_phase_max_target_chapters=two_phase_max_target_chapters,
        parallel_chunks=parallel_chunks,
        parallel_dimensions=parallel_dimensions,
    )


def build_export_book_request(
    *,
    project_id: str,
    format: str = "markdown",
    chapter_range: list[int] | None = None,
    output_dir: str = "",
    book_title: str = "",
) -> ExportBookRequest:
    project_value = project_id.strip()
    if not project_value:
        raise ValueError("请选择或输入长篇项目 ID。")
    return ExportBookRequest(
        project_id=project_value,
        format=format,
        chapter_range=chapter_range or [],
        output_dir=output_dir,
        book_title=book_title,
    )


def summarize_job_result(result: dict[str, Any]) -> str:
    """Build a short human-readable summary for UI hints/tests."""
    parts: list[str] = []
    word_count = result.get("word_count")
    overall_score = result.get("overall_score")
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    causal_score = result.get("causal_score", metadata.get("causal_score"))
    continuity_score = result.get("continuity_score", metadata.get("continuity_score"))
    consistency_score = result.get("consistency_score", metadata.get("consistency_score"))
    issue_count = result.get("issue_count", metadata.get("issue_count"))
    warnings = result.get("warnings", metadata.get("warnings"))
    tokens_used = result.get("tokens_used")
    if not isinstance(tokens_used, int):
        alias_tokens = result.get("total_tokens")
        tokens_used = alias_tokens if isinstance(alias_tokens, int) else None
    # Repair-specific fields
    applied = result.get("applied")
    patches_applied = result.get("patches_applied")
    patches_attempted = result.get("patches_attempted")
    motifs_touched = result.get("motifs_touched")
    occurrences_seen = result.get("occurrences_seen")
    extraction_cache_merged = result.get("extraction_cache_merged")
    created_motifs = result.get("created_motifs")
    repaired_ok = result.get("ok")
    auto_repair = result.get("auto_repair")
    tts_status = str(result.get("status") or "").strip().lower()
    completed_segments = result.get("completed_segments")
    total_segments = result.get("total_segments")
    if isinstance(word_count, int) and word_count > 0:
        parts.append(f"{word_count:,} 字")
    if isinstance(overall_score, (int, float)):
        parts.append(f"质量评分 {overall_score:.1f}")
    if isinstance(continuity_score, (int, float)):
        parts.append(f"连贯 {float(continuity_score):.1f}")
    if isinstance(causal_score, (int, float)):
        parts.append(f"因果 {float(causal_score):.1f}")
    if isinstance(consistency_score, (int, float)):
        parts.append(f"一致性 {float(consistency_score):.1f}")
    if isinstance(issue_count, int):
        parts.append(f"问题 {issue_count}")
    if isinstance(applied, bool):
        parts.append("已修复" if applied else "未修改")
    if (
        isinstance(patches_applied, int)
        and isinstance(patches_attempted, int)
        and patches_attempted > 0
    ):
        parts.append(f"补丁 {patches_applied}/{patches_attempted}")
    if isinstance(repaired_ok, bool):
        parts.append("修补完成" if repaired_ok else "修补未完成")
    if isinstance(motifs_touched, int) and motifs_touched > 0:
        parts.append(f"回填母题 {motifs_touched}")
    if isinstance(occurrences_seen, int) and occurrences_seen > 0:
        parts.append(f"统计片段 {occurrences_seen}")
    if isinstance(created_motifs, int) and created_motifs > 0:
        parts.append(f"新增母题 {created_motifs}")
    if isinstance(extraction_cache_merged, int) and extraction_cache_merged > 0:
        parts.append(f"补并缓存章 {extraction_cache_merged}")
    if isinstance(auto_repair, dict):
        targeted = int(auto_repair.get("targeted_chapters", 0) or 0)
        applied_chapters = int(auto_repair.get("applied_chapters", 0) or 0)
        if targeted > 0:
            details = auto_repair.get("details", [])
            no_change = int(auto_repair.get("no_change_unique_chapter_count", 0) or 0)
            if no_change <= 0 and isinstance(details, list):
                no_change = len(
                    {
                        int(item.get("chapter_number", 0))
                        for item in details
                        if isinstance(item, dict) and item.get("status") == "no_change"
                    }
                )
            repair_text = f"修复写入 {applied_chapters}/{targeted} 章"
            if no_change > 0:
                repair_text += f"，无改动 {no_change} 章"
            parts.append(repair_text)
        verification_summary = auto_repair.get("verification_summary")
        if isinstance(verification_summary, dict):
            by_status = verification_summary.get("by_status")
            if isinstance(by_status, dict):
                unresolved = int(by_status.get("unresolved", 0) or 0)
                regressed = int(by_status.get("regressed", 0) or 0)
                if unresolved > 0 or regressed > 0:
                    parts.append(f"未解决 {unresolved} / 回归 {regressed}")
    if isinstance(tokens_used, int) and tokens_used > 0:
        parts.append(f"Token 消耗 {tokens_used}")
    if (
        isinstance(completed_segments, int)
        and isinstance(total_segments, int)
        and total_segments > 0
    ):
        parts.append(f"音频片段 {completed_segments}/{total_segments}")
    if tts_status == "partial":
        parts.append("已保留进度，可续跑")
    elif tts_status == "skipped":
        parts.append("自动配音已跳过")
    if isinstance(warnings, list) and warnings:
        parts.append(f"{len(warnings)} 条提醒")
    return "  ·  ".join(parts)
