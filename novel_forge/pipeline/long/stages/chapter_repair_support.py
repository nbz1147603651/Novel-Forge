"""Shared mechanics for continuity, causal, and reading-power repair loops.

Domain scoring, thresholds, stop conditions, and rollback decisions remain in
their respective repair runners. This module owns only the mechanics those
runners already share.
"""

from __future__ import annotations

import asyncio
from typing import Any

from novel_forge.core.domain.world_context import (
    format_address_rules_for_prompt,
    render_world_context_rules,
)
from novel_forge.core.review.audit_taxonomy import classify_review_issue
from novel_forge.core.utils.audit_issue import stable_issue_id
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.type_coerce import stringify_text_value
from novel_forge.memory.critic import CriticAgent
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    _build_dynamic_continuity_inputs,
    _should_skip_alignment_recheck,
)
from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep
from novel_forge.pipeline.steps.check_chapter_step import ChapterRepairInput, ChapterRepairStep
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
)
from novel_forge.story_kernel.schemas import StoryKernel

_logger = get_logger("pipeline.chapter_repair_support")


async def _build_memory_guidance(
    *,
    episodic_memory: Any,
    current_issues: list[Any],
    current_chapter: int,
    max_entries: int = 3,
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    """Build memory-guided repair guidance by querying historical critique data.

    Actively searches the EpisodicMemory for similar past issues and extracts
    actionable repair guidance including:
    - Matched similar issues from history
    - Recommended strategies (from successful repairs)
    - Strategies to avoid (from failed repairs)
    - Historical success rate
    - Cross-chapter warnings

    Args:
        episodic_memory: The EpisodicMemory instance to query
        current_issues: List of current continuity issues to search for
        current_chapter: Current chapter number
        max_entries: Maximum number of matched issues to include
        timeout_s: Timeout in seconds for the entire search operation

    Returns:
        Structured memory guidance dict or None if no matches found
    """
    import asyncio
    from collections import Counter

    if not episodic_memory or not current_issues:
        return None

    async def _search_with_timeout() -> dict[str, Any] | None:
        """Search for similar critiques with timeout protection."""
        all_matches: list[dict[str, Any]] = []

        # Search for each current issue
        for issue in current_issues:
            issue_type = getattr(issue, "issue_type", "unknown")
            summary = getattr(issue, "summary", "")

            if not summary:
                continue

            try:
                matches = await episodic_memory.search_similar_critiques(
                    issue_type=issue_type,
                    summary=summary,
                    current_chapter=current_chapter,
                    top_k=5,
                    min_relevance=0.55,
                )
                all_matches.extend(matches)
            except Exception as e:
                _logger.warning(
                    "Failed to search similar critiques | issue_type=%s | error=%s",
                    issue_type,
                    str(e),
                )

        if not all_matches:
            return None

        # Deduplicate matches based on summary similarity
        seen_summaries: list[str] = []
        unique_matches: list[dict[str, Any]] = []

        def _char_bigrams(text: str) -> set[str]:
            """Extract character-level bigrams for similarity comparison (works for CJK)."""
            return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) >= 2 else {text}

        def _bigram_similarity(a: str, b: str) -> float:
            """Jaccard similarity using character bigrams."""
            if not a or not b:
                return 0.0
            bigrams_a = _char_bigrams(a)
            bigrams_b = _char_bigrams(b)
            if not bigrams_a or not bigrams_b:
                return 0.0
            intersection = bigrams_a & bigrams_b
            union = bigrams_a | bigrams_b
            return len(intersection) / len(union) if union else 0.0

        for match in all_matches:
            summary = match.get("summary", "")
            is_duplicate = False

            for seen in seen_summaries:
                # Use character-level bigram similarity for CJK text support
                if len(summary) > 0 and len(seen) > 0:
                    similarity = _bigram_similarity(summary, seen)
                    if similarity > 0.75:  # High similarity threshold for dedup
                        is_duplicate = True
                        break

            if not is_duplicate:
                seen_summaries.append(summary)
                unique_matches.append(match)

        # Sort by relevance score
        unique_matches.sort(key=lambda m: m.get("relevance_score", 0), reverse=True)
        unique_matches = unique_matches[:max_entries]

        # Extract recommended strategies from successful repairs
        recommended_strategies: list[str] = []
        avoid_strategies: list[str] = []
        success_count = 0
        total_attempts = 0
        chapter_occurrences: Counter[int] = Counter()

        for match in unique_matches:
            chapter_occurrences[match.get("chapter_number", 0)] += 1
            repair_attempts = match.get("repair_attempts", [])

            for attempt in repair_attempts:
                total_attempts += 1
                result = attempt.get("result", "")
                strategy = attempt.get("strategy", "")
                new_issues = attempt.get("new_issues_introduced", [])

                if result == "success":
                    success_count += 1
                    if strategy and strategy not in recommended_strategies:
                        recommended_strategies.append(strategy)
                elif result == "regression":
                    # Add strategies that caused regression to avoid list
                    if strategy and strategy not in avoid_strategies:
                        avoid_strategies.append(strategy)
                    # Also add new issues introduced as warnings
                    for new_issue in new_issues:
                        issue_warning = f"修复时容易引入{new_issue}"
                        if issue_warning not in avoid_strategies:
                            avoid_strategies.append(issue_warning)

            # Extract lessons learned
            lesson = match.get("lesson_learned")
            if lesson and lesson not in recommended_strategies:
                recommended_strategies.append(lesson)

            # Extract failure patterns
            pattern = match.get("failure_pattern")
            if pattern and pattern not in avoid_strategies:
                avoid_strategies.append(pattern)

            # Include suggested_fix from original critique as reference
            suggested_fix = match.get("suggested_fix")
            if suggested_fix and suggested_fix not in recommended_strategies:
                recommended_strategies.append(f"原始建议: {suggested_fix[:60]}")

        # Calculate success rate
        success_rate = success_count / total_attempts if total_attempts > 0 else 0.0

        # Generate cross-chapter warning
        warning: str | None = None
        if len(chapter_occurrences) >= 2:
            chapters = sorted(chapter_occurrences.keys())
            warning = f"类似问题在第{', '.join(str(ch) for ch in chapters)}章都出现过，需要格外小心"

        # Build matched issues summary for display
        matched_issues = []
        for match in unique_matches:
            matched_issues.append(
                {
                    "chapter": match.get("chapter_number", 0),
                    "issue_type": match.get("issue_type", ""),
                    "summary": match.get("summary", "")[:80],  # Truncate for display
                    "relevance": match.get("relevance_score", 0),
                    "lesson_learned": match.get("lesson_learned", ""),
                }
            )

        return {
            "matched_issues": matched_issues,
            "recommended_strategies": recommended_strategies[:3],  # Limit to 3
            "avoid_strategies": avoid_strategies[:2],  # Limit to 2
            "success_rate": round(success_rate, 2),
            "total_attempts": total_attempts,
            "warning": warning,
        }

    try:
        # Execute search with timeout
        return await asyncio.wait_for(_search_with_timeout(), timeout=timeout_s)
    except asyncio.TimeoutError:
        _logger.warning(
            "Memory guidance search timed out after %.1f seconds, skipping",
            timeout_s,
        )
        return None
    except Exception as e:
        _logger.warning(
            "Memory guidance search failed | error=%s",
            str(e),
        )
        return None

def _get_runner_episodic_memory(runner: Any) -> Any | None:
    """Return the runner's episodic memory instance, if available."""
    has_memory_context = getattr(runner, "has_memory_context", None)
    if callable(has_memory_context) and not has_memory_context():
        return None

    memory_ctx = getattr(runner, "memory_context", None)
    if memory_ctx is None:
        return None

    episodic_memory = getattr(memory_ctx, "episodic_memory", None)
    if episodic_memory is None:
        episodic_memory = getattr(memory_ctx, "_episodic_memory", None)
    return episodic_memory

def _convert_critique_to_continuity(
    critique: Any | None,
    chapter_number: int,
) -> Any:
    """Convert CritiqueReport to ContinuityReport format for compatibility.

    Only continuity-relevant issues (issue_type containing 'continuity' or
    'causal') are included in the ContinuityReport and used to compute
    ``continuity_score``.  Other issue categories (world_building, character,
    alignment, thematic) are valuable for UI display but should NOT inflate
    the continuity repair trigger — continuity repair cannot fix them and
    attempting to do so causes massive rewrites that destroy alignment.
    """
    from novel_forge.core.schemas.continuity import ContinuityIssue, ContinuityReport

    if critique is None:
        return ContinuityReport(
            continuity_score=7.0,
            summary="CriticAgent 不可用，使用保守评估（默认 7.0 触发轻量修复检查）。",
            issues=[],
        )

    # Separate continuity-relevant issues from other categories through the
    # shared taxonomy.  This is intentionally stricter than the historical
    # CriticAgent adapter: chapter-local naming, prompt-leak, expression, POV,
    # and forbidden-element findings must not enter continuity repair simply
    # because the critic surfaced them in a "character" or "world" branch.
    _ISSUE_TYPE_MAP = {
        "continuity_error": "continuity_gap",
    }
    _MIN_CONFIDENCE = 0.4  # filter out low-confidence noise from CriticAgent

    issues: list[Any] = []
    other_count = 0

    for ci in critique.issues:
        issue_type_str = str(ci.issue_type)
        summary = stringify_text_value(getattr(ci, "summary", ""))
        evidence = stringify_text_value(getattr(ci, "evidence", ""))
        suggested_fix = stringify_text_value(getattr(ci, "suggested_fix", ""))
        classification = classify_review_issue(
            issue_type=issue_type_str,
            source_module="critic_agent",
            summary=summary,
            evidence=evidence,
        )
        if classification.is_continuity:
            # Filter low-confidence issues: CriticAgent LLM calls can produce
            # speculative findings with confidence < 0.4 that inflate the penalty
            # without actionable signal for continuity repair.
            confidence = getattr(ci, "confidence", 1.0)
            if confidence < _MIN_CONFIDENCE:
                other_count += 1
                continue
            mapped_type = _ISSUE_TYPE_MAP.get(issue_type_str, issue_type_str)
            cont_issue = ContinuityIssue(
                issue_id=stable_issue_id(
                    "continuity",
                    chapter_number=chapter_number,
                    issue_type=mapped_type,
                    summary=summary,
                    evidence=evidence,
                    repair_surface="chapter_text",
                    extra={
                        "affected_chapters": list(getattr(ci, "affected_chapters", []) or []),
                    },
                ),
                issue_type=mapped_type,
                severity=ci.severity,
                summary=summary,
                evidence=evidence,
                fix_actions=[suggested_fix] if suggested_fix else [],
            )
            issues.append(cont_issue)
        else:
            other_count += 1

    _SEVERITY_PENALTIES = {"critical": 3.0, "high": 1.5, "medium": 0.5, "low": 0.2}
    cont_score = 10.0
    for ci in issues:
        cont_score -= _SEVERITY_PENALTIES.get(ci.severity, 0.5)
    cont_score = max(0.0, min(10.0, cont_score))

    summary_parts = [
        f"基于 {len(issues)} 个连续性问题的评估（另有 {other_count} 个非连续性问题不纳入分数）。"
    ]
    if critique.warnings:
        summary_parts.append(" ".join(critique.warnings))

    return ContinuityReport(
        continuity_score=cont_score,
        summary="\n\n".join(summary_parts),
        issues=issues,
    )

def _build_post_repair_check_plan(
    *,
    previous_text: str,
    revised_text: str,
) -> tuple[str, list[dict[str, Any]], float]:
    """Choose the safest post-repair chapter-check mode.

    Small, local continuity repairs are rechecked in delta mode to reduce token
    cost. Broader rewrites automatically fall back to a full chapter scan.
    """
    from novel_forge.pipeline.steps.check_chapter_step import ChapterRepairStep

    return ChapterRepairStep.build_delta_recheck_payload(previous_text, revised_text)

def _clean_recheck_strings(values: Any, *, limit: int = 12, item_limit: int = 240) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, (list, tuple, set)):
        return cleaned
    for value in values:
        text = str(value or "").strip()
        if len(text) > item_limit:
            text = text[: item_limit - 1].rstrip("，。、；： \n") + "…"
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned

def _continuity_issue_recheck_payload(issues: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    """Build compact prior-issue payloads for targeted post-repair rechecks."""
    if not isinstance(issues, (list, tuple, set)):
        return []

    allowed = {
        "issue_id",
        "issue_type",
        "severity",
        "repair_surface",
        "summary",
        "evidence",
        "evidence_quote",
        "location",
        "paragraph_start",
        "paragraph_end",
        "missing_anchors",
        "postconditions",
        "repair_focus",
    }
    payloads: list[dict[str, Any]] = []
    for issue in list(issues)[:limit]:
        if issue is None:
            continue
        if isinstance(issue, dict):
            raw = dict(issue)
        elif hasattr(issue, "model_dump"):
            raw = issue.model_dump(mode="json")
        else:
            raw = {
                "issue_id": getattr(issue, "issue_id", ""),
                "issue_type": getattr(issue, "issue_type", ""),
                "severity": getattr(issue, "severity", "medium"),
                "repair_surface": getattr(issue, "repair_surface", ""),
                "summary": getattr(issue, "summary", ""),
                "evidence": getattr(issue, "evidence", ""),
                "evidence_quote": getattr(issue, "evidence_quote", ""),
                "location": getattr(issue, "location", ""),
                "paragraph_start": getattr(issue, "paragraph_start", 0),
                "paragraph_end": getattr(issue, "paragraph_end", 0),
                "missing_anchors": getattr(issue, "missing_anchors", []),
                "postconditions": getattr(issue, "postconditions", []),
            }

        if "repair_focus" not in raw:
            directive = raw.get("repair_directive")
            if isinstance(directive, dict):
                focus = directive.get("repair_strategy") or directive.get("recommended_rewrite")
                if focus:
                    raw["repair_focus"] = str(focus)[:240]

        clean = {
            key: raw.get(key)
            for key in allowed
            if key in raw and raw.get(key) not in (None, "", [], {})
        }
        if clean.get("issue_type") or clean.get("summary"):
            payloads.append(clean)
    return payloads

async def run_post_repair_checks(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    pre_repair_text: str,
    current_text: str,
    chapter_number: int,
    alignment_report: Any,
    continuity_report: Any,
    chapter_repair_report: Any,
    trace: Any,
    *,
    use_same_detector: bool = True,
    skip_chapter_repair: bool = False,
    skip_critic_agent: bool = False,
    memory_hints: dict[str, Any] | None = None,
    prior_issues: list[Any] | tuple[Any, ...] | None = None,
    must_resolve_summaries: list[str] | tuple[str, ...] | None = None,
    repaired_issue_types: list[str] | tuple[str, ...] | None = None,
    patch_only_repair: bool = False,
) -> tuple[Any, Any, Any]:
    """Run post-repair quality checks after continuity repair.

    When *use_same_detector* is True (default), the continuity recheck
    uses the same detector source as the initial check:
    - If the runner has a CriticAgent available, use CriticAgent → convert
    - Otherwise fall back to ContinuityEvalStep
    This avoids "detector mismatch" where different tools flag different
    issue types, creating false regressions.

    When *skip_chapter_repair* is True, ChapterRepairStep is not run even if
    ``long_check_chapter_enabled`` is set.  This is used for moderate changes
    that still need alignment+continuity verification but don't warrant a full
    chapter-quality recheck.

    All three checks — AlignmentStep, ContinuityRecheck, ChapterRepairStep —
    are dispatched in parallel (where applicable) since they only *read*
    current_text and write to distinct storage paths.

    Returns:
        Tuple of (alignment_report, continuity_report, chapter_repair_report).
    """
    _recheck_prior_issues = _continuity_issue_recheck_payload(prior_issues)
    _recheck_must_resolve = _clean_recheck_strings(must_resolve_summaries)
    _recheck_repaired_types = _clean_recheck_strings(repaired_issue_types, item_limit=80)
    _recheck_memory_hints = dict(memory_hints or {})
    if _recheck_prior_issues or _recheck_must_resolve or _recheck_repaired_types:
        _recheck_memory_hints["post_repair_recheck"] = {
            "prior_issues": _recheck_prior_issues,
            "must_resolve_summaries": _recheck_must_resolve,
            "repaired_issue_types": _recheck_repaired_types,
            "patch_only_repair": bool(patch_only_repair),
        }
        runner._on_step(
            "continuity_recheck_context",
            {
                "chapter": chapter_number,
                "prior_issues": len(_recheck_prior_issues),
                "must_resolve": len(_recheck_must_resolve),
                "repaired_issue_types": _recheck_repaired_types,
                "patch_only": bool(patch_only_repair),
            },
        )

    kernel_composer = await load_story_kernel_composer(runner, bundle)
    alignment_kernel_context = (
        kernel_composer.compose_alignment_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    continuity_kernel_context = (
        kernel_composer.compose_continuity_eval_input(chapter_number, current_text)
        if kernel_composer is not None
        else {}
    )
    chapter_repair_kernel_context = (
        kernel_composer.compose_check_chapter_input(chapter_number)
        if kernel_composer is not None
        else {}
    )

    # ── Sync pre-checks: decide which tasks to run BEFORE starting the gather ──
    skip_alignment, skip_reason = _should_skip_alignment_recheck(
        pre_repair_text, current_text, alignment_report
    )
    if skip_alignment:
        runner._on_step(
            "alignment_cached",
            {
                "chapter": chapter_number,
                "reason": skip_reason,
                "cached_score": round(float(getattr(alignment_report, "alignment_score", 0.0)), 2),
            },
        )

    run_chapter_repair = False
    _repair_check_mode: str = "full"
    _repair_changed_sections: list[Any] = []
    _repair_change_ratio: float = 1.0
    if runner._settings.long_check_chapter_enabled and not skip_chapter_repair:
        _repair_check_mode, _repair_changed_sections, _repair_change_ratio = (
            _build_post_repair_check_plan(
                previous_text=pre_repair_text,
                revised_text=current_text,
            )
        )
        initial_clean = (
            chapter_repair_report is not None
            and not chapter_repair_report.prompt_leaks
            and getattr(chapter_repair_report, "risk_level", "medium") == "low"
        )
        if _repair_change_ratio > 0.98 and initial_clean:
            _logger.info(
                "chapter_repair_recheck skipped | change_ratio=%.4f | initial_risk=low",
                _repair_change_ratio,
            )
            runner._on_step(
                "chapter_repair_recheck_skipped",
                {
                    "chapter": chapter_number,
                    "change_ratio": _repair_change_ratio,
                    "reason": "near_noop_and_initially_clean",
                },
            )
        else:
            run_chapter_repair = True
            runner._on_step(
                "chapter_repair_recheck_mode",
                {
                    "chapter": chapter_number,
                    "mode": _repair_check_mode,
                    "change_ratio": _repair_change_ratio,
                    "changed_sections": len(_repair_changed_sections),
                },
            )
    elif skip_chapter_repair and runner._settings.long_check_chapter_enabled:
        runner._on_step(
            "chapter_repair_recheck_skipped",
            {
                "chapter": chapter_number,
                "reason": "skip_chapter_repair_flag_set",
            },
        )

    # ── Async task helpers ────────────────────────────────────────────────────

    async def _task_alignment() -> Any:
        alignment_step = AlignmentStep(
            runner._router, runner._builder, settings=runner._settings, trace=trace
        )
        return await alignment_step.run(
            AlignmentInput(
                chapter_outline=bundle.chapter_outline,
                chapter_plan=plan,
                chapter_text=current_text,
                kernel_context=alignment_kernel_context,
            )
        )

    async def _task_continuity_recheck() -> Any:
        """Match the initial detector type to avoid false regressions."""
        if use_same_detector and not skip_critic_agent and runner.has_memory_context():
            try:
                memory_ctx = runner.memory_context
                critic_agent = getattr(memory_ctx, "critic_agent", None)
                if critic_agent is None and getattr(
                    runner._settings, "memory_critic_agent_enabled", True
                ):
                    critic_agent = CriticAgent(
                        router=runner._router,
                        builder=runner._builder,
                        episodic_memory=getattr(memory_ctx, "_episodic_memory", None),
                        motif_tracker=getattr(memory_ctx, "motif_tracker", None),
                        check_timeout_s=float(
                            getattr(runner._settings, "memory_critic_agent_timeout_s", 45.0) or 45.0
                        ),
                        timeout_extend_attempts=int(
                            getattr(
                                runner._settings, "memory_critic_agent_timeout_extend_attempts", 1
                            )
                            or 0
                        ),
                        timeout_extend_multiplier=float(
                            getattr(
                                runner._settings,
                                "memory_critic_agent_timeout_extend_multiplier",
                                1.5,
                            )
                            or 1.0
                        ),
                        cache_enabled=False,  # Don't cache recheck results
                        motif_repetition_lookback_chapters=int(
                            getattr(runner._settings, "motif_repetition_lookback_chapters", 5) or 5
                        ),
                        motif_repetition_recent_gap_chapters=int(
                            getattr(runner._settings, "motif_repetition_recent_gap_chapters", 2)
                            or 2
                        ),
                    )
                if critic_agent is not None:
                    # ``packet.canon_context`` is a bounded legacy projection
                    # (characters/recent_events/etc.), not the persisted
                    # StoryKernel schema.  Re-validating that projection as a
                    # StoryKernel always fails and needlessly triggers another
                    # LLM detector.  Use the authoritative bundle state, matching
                    # the initial CriticAgent pass.
                    _raw_canon_state = getattr(bundle, "canon_state", None)
                    _canon_state: StoryKernel
                    if isinstance(_raw_canon_state, dict):
                        _canon_state = StoryKernel.model_validate(_raw_canon_state)
                    elif isinstance(_raw_canon_state, StoryKernel):
                        _canon_state = _raw_canon_state
                    else:
                        _proj_id = (
                            getattr(bundle, "project_id", "")
                            or getattr(packet, "project_id", "")
                            or ""
                        )
                        _canon_state = StoryKernel(project_id=_proj_id)
                    critique_report = await critic_agent.critique_chapter(
                        chapter_number=chapter_number,
                        chapter_text=current_text,
                        canon_state=_canon_state,
                        chapter_outline=bundle.chapter_outline,
                        story_outline=getattr(bundle, "story_outline", None),
                        previous_chapter_text=getattr(packet, "previous_chapter_ending", "")
                        or None,
                        chapter_plan=plan,
                        check_alignment=False,
                        chapter_bridge=bridge,
                        previous_chapter_ending=getattr(packet, "previous_chapter_ending", "")
                        or "",
                        memory_hints=_recheck_memory_hints or None,
                    )
                    result = _convert_critique_to_continuity(critique_report, chapter_number)
                    if _recheck_prior_issues or _recheck_must_resolve:
                        result = ContinuityEvalStep._apply_recheck_focus(
                            result,
                            must_resolve_summaries=_recheck_must_resolve,
                            prior_issues=_recheck_prior_issues,
                        )
                    runner._on_step(
                        "continuity_recheck_via_critic",
                        {
                            "chapter": chapter_number,
                            "score": round(float(getattr(result, "continuity_score", 0.0)), 2),
                            "issues": len(getattr(result, "issues", []) or []),
                        },
                    )
                    return result
            except Exception as exc:
                _logger.warning(
                    "CriticAgent recheck failed for chapter %d, falling back to EvalStep: %s",
                    chapter_number,
                    exc,
                )
        # Fallback: ContinuityEvalStep
        continuity_eval_step = ContinuityEvalStep(
            runner._router, runner._builder, settings=runner._settings, trace=trace
        )
        motif_context, bible_anchor_terms, registry_word_sets = _build_dynamic_continuity_inputs(
            runner,
            bundle,
            packet,
            chapter_number,
        )
        return await continuity_eval_step.run(
            ContinuityEvalInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                chapter_state_packet=packet,
                chapter_bridge=bridge,
                chapter_plan=plan,
                pov_switch=getattr(bundle.chapter_outline, "pov_switch", False),
                motif_context=motif_context,
                bible_anchor_terms=bible_anchor_terms,
                project_path=getattr(bundle.layout, "root", None),
                recheck_mode=True,
                strict_review=True,
                prior_issues=_recheck_prior_issues,
                must_resolve_summaries=_recheck_must_resolve,
                patch_only_repair=bool(patch_only_repair),
                repaired_issue_types=_recheck_repaired_types,
                registry_kinship_terms=registry_word_sets.get("kinship_terms"),
                registry_rhetorical_hints=registry_word_sets.get("rhetorical_hints"),
                registry_emotion_keywords=registry_word_sets.get("emotion_keywords"),
                registry_bible_derived=registry_word_sets.get("bible_derived"),
                registry_genre=registry_word_sets.get("genre"),
                kernel_context=continuity_kernel_context,
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )
        )

    async def _task_chapter_repair() -> Any:
        from novel_forge.core.domain.guardrails import KNOWN_PROMPT_MARKERS

        chapter_repair_step = ChapterRepairStep(
            runner._router, runner._builder, settings=runner._settings, trace=trace
        )
        return await chapter_repair_step.run(
            ChapterRepairInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                canon_context=packet.canon_context,
                character_profiles=list(getattr(packet, "character_profiles", []) or []),
                previous_chapter_ending=packet.previous_chapter_ending,
                known_prompt_markers=list(KNOWN_PROMPT_MARKERS),
                check_mode=_repair_check_mode,
                previous_report=chapter_repair_report,
                changed_sections=_repair_changed_sections,
                change_ratio=_repair_change_ratio,
                time_convention=getattr(bundle.story_bible, "time_convention", "")
                if getattr(bundle, "story_bible", None)
                else "",
                address_rules=format_address_rules_for_prompt(bundle.story_bible)
                if getattr(bundle, "story_bible", None)
                else "",
                world_context_rules=render_world_context_rules(bundle.story_bible)
                if getattr(bundle, "story_bible", None)
                else "",
                forbidden_elements=list(getattr(plan, "forbidden_elements", []) or []),
                forbidden_elements_soft=list(getattr(plan, "forbidden_elements_soft", []) or []),
                expression_channel_records=list(
                    getattr(plan, "expression_channel_records", []) or []
                ),
                expression_channel_detection_enabled=bool(
                    getattr(runner._settings, "expression_channel_detection_enabled", True)
                ),
                intentional_callbacks=list(getattr(plan, "intentional_callbacks", []) or []),
                pov_character=getattr(bundle.chapter_outline, "pov_character", "") or "",
                scene_intents=list(getattr(plan, "scene_intents", []) or []),
                kernel_context=chapter_repair_kernel_context,
            )
        )

    # ── Parallel gather ───────────────────────────────────────────────────────
    _tasks: list[Any] = []
    _task_names: list[str] = []

    if not skip_alignment:
        _tasks.append(_task_alignment())
        _task_names.append("alignment")

    _tasks.append(_task_continuity_recheck())
    _task_names.append("continuity")

    if run_chapter_repair:
        _tasks.append(_task_chapter_repair())
        _task_names.append("chapter_repair")

    _results = await asyncio.gather(*_tasks, return_exceptions=True)

    assert len(_task_names) == len(_results), "task_names and results must have same length"
    for _tname, _tres in zip(_task_names, _results, strict=True):
        if isinstance(_tres, BaseException):
            _logger.warning(
                "post_repair_check_task_failed | chapter=%d | task=%s | error=%s",
                chapter_number,
                _tname,
                _tres,
            )
            continue
        if _tname == "alignment":
            alignment_report = _tres
            alignment_payload = alignment_report.model_dump(mode="json")
            alignment_payload["source_text_hash"] = source_text_hash(current_text)
            runner._storage.save_json(
                bundle.layout.alignment_report_path(chapter_number),
                alignment_payload,
            )
            runner._on_step("alignment_after_repair", alignment_report)
        elif _tname == "continuity":
            continuity_report = _tres
            continuity_payload = continuity_report.model_dump(mode="json")
            continuity_payload["source_text_hash"] = source_text_hash(current_text)
            runner._storage.save_json(
                bundle.layout.continuity_report_path(chapter_number),
                continuity_payload,
            )
            runner._on_step("continuity_eval_after_repair", continuity_report)
        elif _tname == "chapter_repair":
            chapter_repair_report = _tres
            runner._storage.save_json(
                bundle.layout.chapter_repair_report_path(chapter_number),
                chapter_repair_report.model_dump(mode="json"),
            )
            runner._on_step("chapter_repair_after_continuity", chapter_repair_report)
            if chapter_repair_report.prompt_leaks:
                _logger.warning(
                    "Prompt markers remain after repair (will continue): %s",
                    "；".join(chapter_repair_report.prompt_leaks[:3]),
                )

    return alignment_report, continuity_report, chapter_repair_report

__all__ = [
    "_build_memory_guidance",
    "_get_runner_episodic_memory",
    "run_post_repair_checks",
]
