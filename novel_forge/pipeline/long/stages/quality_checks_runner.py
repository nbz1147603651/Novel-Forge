# ruff: noqa: F403,F405,I001
"""Entry-point orchestration for the long-form quality stage."""

from __future__ import annotations

from novel_forge.pipeline.long.stages.quality_checks_common import *
from novel_forge.pipeline.long.stages.quality_checks_lib import *
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.pipeline.long.services.upstream_compass import (
    find_outline_plan_duration_conflicts,
)
from novel_forge.pipeline.long.stages.report_freshness import (
    ReportRefreshPlanner,
    quality_report_evidence_binding,
    stamp_report_freshness,
)


async def run_opening_guard_patch(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
) -> str:
    """Pre-screen opening continuity and run a narrow-window patch before full checks."""
    if chapter_number <= 1:
        return current_text
    if not bool(getattr(runner._settings, "long_opening_guard_enabled", True)):
        return current_text

    max_issues = max(1, int(getattr(runner._settings, "long_opening_guard_max_issues", 2) or 2))
    confidence_floor = _coerce_confidence(
        getattr(runner._settings, "local_check_confidence_threshold", 0.7),
        default=0.7,
    )
    confidence_floor = max(0.75, min(0.95, confidence_floor))

    from novel_forge.core.schemas.continuity import ContinuityIssue, ContinuityReport
    from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairStep

    try:
        local_eval_input = ContinuityEvalInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_state_packet=packet,
            chapter_bridge=bridge,
            chapter_plan=plan,
            pov_switch=getattr(bundle.chapter_outline, "pov_switch", False),
            project_path=getattr(bundle.layout, "root", None),
            boundary_prev_tail_paragraphs=getattr(
                runner._settings,
                "long_boundary_prev_tail_paragraphs",
                5,
            ),
            boundary_opening_paragraphs=getattr(
                runner._settings,
                "long_boundary_opening_paragraphs",
                3,
            ),
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
        local_issues = ContinuityEvalStep._build_local_issues(local_eval_input)
    except Exception as exc:
        _logger.warning(
            "opening_guard_prescreen_failed | chapter=%d | error=%s", chapter_number, exc
        )
        runner._on_step(
            "opening_guard_prescreen",
            {"chapter": chapter_number, "status": "failed", "error": str(exc)},
        )
        return current_text

    candidates: list[tuple[ContinuityIssue, dict[str, Any]]] = []
    for raw in local_issues:
        if not isinstance(raw, dict):
            continue
        if raw.get("requires_llm_judgment"):
            continue

        issue_type = str(raw.get("issue_type", "") or "").strip().lower()
        if not issue_type:
            continue

        severity = str(raw.get("severity", "medium") or "medium").strip().lower()
        if not severity_at_least(severity, "high"):
            continue

        confidence = _coerce_confidence(raw.get("confidence"), default=0.0)
        if confidence < confidence_floor:
            continue

        rewrite_scope = str(raw.get("rewrite_scope", "") or "").strip().lower()
        is_opening_scope = rewrite_scope in _OPENING_GUARD_SCOPE_TOKENS
        if not is_opening_scope and issue_type not in _OPENING_GUARD_STRUCTURAL_TYPES:
            continue

        affected_chars = raw.get("affected_characters")
        fix_acts = raw.get("fix_actions")
        issue = ContinuityIssue(
            issue_type=issue_type,
            severity=severity if severity in SEVERITY_RANK else "medium",
            summary=str(raw.get("summary", "") or "").strip(),
            evidence=str(raw.get("evidence", "") or "").strip(),
            location=str(raw.get("location", "") or "").strip(),
            location_confidence=float(raw.get("location_confidence", 0.0) or 0.0),
            anchor_type=str(raw.get("anchor_type", "") or "").strip(),
            paragraph_start=int(raw.get("paragraph_start", 0) or 0),
            paragraph_end=int(raw.get("paragraph_end", 0) or 0),
            evidence_quote=str(raw.get("evidence_quote", "") or "").strip(),
            fix_mode=str(raw.get("fix_mode", "") or "").strip(),
            insert_before_para=int(raw.get("insert_before_para", 0) or 0),
            insert_after_para=int(raw.get("insert_after_para", 0) or 0),
            affected_characters=affected_chars if isinstance(affected_chars, list) else [],
            rewrite_scope="opening" if is_opening_scope else rewrite_scope or "opening",
            fix_actions=fix_acts if isinstance(fix_acts, list) else [],
        )
        if not ContinuityRepairStep._is_patch_candidate(issue):
            continue
        if "结尾" in ContinuityRepairStep._infer_patch_location(issue):
            continue
        candidates.append((issue, raw))

    candidates.sort(key=_opening_guard_sort_key, reverse=True)
    selected = [item[0] for item in candidates[:max_issues]]

    runner._on_step(
        "opening_guard_prescreen",
        {
            "chapter": chapter_number,
            "status": "ok",
            "local_issue_count": len(local_issues),
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "confidence_floor": confidence_floor,
            "selected_types": [issue.issue_type for issue in selected],
        },
    )

    if not selected:
        return current_text

    guard_report = ContinuityReport(
        continuity_score=0.0,
        summary="开场硬门禁命中高置信度承接断裂问题，先行执行窄窗口修补。",
        issues=selected,
    )

    from novel_forge.pipeline.long.stages.continuity_repair import run_continuity_repair

    try:
        revised_text, guard_result = await run_continuity_repair(
            runner,
            bundle,
            packet,
            bridge,
            plan,
            guard_report,
            current_text,
            chapter_number,
            trace,
            must_fix_issues=selected,
        )
    except Exception as exc:
        _logger.warning("opening_guard_repair_failed | chapter=%d | error=%s", chapter_number, exc)
        _remember_opening_guard_pending_issues(
            runner,
            selected,
            reason=f"opening_guard_repair_failed:{type(exc).__name__}",
        )
        runner._on_step(
            "opening_guard_repair",
            {
                "chapter": chapter_number,
                "applied": False,
                "error": str(exc),
                "pending_issues": len(selected),
            },
        )
        return current_text

    changed = revised_text != current_text
    applied = bool(getattr(guard_result, "applied", False)) and changed
    if not applied:
        _remember_opening_guard_pending_issues(
            runner,
            selected,
            reason="opening_guard_repair_not_applied",
        )
    runner._on_step(
        "opening_guard_repair",
        {
            "chapter": chapter_number,
            "selected_count": len(selected),
            "applied": applied,
            "patch_only": bool(getattr(guard_result, "patch_only", False)),
            "failure_reason": getattr(guard_result, "failure_reason", None),
            "pending_issues": 0 if applied else len(selected),
        },
    )
    return revised_text if changed else current_text


def _editorial_boundary_context(bundle: Any) -> dict[str, Any]:
    source_slice = getattr(bundle, "chapter_source_slice", None)
    if source_slice is not None:
        try:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )

            cards = project_stage_source_cards(source_slice, stage="review")
            return {
                "chapter_contract": dict(cards.get("chapter_contract") or {}),
                "forbidden_reveal_boundaries": list(cards.get("forbidden_reveal_boundaries") or []),
            }
        except Exception as exc:  # noqa: BLE001
            _logger.warning("editorial_boundary_context_failed | error=%s", exc)

    contract = getattr(bundle, "chapter_contract", None)
    if contract is None:
        outline = getattr(bundle, "chapter_outline", None)
        contract = getattr(outline, "chapter_contract", None)
    model_dump = getattr(contract, "model_dump", None)
    if callable(model_dump):
        contract = model_dump(mode="json")
    if not isinstance(contract, dict):
        contract = {}

    forbidden: list[dict[str, Any]] = []
    for key in ("future_leak_risks", "forbidden_changes", "forbidden_progressions"):
        raw = contract.get(key)
        if isinstance(raw, list):
            forbidden.extend(
                item if isinstance(item, dict) else {"rule": str(item)} for item in raw
            )
    return {
        "chapter_contract": contract,
        "forbidden_reveal_boundaries": forbidden[:30],
    }


def _build_alignment_fallback_report(
    chapter_number: int,
    *,
    reason: str = "timeout",
    summary: str = "",
) -> Any:
    """Construct a fallback AlignmentReport when the real evaluation is unavailable.

    Mirrors the ``ReadingPowerReport._default_report`` pattern: the report is
    marked with ``is_fallback=True`` so downstream gates and threshold checks
    can exempt it from hard-blocking decisions.
    """
    from novel_forge.core.schemas.chapter import AlignmentReport

    fallback_summaries = {
        "timeout": "对齐评估因评审软超时未完成，此为兜底报告，分数不可信。",
        "llm_error": "对齐评估模型调用失败，此为兜底报告，分数不可信。",
        "truncated": "对齐评估响应被截断或部分解码，此为兜底报告，分数不可信。",
    }
    return AlignmentReport(
        alignment_score=0.0,
        risk_level="medium",
        summary=summary or fallback_summaries.get(reason, fallback_summaries["timeout"]),
        evaluation_status="fallback",
        is_fallback=True,
        fallback_reason=reason,
    )


async def run_quality_checks(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    window_manager: Any | None = None,
    window_config: Any | None = None,
    memory_hints: dict[str, Any] | None = None,
    precomputed_reading_power_report: Any | None = None,
    precomputed_reading_power_text_hash: str | None = None,
    precomputed_reading_power_sink: dict[str, Any] | None = None,
    precomputed_causal_sink: dict[str, Any] | None = None,
) -> tuple[Any, Any, Any]:
    """Run alignment and continuity checks against the current chapter draft."""
    # ── Fast-fail: empty text cannot be quality-checked ─────────────────────
    # Prevents wasting API calls on continuity/alignment checks when the
    # chapter text is empty. The error is transient so the upper layer
    # (LLMService) will retry with token escalation.
    if not current_text or not current_text.strip():
        raise ModelGatewayError(
            f"第 {chapter_number} 章正文为空，无法执行质量检查",
            is_transient=True,
        )

    current_text_hash = source_text_hash(current_text)
    evidence_binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )
    report_context_hash = str(evidence_binding["context_hash"])
    report_evidence_hashes = dict(evidence_binding["evidence_hashes"])
    upstream_conflicts = find_outline_plan_duration_conflicts(
        outline=getattr(bundle, "chapter_outline", None),
        plan=plan,
    )
    if upstream_conflicts:
        requires_source_revision = any(
            str(item.get("recovery_target")) == "manual" for item in upstream_conflicts
        )
        runner._on_step(
            "review_upstream_evidence_conflict",
            {
                "chapter": chapter_number,
                "conflicts": upstream_conflicts,
                "report_context_hash": report_context_hash,
                "action": (
                    "revise_upstream_source_before_scoring"
                    if requires_source_revision
                    else "replan_before_scoring"
                ),
            },
        )
        messages = [
            (
                f"审查证据内部冲突：「{item['subject']}」"
                f"大纲={item['outline_days']}日，Plan={item['plan_days']}日。"
                + (
                    "大纲内部已冲突，必须先修订并审批源证据；不得改正文或无限重做 Plan。"
                    if str(item.get("recovery_target")) == "manual"
                    else "必须先修复 Plan，不得让正文迁就错误证据。"
                )
            )
            for item in upstream_conflicts
        ]
        raise ConsistencyViolationError(
            messages,
            violation_kind=(
                "upstream_source_conflict"
                if requires_source_revision
                else "upstream_plan_conflict"
            ),
            failed_stage="review_upstream_evidence",
            replan_target=(RecoveryTarget.MANUAL if requires_source_revision else RecoveryTarget.PLAN),
        )

    # Short text warning (non-blocking)
    text_char_count = count_chapter_words(current_text)
    if text_char_count < 500:
        runner._on_step(
            "quality_check_short_text_warning",
            {
                "chapter": chapter_number,
                "text_chars": text_char_count,
            },
        )

    from novel_forge.pipeline.long.stages.continuity_repair import _convert_critique_to_continuity

    audit_context: Any = None
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    continuity_kernel_context = (
        kernel_composer.compose_continuity_eval_input(chapter_number, current_text)
        if kernel_composer is not None
        else {}
    )
    alignment_kernel_context = (
        kernel_composer.compose_alignment_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    chapter_repair_kernel_context = (
        kernel_composer.compose_check_chapter_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    source_slice = getattr(bundle, "chapter_source_slice", None)
    if source_slice is not None:
        from novel_forge.pipeline.long.services.context.source_artifacts import project_stage_source_cards

        world_rule_card = project_stage_source_cards(source_slice, stage="review").get(
            "world_rule_card", {}
        )
    else:
        world_rule_card = {}

    if runner.has_audit_coordinator() and runner.audit_coordinator is not None:
        try:
            runner._on_step(
                "audit_context_preparing", {"chapter": chapter_number, "stage": "preparing_context"}
            )
            active_characters = _extract_active_characters(current_text, packet)
            audit_context = await runner.audit_coordinator.prepare_audit_context(
                chapter_number=chapter_number,
                chapter_text=current_text,
                chapter_type=_infer_chapter_type(current_text, bundle),
                active_characters=active_characters,
            )
            runner._on_step(
                "audit_context_ready",
                {
                    "chapter": chapter_number,
                    "has_history": audit_context.has_relevant_history(),
                    "has_characters": audit_context.has_character_history(),
                    "has_motifs": audit_context.has_motif_context(),
                    "memory_available": audit_context.memory_available,
                },
            )
            _logger.info(
                "AuditContext prepared | chapter=%d | has_history=%s | has_characters=%s | has_motifs=%s",
                chapter_number,
                audit_context.has_relevant_history(),
                audit_context.has_character_history(),
                audit_context.has_motif_context(),
            )
        except Exception as exc:
            _logger.warning(
                "AuditCoordinator context preparation failed for chapter %d: %s",
                chapter_number,
                exc,
            )
            audit_context = None

    async def _task_critic() -> "CritiqueReport | None":
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
                        getattr(runner._settings, "memory_critic_agent_timeout_extend_attempts", 1)
                        or 0
                    ),
                    timeout_extend_multiplier=float(
                        getattr(
                            runner._settings, "memory_critic_agent_timeout_extend_multiplier", 1.5
                        )
                        or 1.0
                    ),
                    cache_enabled=bool(
                        getattr(runner._settings, "memory_critic_agent_cache_enabled", True)
                    ),
                    cache_max_entries=int(
                        getattr(runner._settings, "memory_critic_agent_cache_max_entries", 24) or 24
                    ),
                    motif_repetition_lookback_chapters=int(
                        getattr(runner._settings, "motif_repetition_lookback_chapters", 5) or 5
                    ),
                    motif_repetition_recent_gap_chapters=int(
                        getattr(runner._settings, "motif_repetition_recent_gap_chapters", 2) or 2
                    ),
                )
                try:
                    memory_ctx.critic_agent = critic_agent
                except Exception as exc:
                    _logger.debug(
                        "Could not cache critic_agent on memory_ctx (read-only?): %s", exc
                    )
            if critic_agent is None:
                _logger.info(
                    "CriticAgent skipped | reason=disabled_or_unavailable | chapter=%d",
                    chapter_number,
                )
                return None

            _raw_canon_state = getattr(bundle, "canon_state", None)
            _canon_state: StoryKernel
            if isinstance(_raw_canon_state, dict):
                _canon_state = StoryKernel.model_validate(_raw_canon_state)
            elif isinstance(_raw_canon_state, StoryKernel):
                _canon_state = _raw_canon_state
            else:
                _proj_id = (
                    getattr(bundle, "project_id", "") or getattr(packet, "project_id", "") or ""
                )
                _canon_state = StoryKernel(project_id=_proj_id)

            result: CritiqueReport = await critic_agent.critique_chapter(
                chapter_number=chapter_number,
                chapter_text=current_text,
                canon_state=_canon_state,
                chapter_outline=bundle.chapter_outline,
                story_outline=getattr(bundle, "story_outline", None),
                previous_chapter_text=getattr(packet, "previous_chapter_ending", "") or None,
                chapter_plan=plan,
                check_alignment=True,
                chapter_bridge=bridge,
                previous_chapter_ending=getattr(packet, "previous_chapter_ending", "") or "",
                audit_context=audit_context,
                memory_hints=memory_hints,
            )

            alignment_score = 0.0
            if result and result.alignment_result:
                alignment_score = result.alignment_result.alignment_score
            runner._on_step(
                "critique_completed",
                {
                    "chapter": chapter_number,
                    "overall_score": result.overall_score if result else 0.0,
                    "issue_count": len(result.issues) if result else 0,
                    "critical_issues": sum(1 for i in result.issues if i.severity == "critical")
                    if result
                    else 0,
                    "high_issues": sum(1 for i in result.issues if i.severity == "high")
                    if result
                    else 0,
                    "warnings": result.warnings if result else [],
                    "audit_context_used": audit_context is not None,
                    "alignment_score": alignment_score,
                },
            )
            _logger.info(
                "CriticAgent completed | chapter=%d | score=%.2f | issues=%d | alignment=%.1f | audit_ctx=%s",
                chapter_number,
                result.overall_score if result else 0.0,
                len(result.issues) if result else 0,
                alignment_score,
                audit_context is not None,
            )
            motif_tracker = getattr(memory_ctx, "_motif_tracker", None)
            if motif_tracker is not None:
                extraction_cache = getattr(motif_tracker, "_extraction_cache", {})
                cached_occs = extraction_cache.get(chapter_number)
                motif_cache = getattr(memory_ctx, "_motif_cache", None)
                if cached_occs and motif_cache is not None and not motif_cache.get(chapter_number):
                    motif_cache[chapter_number] = cached_occs
                    try:
                        memory_ctx.save_to_disk()
                    except Exception as exc:
                        _logger.warning(
                            "memory_ctx.save_to_disk() failed after CriticAgent | error=%s", exc
                        )
            return result
        except Exception as exc:
            _logger.warning("CriticAgent failed for chapter %d: %s", chapter_number, exc)
            return None

    async def _task_continuity_eval() -> Any:
        continuity_eval_step = ContinuityEvalStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        motif_context, bible_anchor_terms, registry_word_sets = _build_dynamic_continuity_inputs(
            runner,
            bundle,
            packet,
            chapter_number,
        )
        result = await continuity_eval_step.run(
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
                registry_kinship_terms=registry_word_sets.get("kinship_terms"),
                registry_rhetorical_hints=registry_word_sets.get("rhetorical_hints"),
                registry_emotion_keywords=registry_word_sets.get("emotion_keywords"),
                registry_bible_derived=registry_word_sets.get("bible_derived"),
                registry_genre=registry_word_sets.get("genre"),
                kernel_context=continuity_kernel_context,
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )
        )
        runner._on_step(
            "continuity_eval_fallback", {"chapter": chapter_number, "reason": "critic_unavailable"}
        )
        return result

    async def _task_alignment_step() -> Any:
        alignment_step = AlignmentStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        return await alignment_step.run(
            AlignmentInput(
                chapter_outline=bundle.chapter_outline,
                chapter_plan=plan,
                chapter_text=current_text,
                kernel_context=alignment_kernel_context,
            )
        )

    async def _task_chapter_repair() -> Any:
        chapter_repair_step = ChapterRepairStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        return await chapter_repair_step.run(
            ChapterRepairInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                canon_context=packet.canon_context,
                character_profiles=list(getattr(packet, "character_profiles", []) or []),
                previous_chapter_ending=packet.previous_chapter_ending,
                known_prompt_markers=list(KNOWN_PROMPT_MARKERS),
                time_convention=getattr(bundle.story_bible, "time_convention", "")
                if getattr(bundle, "story_bible", None)
                else "",
                address_rules=format_address_rules_for_prompt(bundle.story_bible)
                if getattr(bundle, "story_bible", None)
                else "",
                world_context_rules=render_world_context_rules(
                    bundle.story_bible,
                    include_world_rules=False,
                )
                if getattr(bundle, "story_bible", None)
                else "",
                world_rule_card=world_rule_card,
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

    async def _task_editorial_check() -> Any:
        editorial_contract = getattr(bundle, "editorial_contract", None)
        if editorial_contract is None:
            return None
        editorial_step = EditorialCheckStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        from novel_forge.editorial.cards import build_editorial_risk_guidance

        risk_guidance = build_editorial_risk_guidance(
            editorial_contract,
            readiness=getattr(bundle, "editorial_readiness", None),
            chapter_number=chapter_number,
            stage="check",
        )
        boundary_context = _editorial_boundary_context(bundle)
        return await editorial_step.run(
            EditorialCheckInput(
                chapter_number=chapter_number,
                chapter_text=current_text,
                editorial_contract=editorial_contract,
                chapter_contract=boundary_context["chapter_contract"],
                forbidden_reveal_boundaries=boundary_context["forbidden_reveal_boundaries"],
                extra_context={"editorial_risk_guidance": risk_guidance},
            )
        )

    def _lookup_alignment_cache() -> Any | None:
        # A score from a different chapter can never be evidence for this
        # chapter, even if prose happens to be similar. Reuse only the current
        # chapter's exact text+upstream binding through the shared planner.
        from novel_forge.core.schemas.chapter import AlignmentReport

        path = bundle.layout.alignment_report_path(chapter_number)
        decision = ReportRefreshPlanner(
            storage=runner._storage,
            current_text_hash=current_text_hash,
            context_hash=report_context_hash,
        ).decide(
            dimension="alignment",
            provided=None,
            path=path,
            model=AlignmentReport,
        )
        if not decision.should_reuse:
            return None
        runner._on_step(
            "alignment_cached",
            {
                "chapter": chapter_number,
                "source": decision.source,
                "source_text_hash": current_text_hash,
                "report_context_hash": report_context_hash,
                "reason": decision.reason,
                "cached": True,
            },
        )
        return decision.report

    alignment_report: Any = None
    continuity_report: Any
    chapter_repair_report: Any = None
    critique_report: CritiqueReport | None = None
    check_enabled: bool = bool(getattr(runner._settings, "long_check_chapter_enabled", False))

    if runner.has_memory_context():
        if check_enabled:
            check_results = await asyncio.gather(
                _task_critic(), _task_chapter_repair(), return_exceptions=True
            )
            critique_task_result = check_results[0]
            repair_task_result = check_results[1]
            critique_report = (
                critique_task_result
                if not isinstance(critique_task_result, BaseException)
                else None
            )
            if isinstance(critique_task_result, BaseException):
                _logger.warning(
                    "CriticAgent task raised for chapter %d: %s",
                    chapter_number,
                    critique_task_result,
                )
            chapter_repair_report = (
                repair_task_result if not isinstance(repair_task_result, BaseException) else None
            )
            if isinstance(repair_task_result, BaseException):
                _logger.warning(
                    "ChapterRepair task raised for chapter %d: %s",
                    chapter_number,
                    repair_task_result,
                )
        else:
            critique_report = await _task_critic()

        if critique_report is not None:
            continuity_report = _convert_critique_to_continuity(critique_report, chapter_number)
            if critique_report.alignment_result:
                alignment_report = critique_report.to_alignment_report()
            else:
                _cached = _lookup_alignment_cache()
                if _cached is not None:
                    alignment_report = _cached
                else:
                    try:
                        alignment_report = await _task_alignment_step()
                    except Exception as exc:
                        _logger.warning(
                            "alignment_step_fallback_failed | chapter=%d | error=%s",
                            chapter_number,
                            exc,
                        )
                        _reason = "timeout" if critique_report.alignment_timed_out else "llm_error"
                        alignment_report = _build_alignment_fallback_report(
                            chapter_number, reason=_reason
                        )
                        runner._on_step(
                            "alignment_fallback",
                            {
                                "chapter": chapter_number,
                                "reason": _reason,
                                "error": str(exc)[:200],
                            },
                        )
        else:
            _cached = _lookup_alignment_cache()
            _fb_tasks: list[Any] = [_task_continuity_eval()]
            _fb_task_names = ["continuity"]
            _fb_include_alignment = _cached is None
            if _fb_include_alignment:
                _fb_tasks.append(_task_alignment_step())
                _fb_task_names.append("alignment")
            _fb_results = await asyncio.gather(*_fb_tasks, return_exceptions=True)
            continuity_report = _convert_critique_to_continuity(None, chapter_number)
            alignment_report = _cached
            assert len(_fb_task_names) == len(_fb_results), (
                "task_names and results must have same length"
            )
            for _tname, _tres in zip(_fb_task_names, _fb_results, strict=True):
                if isinstance(_tres, BaseException):
                    _logger.warning(
                        "quality_check_fallback_failed | chapter=%d | task=%s | error=%s",
                        chapter_number,
                        _tname,
                        _tres,
                    )
                    continue
                if _tname == "continuity":
                    continuity_report = _tres
                elif _tname == "alignment":
                    alignment_report = _tres
    else:
        _cached = _lookup_alignment_cache()
        _tasks: list[Any] = [_task_continuity_eval()]
        _include_alignment = _cached is None
        if _include_alignment:
            _tasks.append(_task_alignment_step())
        if check_enabled:
            _tasks.append(_task_chapter_repair())
        _task_names = (
            ["continuity"]
            + (["alignment"] if _include_alignment else [])
            + (["chapter_repair"] if check_enabled else [])
        )
        _results = await asyncio.gather(*_tasks, return_exceptions=True)
        continuity_report = _convert_critique_to_continuity(None, chapter_number)
        for _tname, _tres in zip(_task_names, _results):  # noqa: B905
            if isinstance(_tres, BaseException):
                _logger.warning(
                    "quality_check_task_failed | chapter=%d | task=%s | error=%s",
                    chapter_number,
                    _tname,
                    _tres,
                )
                continue
            if _tname == "continuity":
                continuity_report = _tres
            elif _tname == "alignment":
                alignment_report = _tres
            elif _tname == "chapter_repair":
                chapter_repair_report = _tres
        if not _include_alignment:
            alignment_report = _cached
        if not hasattr(continuity_report, "continuity_score"):
            continuity_report = _convert_critique_to_continuity(None, chapter_number)

    continuity_payload = continuity_report.model_dump(mode="json")
    stamp_report_freshness(
        continuity_payload,
        current_hash=current_text_hash,
        context_hash=report_context_hash,
        evidence_hashes=report_evidence_hashes,
    )
    from novel_forge.pipeline.long.services.context.story_kernel_context import (
        load_canon_state_hash_for_bundle,
    )

    continuity_payload["canon_state_hash"] = await load_canon_state_hash_for_bundle(runner, bundle)
    runner._storage.save_json(
        bundle.layout.continuity_report_path(chapter_number), continuity_payload
    )
    runner._on_step("continuity_eval", continuity_report)

    if alignment_report is None:
        try:
            alignment_report = await _task_alignment_step()
        except Exception as exc:
            _logger.warning(
                "alignment_step_final_fallback_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
            alignment_report = _build_alignment_fallback_report(chapter_number, reason="llm_error")
            runner._on_step(
                "alignment_fallback",
                {
                    "chapter": chapter_number,
                    "reason": "llm_error",
                    "error": str(exc)[:200],
                },
            )
    _alignment_payload = (
        alignment_report
        if isinstance(alignment_report, dict)
        else alignment_report.model_dump(mode="json")
    )
    stamp_report_freshness(
        _alignment_payload,
        current_hash=current_text_hash,
        context_hash=report_context_hash,
        evidence_hashes=report_evidence_hashes,
    )
    runner._storage.save_json(
        bundle.layout.alignment_report_path(chapter_number), _alignment_payload
    )
    runner._on_step("alignment", alignment_report)

    _push_audit_result_to_ui(runner, chapter_number, critique_report, alignment_report)
    _persist_critic_report(
        runner,
        bundle.layout,
        chapter_number,
        critique_report,
        current_text,
        context_hash=report_context_hash,
        evidence_hashes=report_evidence_hashes,
    )

    if chapter_repair_report is not None:
        _chapter_repair_payload = chapter_repair_report.model_dump(mode="json")
        stamp_report_freshness(
            _chapter_repair_payload,
            current_hash=current_text_hash,
            context_hash=report_context_hash,
            evidence_hashes=report_evidence_hashes,
        )
        runner._storage.save_json(
            bundle.layout.chapter_repair_report_path(chapter_number),
            _chapter_repair_payload,
        )
        runner._on_step("chapter_repair_after_alignment", chapter_repair_report)

    try:
        editorial_report = await _task_editorial_check()
        if editorial_report is not None:
            _editorial_payload = editorial_report.model_dump(mode="json")
            stamp_report_freshness(
                _editorial_payload,
                current_hash=current_text_hash,
                context_hash=report_context_hash,
                evidence_hashes=report_evidence_hashes,
            )
            runner._storage.save_json(
                bundle.layout.reports_dir / f"chapter_{chapter_number:03d}_editorial.json",
                _editorial_payload,
            )
            runner._on_step("check_editorial", editorial_report)
    except Exception as exc:
        _logger.warning("editorial_check_failed | chapter=%d | error=%s", chapter_number, exc)

    from novel_forge.pipeline.long.stages.causal_repair import run_causal_validation

    _causal_task = run_causal_validation(
        runner,
        bundle,
        bridge,
        current_text,
        chapter_number,
        trace,
        previous_chapter_ending=getattr(packet, "previous_chapter_ending", "") or "",
        recheck_mode=False,
        strict_review=False,
    )
    _rp_task = evaluate_and_record_reading_power(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        trace=trace,
        window_manager=window_manager,
        pipeline_stage="quality_stage",
        step_name="reading_power_eval",
        update_window=False,
        precomputed_report=precomputed_reading_power_report,
        precomputed_text_hash=precomputed_reading_power_text_hash,
    )
    sidecar_results: tuple[Any | BaseException, Any | BaseException] = await asyncio.gather(
        _causal_task,
        _rp_task,
        return_exceptions=True,
    )
    causal_result, reading_power_result = sidecar_results

    def _persist_sidecar_binding(result: Any, path: Any) -> None:
        if result is None or isinstance(result, BaseException) or path is None:
            return
        payload = (
            dict(result)
            if isinstance(result, dict)
            else result.model_dump(mode="json")
        )
        stamp_report_freshness(
            payload,
            current_hash=current_text_hash,
            context_hash=report_context_hash,
            evidence_hashes=report_evidence_hashes,
        )
        runner._storage.save_json(path, payload)

    causal_path_factory = getattr(bundle.layout, "chapter_causal_report_path", None)
    reading_power_path_factory = getattr(bundle.layout, "reading_power_report_path", None)
    _persist_sidecar_binding(
        causal_result,
        causal_path_factory(chapter_number) if callable(causal_path_factory) else None,
    )
    _persist_sidecar_binding(
        reading_power_result,
        reading_power_path_factory(chapter_number)
        if callable(reading_power_path_factory)
        else None,
    )
    if precomputed_causal_sink is not None and not isinstance(causal_result, BaseException):
        precomputed_causal_sink["causal_report"] = causal_result
        precomputed_causal_sink["source_text_hash"] = source_text_hash(current_text)
    if precomputed_reading_power_sink is not None and not isinstance(
        reading_power_result,
        BaseException,
    ):
        precomputed_reading_power_sink["reading_power_report"] = reading_power_result
        precomputed_reading_power_sink["source_text_hash"] = source_text_hash(current_text)
    if isinstance(reading_power_result, BaseException):
        _logger.debug("reading_power_eval sidecar failed: %s", reading_power_result)

    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            load_stage_artifact,
            persist_stage_artifact,
        )

        previous_artifact = load_stage_artifact(
            runner._storage,
            bundle.layout,
            chapter_number=chapter_number,
            artifact_type="wave",
        )
        persist_stage_artifact(
            storage=runner._storage,
            layout=bundle.layout,
            project_id=getattr(bundle, "project_id", "") or "unknown",
            chapter_number=chapter_number,
            artifact_type="review",
            payload={
                "text_hash": source_text_hash(current_text),
                "text_chars": len(current_text),
                "reports": {
                    "alignment": _artifact_report_summary(alignment_report),
                    "continuity": _artifact_report_summary(continuity_report),
                    "chapter_repair": _artifact_report_summary(chapter_repair_report),
                    "causal": (
                        {}
                        if isinstance(causal_result, BaseException)
                        else _artifact_report_summary(causal_result)
                    ),
                    "reading_power": (
                        {}
                        if isinstance(reading_power_result, BaseException)
                        else _artifact_report_summary(reading_power_result)
                    ),
                },
            },
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            previous_artifact=previous_artifact,
        )
    except Exception as exc:
        _logger.warning(
            "review_stage_artifact_persist_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )

    return alignment_report, continuity_report, chapter_repair_report


async def check_guard_constraint_compliance(
    runner: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    *,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    """检查本章是否遵守了上一章的 AI 护栏约束与章节契约禁令。

    通过分析章节文本，验证 guard_constraints 与 chapter_contract.forbidden_changes
    中的每条约束是否得到回应。

    Args:
        runner: ChapterRunner 实例
        bundle: ChapterBundle 实例
        packet: ChapterStatePacket 实例
        current_text: 本章正文
        chapter_number: 本章章节号

    Returns:
        合规报告字典，包含：
        - constraints: 约束列表
        - compliance_results: 每条约束的合规状态
        - overall_compliance_rate: 整体合规率
        - summary: 总结
    """
    guard_constraints = list(constraints) if constraints is not None else guard_constraints_for_packet(packet)
    if not guard_constraints:
        return {
            "constraints": [],
            "compliance_results": [],
            "overall_compliance_rate": 1.0,
            "checked_count": 0,
            "compliant_count": 0,
            "actionable_violation_count": 0,
            "unverified_count": 0,
            "check_failed_count": 0,
            "summary": "无护栏约束需要检查",
        }

    known_subjects = _collect_guard_known_subjects(packet, bundle)

    # 并行评估所有约束检查（各约束相互独立，无数据依赖）
    async def _check_one(constraint: str) -> dict[str, Any]:
        result = _evaluate_local_guard_constraint(
            constraint=constraint,
            current_text=current_text,
        )
        if result is None:
            result = await _evaluate_single_constraint_compliance(
                runner=runner,
                constraint=constraint,
                chapter_text=current_text,
                chapter_number=chapter_number,
            )
        result["constraint_subjects"] = _guard_constraint_subjects(constraint, known_subjects)
        return result

    raw_results = list(await asyncio.gather(*[_check_one(c) for c in guard_constraints]))
    compliance_results: list[dict[str, Any]] = []
    for result in raw_results:
        normalized = _normalize_guard_result_for_actionability(
            result,
            current_text=current_text,
        )
        compliance_results.append(normalized)

    checked_results = [
        result
        for result in compliance_results
        if not _is_guard_check_error(result)
        and _normalize_guard_status(result.get("status")) in _GUARD_CHECKABLE_STATUSES
    ]
    compliant_count = sum(1 for r in checked_results if r.get("status") == "compliant")
    actionable_violation_count = sum(1 for r in checked_results if _guard_result_is_actionable(r))
    unverified_count = sum(
        1
        for r in compliance_results
        if not _is_guard_check_error(r) and _normalize_guard_status(r.get("status")) == "unknown"
    )
    check_failed_count = sum(1 for r in compliance_results if _is_guard_check_error(r))
    checked_count = len(checked_results)
    overall_rate = compliant_count / checked_count if checked_count else None

    summary_parts = [f"共 {len(guard_constraints)} 条约束，{compliant_count} 条已遵守"]
    if checked_count:
        summary_parts.append(f"合规率 {overall_rate:.0%}")
    else:
        summary_parts.append("合规率未计算")
    if actionable_violation_count:
        summary_parts.append(f"{actionable_violation_count} 条需要修复")
    if unverified_count:
        summary_parts.append(f"{unverified_count} 条无法确认")
    if check_failed_count:
        summary_parts.append(f"{check_failed_count} 条检查失败")

    return {
        "constraints": guard_constraints,
        "compliance_results": compliance_results,
        "overall_compliance_rate": round(overall_rate, 2) if overall_rate is not None else None,
        "checked_count": checked_count,
        "compliant_count": compliant_count,
        "actionable_violation_count": actionable_violation_count,
        "unverified_count": unverified_count,
        "check_failed_count": check_failed_count,
        "summary": "；".join(summary_parts),
    }


def _guard_check_escalation_update(
    exc: Exception,
    response: Any | None,
    current_request: Any,
    *,
    route_max_tokens: int | None = None,
) -> dict[str, Any] | None:
    max_tokens = int(getattr(current_request, "max_tokens", 0) or 0)
    max_allowed = max(int(route_max_tokens or max_tokens), max_tokens)
    if max_tokens >= max_allowed:
        return None
    content = str(getattr(response, "content", "") or "")
    finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
    completion_tokens = int(getattr(response, "completion_tokens", 0) or 0)
    looks_truncated = (
        finish_reason == "length"
        or not content.strip()
        or (max_tokens > 0 and completion_tokens >= int(max_tokens * 0.9))
    )
    if not looks_truncated:
        return None
    next_max_tokens = llm_h.escalate_retry_tokens(
        max_tokens or min(max_allowed, 1024),
        TaskType.GUARD_CONSTRAINT_CHECK.value,
        model_max_tokens=max_allowed,
    )
    next_max_tokens = min(max_allowed, next_max_tokens)
    if next_max_tokens <= max_tokens:
        return None
    _logger.warning(
        "guard_constraint_check_escalating_tokens | max_tokens=%d -> %d | error=%s",
        max_tokens,
        next_max_tokens,
        exc,
    )
    return {"max_tokens": next_max_tokens, "temperature": 0.0}


def _guard_check_initial_max_tokens(router: Any, chapter_text: str) -> tuple[int, int]:
    from novel_forge.core.parsing.text_utils import calculate_safe_max_tokens

    route_max_tokens = route_output_limit(router, TaskType.GUARD_CONSTRAINT_CHECK)
    # This check returns a four-field, compact object.  Starting at the global
    # FULL_OBJECT floor would consume the route's entire output ceiling and
    # leave no room for the truncation escalation below.  Keep a bounded first
    # attempt, then use the shared retry helper to grow toward the real route
    # limit only when the provider reports a damaged/length response.
    initial_cap = min(route_max_tokens, max(1024, route_max_tokens // 2))
    initial_max_tokens = calculate_safe_max_tokens(
        max(_GUARD_CHECK_INITIAL_OUTPUT_CHARS, min(len(chapter_text) // 6, 1600)),
        prompt_overhead=0,
        model_limit=initial_cap,
        min_tokens=min(1024, initial_cap),
    )
    return initial_max_tokens, route_max_tokens


async def _evaluate_single_constraint_compliance(
    runner: Any,
    constraint: str,
    chapter_text: str,
    chapter_number: int,
) -> dict[str, Any]:
    """评估单条护栏约束的合规性。

    Args:
        runner: ChapterRunner 实例
        constraint: 护栏约束文本
        chapter_text: 本章正文
        chapter_number: 本章章节号

    Returns:
        合规性评估结果字典
    """
    try:
        initial_max_tokens, route_max_tokens = _guard_check_initial_max_tokens(
            runner._router,
            chapter_text,
        )
        context = {
            "constraint": constraint,
            "chapter_text": chapter_text[:8000],  # 限制文本长度
            "chapter_number": chapter_number,
        }
        request = runner._builder.build(
            TaskType.GUARD_CONSTRAINT_CHECK,
            context,
            max_tokens=initial_max_tokens,
            temperature=0.1,
        )
        parsed = await llm_h.route_json_object_with_retry(
            runner._router,
            request,
            task_type=TaskType.GUARD_CONSTRAINT_CHECK,
            context=context,
            escalation_request_update=(
                lambda exc, response, retry_request: _guard_check_escalation_update(
                    exc,
                    response,
                    retry_request,
                    route_max_tokens=route_max_tokens,
                )
            ),
        )

        return {
            "constraint": constraint,
            "status": _normalize_guard_status(parsed.get("status", "unknown")),
            "confidence": parsed.get("confidence", 0.0),
            "evidence": parsed.get("evidence", ""),
            "notes": parsed.get("notes", ""),
            "check_error": False,
        }
    except Exception as exc:
        _logger.warning(
            "guard_constraint_check_failed | chapter=%d | constraint=%s | error=%s",
            chapter_number,
            constraint[:50],
            exc,
        )
        return {
            "constraint": constraint,
            "status": "check_error",
            "confidence": 0.0,
            "evidence": "",
            "notes": f"检查过程出错：{exc}",
            "check_error": True,
            "error_type": type(exc).__name__,
            "repairable": False,
        }


__all__ = [
    # Core checks
    "run_quality_checks",
    "run_opening_guard_patch",
    "merge_opening_guard_pending_issues",
    "ReadingPowerRepairLoopResult",
    "evaluate_and_record_reading_power",
    "check_guard_constraint_compliance",
    "build_guard_compliance_findings",
    "compile_guard_repair_tickets",
    "attach_guard_repair_metadata",
    "guard_constraints_available",
    "guard_constraints_for_packet",
    "guard_report_has_actionable_low_compliance",
    "guard_report_incomplete_warning",
    # Shared helpers (used by sibling modules)
    "_text_change_ratio",
    "_compute_text_similarity",
    "_should_skip_alignment_recheck",
    "_coerce_confidence",
    "_extract_active_characters",
    "_infer_chapter_type",
    "_build_reading_power_excerpt",
    "_build_reading_power_input",
    "detect_drift",
    "diff_issues",
    "_push_audit_result_to_ui",
]
