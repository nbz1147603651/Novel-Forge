"""Planning stage: generate bridge and chapter plan."""

from __future__ import annotations

import ast
import re
from types import SimpleNamespace
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.schemas.continuity import ChapterPlan
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.utils.field_extractor import field
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.canon_ops import persist_bridge, persist_chapter_state_packet
from novel_forge.pipeline.long.services.chapter_position import build_chapter_position
from novel_forge.pipeline.long.services.context.source_artifacts import persist_stage_artifact
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    collect_planning_memory_hints,
    persist_stage_memory_diagnostics_report,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.services.element_progress import (
    build_planning_hint,
    record_schedule,
    suggest_dynamic_focus_ids,
)
from novel_forge.pipeline.long.services.element_schedule import (
    build_element_schedule_hint,
    merge_schedule_hint_into_progress_hint,
)
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.generation.strand_weave import (
    StrandTracker,
    build_strand_config_from_profile,
    build_strand_hint_for_planning,
)
from novel_forge.pipeline.long.services.guidance_contract_audit import (
    GuidanceAuditPolicy,
    audit_plan_contract,
    compile_guidance_repair_tickets,
)
from novel_forge.pipeline.long.services.guidance_contract_audit import (
    blocking_messages as guidance_blocking_messages,
)
from novel_forge.pipeline.long.services.time_validation import (
    build_time_context_for_planning,
    validate_time_consistency,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    audit_upstream_compass,
    build_location_transition_semantic_check,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    blocking_messages as upstream_compass_blocking_messages,
)
from novel_forge.pipeline.style_profile_helpers import get_profile_section
from novel_forge.pipeline.token_budget import route_max_output_budget

_logger = get_logger("pipeline.long.stages.planning")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list | tuple | set) else [value]
    items: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def _append_unique(items: list[str], value: str) -> None:
    text = _text(value)
    if text and text not in items:
        items.append(text)


def _atomic_event_items(value: Any) -> list[str]:
    """Return semicolon-delimited event text as an ordered atomic checklist."""

    items: list[str] = []
    for raw in _string_list(value):
        stripped = raw.strip()
        if stripped.startswith(("[", "(")) and stripped.endswith(("]", ")")):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, list | tuple | set):
                for item in _atomic_event_items(parsed):
                    if item not in items:
                        items.append(item)
                continue
        for item in re.split(r"[；;\n]+", raw):
            text = _text(item)
            if text and text not in items:
                items.append(text)
    return items


def materialize_scene_event_ownership(plan: ChapterPlan) -> ChapterPlan:
    """Ensure every declared scene outcome reaches DRAFT as an atomic owned event.

    ``required_outcome`` remains a concise scene summary, while ``owned_events``
    is the executable P0 checklist.  Legacy plans often put several hard
    outcomes in one semicolon-delimited string or omitted the ownership field
    entirely; normalize those plans before any writing stage consumes them.
    """

    plan_payload = plan.model_dump(mode="json")
    changed = False
    for scene in list(plan_payload.get("scene_intents") or []):
        if not isinstance(scene, dict):
            continue
        outcome_items = _atomic_event_items(scene.get("required_outcome", ""))
        owned_events = _atomic_event_items(scene.get("owned_events", []))
        for item in outcome_items:
            if item not in owned_events:
                owned_events.append(item)
        if owned_events != list(scene.get("owned_events") or []):
            scene["owned_events"] = owned_events
            changed = True
        normalized_outcome = "；".join(outcome_items)
        if normalized_outcome and normalized_outcome != _text(scene.get("required_outcome", "")):
            scene["required_outcome"] = normalized_outcome
            changed = True
    return ChapterPlan.model_validate(plan_payload) if changed else plan


def _inject_reading_power_closing_contract(
    plan: ChapterPlan,
    reading_power_hint: dict[str, Any] | None,
    *,
    chapter_number: int,
) -> ChapterPlan:
    """Preserve hook/payoff guidance if a structural audit replans a chapter."""

    if not reading_power_hint:
        return plan
    try:
        hook_constraint = _text(reading_power_hint.get("hook_type_constraint"))
        payoff_guidance = _text(reading_power_hint.get("payoff_guidance"))
        plan_dict = plan.model_dump(mode="json")
        existing_closing = _text(plan_dict.get("closing_contract"))
        changes = [
            item
            for item in (hook_constraint, payoff_guidance)
            if item and item not in existing_closing
        ]
        if changes:
            plan_dict["closing_contract"] = (
                f"{existing_closing}；{'；'.join(changes)}"
                if existing_closing
                else "；".join(changes)
            )
        return ChapterPlan.model_validate(plan_dict)
    except Exception as exc:
        _logger.warning(
            "reading_power_constraint_injection_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return plan


def _causal_link_payload(source: Any) -> dict[str, Any]:
    payload = {
        "previous_event": _text(field(source, "previous_event", "")),
        "causal_mechanism": _text(field(source, "causal_mechanism", "")),
        "unresolved_question": _text(field(source, "unresolved_question", "")),
        "open_threads": _string_list(field(source, "open_threads", [])),
    }
    return {key: item for key, item in payload.items() if item not in ("", [], {})}


def _opening_bridge_payload(bridge: Any) -> dict[str, Any]:
    if not bridge:
        return {}
    causal = _causal_link_payload(field(bridge, "causal_link", None))
    payload = {
        "opening_time": _text(field(bridge, "opening_time", "")),
        "opening_location": _text(field(bridge, "opening_location", "")),
        "opening_pov": _text(field(bridge, "opening_pov", "")),
        "transition_mode": _text(field(bridge, "transition_mode", "")),
        "emotional_carryover": _text(field(bridge, "emotional_carryover", "")),
        "action_handoff": _text(field(bridge, "action_handoff", "")),
        "bridge_summary": _text(field(bridge, "bridge_summary", "")),
        "pending_questions": _string_list(field(bridge, "pending_questions", [])),
        "sensory_anchors": _string_list(field(bridge, "sensory_anchors", [])),
        "opening_acceptance_criteria": _string_list(
            field(bridge, "opening_acceptance_criteria", [])
        ),
        "causal_link": causal,
    }
    return {key: item for key, item in payload.items() if item not in ("", [], {})}


def _opening_contract_from_bridge(opening_bridge: dict[str, Any]) -> str:
    parts = [
        _text(opening_bridge.get("transition_mode")),
        _text(opening_bridge.get("action_handoff")),
        _text(opening_bridge.get("emotional_carryover")),
    ]
    criteria = _string_list(opening_bridge.get("opening_acceptance_criteria", []))
    if criteria:
        parts.append("；".join(criteria))
    return "；".join(part for part in parts if part)


def absorb_bridge_into_plan(plan: Any, bridge: Any) -> ChapterPlan:
    """Copy executable bridge opening anchors into ChapterPlan.

    Bridge remains useful for lineage and auditing, but writing stages should
    read the accepted opening handoff from the plan so DRAFT has one execution
    source for scene intent plus opening constraints.
    """

    if isinstance(plan, ChapterPlan):
        typed_plan = materialize_scene_event_ownership(plan)
    else:
        typed_plan = materialize_scene_event_ownership(
            ChapterPlan.model_validate(
                plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
            )
        )
    bridge_payload = _opening_bridge_payload(bridge)
    if not bridge_payload:
        return typed_plan

    plan_dict = typed_plan.model_dump(mode="json")
    existing_bridge = plan_dict.get("opening_bridge")
    if not isinstance(existing_bridge, dict):
        existing_bridge = {}
    opening_bridge = {
        **existing_bridge,
        **bridge_payload,
    }
    plan_dict["opening_bridge"] = opening_bridge

    if not _text(plan_dict.get("opening_contract", "")):
        plan_dict["opening_contract"] = _opening_contract_from_bridge(opening_bridge)

    scenes = list(plan_dict.get("scene_intents") or [])
    if scenes and isinstance(scenes[0], dict):
        first = dict(scenes[0])
        if not _text(first.get("location")):
            first["location"] = opening_bridge.get("opening_location", "")
        if not _text(first.get("time_marker")):
            first["time_marker"] = opening_bridge.get("opening_time", "")
        if not _text(first.get("pov_character")):
            first["pov_character"] = opening_bridge.get("opening_pov", "")
        if not _text(first.get("entry_state")):
            first["entry_state"] = (
                opening_bridge.get("action_handoff")
                or opening_bridge.get("emotional_carryover")
                or opening_bridge.get("bridge_summary")
                or ""
            )
        entry_refs = _string_list(first.get("entry_state_refs", []))
        _append_unique(entry_refs, opening_bridge.get("action_handoff", ""))
        _append_unique(entry_refs, opening_bridge.get("emotional_carryover", ""))
        _append_unique(entry_refs, opening_bridge.get("bridge_summary", ""))
        causal = opening_bridge.get("causal_link")
        if isinstance(causal, dict):
            _append_unique(entry_refs, causal.get("previous_event", ""))
            _append_unique(entry_refs, causal.get("unresolved_question", ""))
        first["entry_state_refs"] = entry_refs
        scenes[0] = first
        plan_dict["scene_intents"] = scenes

    return materialize_scene_event_ownership(ChapterPlan.model_validate(plan_dict))


def _apply_macro_guard_guidance(
    *,
    packet: Any,
    reading_power_hint: dict[str, Any] | None,
    hint_payload: dict[str, Any] | None,
    alert_payload: dict[str, Any] | None,
    auto_apply_hint: bool,
) -> dict[str, Any] | None:
    """Merge persisted MacroGuard outputs into planning inputs.

    warning-level results stay as soft reading-power suggestions, while
    alert/critical results are elevated into explicit plan constraints.
    """

    merged_hint = reading_power_hint
    if hint_payload and auto_apply_hint:
        if merged_hint is None:
            merged_hint = {}
        mg_reasoning = str(hint_payload.get("reasoning", "")).strip()
        if mg_reasoning:
            suggestions = list(merged_hint.get("prev_chapter_suggestions", []) or [])
            suggestions.append(f"[宏观护栏·轨迹修正] {mg_reasoning}")
            merged_hint["prev_chapter_suggestions"] = suggestions

    if alert_payload:
        action = str(alert_payload.get("action", "")).strip()
        reasoning = str(alert_payload.get("reasoning", "")).strip()
        adjustment_plan = alert_payload.get("adjustment_plan") or {}
        constraints = list(getattr(packet, "guard_constraints", []) or [])
        if reasoning:
            prefix = "宏观护栏强制纠偏"
            if action == "critical_rollback":
                prefix = "宏观护栏紧急纠偏"
            constraint = f"{prefix}：{reasoning}"
            if constraint not in constraints:
                constraints.append(constraint)
        if isinstance(adjustment_plan, dict):
            for key in ("focus_shift", "must_preserve", "must_reduce", "next_arc_target"):
                value = str(adjustment_plan.get(key, "")).strip()
                if not value:
                    continue
                constraint = f"宏观护栏执行项[{key}]：{value}"
                if constraint not in constraints:
                    constraints.append(constraint)
        if constraints:
            packet.guard_constraints = constraints

    return merged_hint


async def _collect_generation_memory_hints(
    runner: Any,
    bundle: Any,
    chapter_number: int,
) -> dict[str, Any]:
    """Collect memory hints for bridge/plan generation."""
    return await collect_planning_memory_hints(runner, bundle, chapter_number)


async def _judge_location_transition_with_llm(
    runner: Any,
    packet: Any,
    bridge: Any,
    plan: ChapterPlan,
    chapter_number: int,
) -> dict[str, Any] | None:
    """Ask the semantic verifier whether Bridge/Plan explains a location change."""

    context = build_location_transition_semantic_check(
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
    )
    if context is None:
        return None

    try:
        request = runner._builder.build(
            TaskType.REPAIR_SEMANTIC_VERIFY,
            context,
            max_tokens=route_max_output_budget(
                runner._router,
                TaskType.REPAIR_SEMANTIC_VERIFY,
                min_tokens=800,
                max_cap=1200,
            ),
            temperature=0.0,
        )
        judgment = await llm_h.route_json_object_with_retry(
            runner._router,
            request,
            task_type=TaskType.REPAIR_SEMANTIC_VERIFY,
            context=context,
            required_keys=("issue_resolved", "confidence", "reasoning"),
            retry_temperature=0.0,
        )
        runner._on_step(
            "upstream_compass_location_judgment",
            {
                "chapter": chapter_number,
                "issue_resolved": bool(judgment.get("issue_resolved")),
                "confidence": judgment.get("confidence"),
                "reasoning": str(judgment.get("reasoning", ""))[:500],
                "metadata": context.get("metadata", {}),
            },
        )
        return judgment
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "upstream_compass_location_judgment_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        runner._on_step(
            "upstream_compass_location_judgment_failed",
            {
                "chapter": chapter_number,
                "error": str(exc),
                "metadata": context.get("metadata", {}),
            },
        )
        return None


async def _run_plan_semantic_consistency(
    runner: Any,
    bundle: Any,
    plan: ChapterPlan,
    chapter_number: int,
    *,
    raise_on_block: bool = True,
) -> dict[str, Any] | None:
    """Compare a generated Plan with the already compiled authoritative sources.

    This deliberately reuses the init coherence compiler.  The Plan is an
    ephemeral candidate here: it may be replanned by the existing bounded
    chapter loop, but it must never create a source RepairCase or publish a
    source mutation.
    """

    settings = runner._settings
    if not bool(getattr(settings, "long_semantic_plan_check_enabled", True)):
        return None

    from novel_forge.core.semantic_consistency import semantic_payload_hash
    from novel_forge.persistence.authoring_store import AuthoringStore
    from novel_forge.persistence.repair_shadow import RepairShadowLedger, RepairShadowPolicy
    from novel_forge.persistence.semantic_consistency import semantic_consistency_view
    from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
        load_reusable_init_coherence_profile,
        run_init_coherence_v2_gate,
        semantic_compiler_runtime_fingerprint,
    )
    from novel_forge.pipeline.long.services.upstream_compass import (
        find_outline_plan_duration_conflicts,
    )

    policy = AuthoringStore(bundle.layout.root).policy()
    if policy is None:
        return None

    compiler_context = SimpleNamespace(
        storage=runner._storage,
        layout=bundle.layout,
        settings=settings,
        router=runner._router,
        builder=runner._builder,
        memory_context=getattr(runner, "memory_context", None),
        on_step=runner._on_step,
        call_with_retry=getattr(runner, "_call_with_retry", None),
        _semantic_repair_cases_disabled=True,
    )
    source_status = semantic_consistency_view(
        runner._storage,
        bundle.layout,
        expected_runtime_fingerprint=semantic_compiler_runtime_fingerprint(compiler_context),
    )
    if source_status.status != "clean":
        runner._on_step(
            "semantic_plan_check_skipped",
            {
                "chapter": chapter_number,
                "source_status": source_status.status,
                "reason": source_status.reason,
            },
        )
        return None

    profile = load_reusable_init_coherence_profile(compiler_context, require_refined=True)
    source_slice = getattr(bundle, "chapter_source_slice", None)
    source_payload = getattr(source_slice, "payload", None)
    if not isinstance(profile, dict) or not isinstance(source_payload, dict):
        runner._on_step(
            "semantic_plan_check_skipped",
            {
                "chapter": chapter_number,
                "source_status": source_status.status,
                "reason": "missing_profile_or_source_slice",
            },
        )
        return None

    shadow_only = not bool(
        getattr(settings, "long_semantic_consistency_blocking", False)
    )
    shadow_ledger: RepairShadowLedger | None = None
    shadow_logical_id = ""
    plan_hash = semantic_payload_hash(plan.model_dump(mode="json"))
    evaluation_source_hash = semantic_payload_hash(
        {
            "source_fingerprint": source_status.source_fingerprint,
            "chapter": chapter_number,
            "plan_hash": plan_hash,
        }
    )
    legacy_fallback_verdict = (
        "conflict"
        if find_outline_plan_duration_conflicts(
            outline=dict(source_payload.get("chapter_outline") or {}),
            plan=plan.model_dump(mode="json"),
        )
        else "no_conflict"
    )
    main_evaluation_id = (
        f"main-plan:{source_status.source_fingerprint}:{chapter_number}:{plan_hash}"
    )
    if shadow_only:
        project_id = str(getattr(bundle, "project_id", "") or bundle.layout.root.name)
        shadow_ledger = RepairShadowLedger(bundle.layout.root)
        admission = shadow_ledger.admit(
            project_id=project_id,
            case_id=(
                f"semantic-plan:{source_status.source_fingerprint}:"
                f"{chapter_number}:{plan_hash}"
            ),
            candidate_version=policy.version,
            policy=RepairShadowPolicy.from_settings(settings),
        )
        if not admission.admitted:
            runner._on_step(
                "semantic_plan_shadow_skipped",
                {
                    "chapter": chapter_number,
                    "reason": admission.reason,
                    "sample_value": admission.sample_value,
                    "shadow_only": True,
                },
            )
            return None
        shadow_logical_id = admission.logical_id

    # The shared compiler may reconcile ordinary RepairCases for a blocking
    # run.  A sampled R7a shadow is evidence-only: it may update reusable claim
    # evidence/reports, but must never create or transition a product case.
    compiler_context._semantic_repair_cases_disabled = shadow_only

    try:
        report = await run_init_coherence_v2_gate(
            compiler_context,
            stage=f"chapter_plan_coherence_{chapter_number:03d}",
            repair_artifact="chapter_plan",
            profile=profile,
            artifacts={
                "chapter_outline": dict(source_payload.get("chapter_outline") or {}),
                "chapter_contract": dict(source_payload.get("chapter_contract") or {}),
                "story_foundation": dict(source_payload.get("story_foundation") or {}),
                "narrative_contract": dict(source_payload.get("narrative_contract") or {}),
                "relevant_entities": {
                    "items": list(source_payload.get("relevant_entities") or [])
                },
                "forbidden_reveal_boundaries": {
                    "items": list(source_payload.get("forbidden_reveal_boundaries") or [])
                },
                "chapter_plan": plan.model_dump(mode="json"),
            },
            focus_chapters=[chapter_number],
        )
    except Exception as exc:  # noqa: BLE001 - shadow errors must not block the chapter
        if shadow_ledger is not None and shadow_logical_id:
            shadow_ledger.record_semantic_judgment(
                shadow_logical_id,
                source_hash=evaluation_source_hash,
                verdict="failed",
                structured_success=False,
                routed_to_human=False,
                model_call_id=shadow_logical_id,
                legacy_fallback_verdict=legacy_fallback_verdict,
            )
            runner._on_step(
                "semantic_plan_shadow_failed",
                {
                    "chapter": chapter_number,
                    "error": str(exc),
                    "shadow_only": True,
                },
            )
            return None
        RepairShadowLedger(bundle.layout.root).record_semantic_main_gate_judgment(
            main_evaluation_id,
            source_hash=evaluation_source_hash,
            verdict="failed",
            structured_success=False,
            routed_to_human=False,
            model_call_id=main_evaluation_id,
            legacy_fallback_verdict=legacy_fallback_verdict,
        )
        raise
    verdict = str(report.get("verdict") or "ambiguous")
    blocked = bool(report.get("blocked")) or verdict in {
        "needs_repair",
        "reject",
        "ambiguous",
        "defer",
    }
    normalized_verdict = verdict if verdict in {
        "accept",
        "needs_repair",
        "reject",
        "ambiguous",
        "defer",
    } else "failed"
    if shadow_ledger is not None and shadow_logical_id:
        shadow_ledger.record_semantic_judgment(
            shadow_logical_id,
            source_hash=evaluation_source_hash,
            verdict=normalized_verdict,
            structured_success=normalized_verdict != "failed",
            routed_to_human=normalized_verdict in {"ambiguous", "defer"},
            model_call_id=shadow_logical_id,
            report_id=semantic_payload_hash(report),
            legacy_fallback_verdict=legacy_fallback_verdict,
        )
    elif not shadow_only:
        RepairShadowLedger(bundle.layout.root).record_semantic_main_gate_judgment(
            main_evaluation_id,
            source_hash=evaluation_source_hash,
            verdict=normalized_verdict,
            structured_success=normalized_verdict != "failed",
            routed_to_human=normalized_verdict in {"ambiguous", "defer"},
            model_call_id=main_evaluation_id,
            report_id=semantic_payload_hash(report),
            legacy_fallback_verdict=legacy_fallback_verdict,
        )
    runner._on_step(
        "semantic_plan_check",
        {
            "chapter": chapter_number,
            "verdict": verdict,
            "blocked": blocked,
            "issue_count": len(report.get("issues") or []),
            "shadow_only": shadow_only,
        },
    )
    if (
        blocked
        and raise_on_block
        and bool(getattr(settings, "long_semantic_consistency_blocking", False))
    ):
        summary = str(report.get("summary") or "Plan 与权威来源的语义一致性未通过。")
        raise ConsistencyViolationError(
            [summary],
            violation_kind="upstream_plan_conflict",
            failed_stage="semantic_plan_check",
            replan_target=RecoveryTarget.PLAN,
        )
    return report


async def generate_bridge_and_plan(
    runner: Any,
    bundle: Any,
    packet: Any,
    chapter_number: int,
    trace: Any,
    *,
    window_manager: Any | None = None,
    window_config: Any | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any] | None]:
    """Generate chapter bridge and plan, persisting both to storage.

    Args:
        runner: ChapterExecutionContext providing storage, router, builder, settings, etc.
        bundle: LongProjectBundle with layout and story context.
        packet: ChapterStatePacket with current chapter state.
        chapter_number: Chapter index being processed.
        trace: PipelineTrace for observability.
        window_manager: Optional ReadingPowerTimelineWindowManager for suspense/hook constraints.
        window_config: Optional ReadingPowerWindowConfig for window parameters.

    Returns:
        Tuple of (bridge, plan, memory_hints).
    """
    from novel_forge.pipeline.long.helpers import load_prev_known_issues
    from novel_forge.pipeline.steps.bridge_step import BridgeInput, BridgeStep
    from novel_forge.pipeline.steps.plan_step import (
        PlanChapterStep,
        PlanInput,
        build_plan_prompt_contexts,
    )

    kernel_composer = await load_story_kernel_composer(runner, bundle)
    bridge_kernel_context = (
        kernel_composer.compose_bridge_input(chapter_number) if kernel_composer is not None else {}
    )
    plan_kernel_context = (
        kernel_composer.compose_plan_input(chapter_number) if kernel_composer is not None else {}
    )

    # ── Build reading power hint from window manager (if available) ─────────
    reading_power_hint: dict[str, Any] | None = None
    if window_manager is not None:
        try:
            reading_power_hint = window_manager.build_reading_power_hint(
                current_chapter=chapter_number,
                chapter_outline=bundle.chapter_outline,
            )
        except Exception as exc:
            _logger.warning(
                "reading_power_hint_build_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    # ── Load MacroGuard outputs for this chapter planning window ───────────
    macro_guard_hint: dict[str, Any] | None = None
    macro_guard_alert: dict[str, Any] | None = None
    try:
        macro_replan_marker_path = (
            bundle.layout.root / "states" / f"macro_guard_replan_required_ch{chapter_number}.json"
        )
        if runner._storage.exists(macro_replan_marker_path):
            marker_payload = runner._storage.load_json(macro_replan_marker_path)
            if bool(getattr(runner._settings, "long_macro_guard_critical_blocks_archive", True)):
                runner._on_step(
                    "macro_guard_replan_required",
                    {
                        "chapter": chapter_number,
                        "source_chapter": marker_payload.get("source_chapter"),
                        "drift_score": marker_payload.get("drift_score"),
                        "marker_path": str(macro_replan_marker_path),
                    },
                )
                raise ConsistencyViolationError(
                    [
                        "宏观护栏触发 critical_rollback，已阻断当前章节规划；"
                        "请先重规划或人工确认后再继续。"
                    ],
                    replan_target=RecoveryTarget.PLAN,
                )
            runner._on_step(
                "macro_guard_replan_marker_downgraded",
                {
                    "chapter": chapter_number,
                    "source_chapter": marker_payload.get("source_chapter"),
                    "reason": "long_macro_guard_critical_blocks_archive_disabled",
                },
            )
        macro_hint_path = (
            bundle.layout.root / "states" / f"macro_guard_hint_ch{chapter_number}.json"
        )
        if runner._storage.exists(macro_hint_path):
            macro_guard_hint = runner._storage.load_json(macro_hint_path)
        if chapter_number > 1:
            macro_alert_path = (
                bundle.layout.root / "states" / f"macro_guard_alert_ch{chapter_number - 1}.json"
            )
            if runner._storage.exists(macro_alert_path):
                macro_guard_alert = runner._storage.load_json(macro_alert_path)
        reading_power_hint = _apply_macro_guard_guidance(
            packet=packet,
            reading_power_hint=reading_power_hint,
            hint_payload=macro_guard_hint,
            alert_payload=macro_guard_alert,
            auto_apply_hint=getattr(runner._settings, "long_macro_guard_auto_apply_hint", True),
        )
    except ConsistencyViolationError:
        raise
    except Exception as exc:
        _logger.warning(
            "macro_guard_output_load_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )

    memory_hints = await _collect_generation_memory_hints(
        runner,
        bundle,
        chapter_number,
    )
    planning_memory_diagnostics = memory_hints.get("memory_diagnostics")
    if isinstance(planning_memory_diagnostics, dict) and planning_memory_diagnostics:
        persist_stage_memory_diagnostics_report(
            runner._storage,
            bundle.layout,
            chapter_number,
            planning_memory_diagnostics,
        )
        runner._on_step("memory_planning_context", planning_memory_diagnostics)

    bridge_step = BridgeStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=runner._on_step,
    )
    bridge = await bridge_step.run(
        BridgeInput(
            chapter_state_packet=packet,
            story_kernel_context=bridge_kernel_context,
            genre=getattr(bundle.story_bible, "genre", ""),
            tone=getattr(bundle.story_bible, "tone", ""),
            memory_hints=memory_hints,
            style_profile=getattr(bundle, "style_profile", None),
            narrative_contract=getattr(bundle, "narrative_contract", None),
            pov_hint=getattr(bundle, "pov_hint", ""),
            reading_power_hint=reading_power_hint,
            story_bible=dump_story_bible_for_prompt(bundle.story_bible),
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
    )
    packet.bridge = bridge
    coherence_warnings = getattr(bridge_step, "coherence_issues", []) or []
    if coherence_warnings:
        runner._on_step(
            "bridge_coherence_warning",
            {
                "chapter": chapter_number,
                "issues": coherence_warnings,
            },
        )
    # ── Force-resolve suspense from window manager into bridge.pending_questions ──
    if window_manager is not None and reading_power_hint is not None:
        try:
            forced_questions = reading_power_hint.get("forced_pending_questions", [])
            if forced_questions:
                existing = list(bridge.pending_questions or [])
                merged = list(existing)
                for q in forced_questions:
                    qs = str(q).strip()
                    if qs and qs not in merged:
                        merged.append(qs)
                bridge.pending_questions = merged
        except Exception as exc:
            _logger.warning(
                "reading_power_force_resolve_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
    persist_bridge(runner._storage, bundle.layout, chapter_number, bridge)
    bridge_artifact = persist_stage_artifact(
        storage=runner._storage,
        layout=bundle.layout,
        project_id=getattr(bundle, "project_id", "") or "unknown",
        chapter_number=chapter_number,
        artifact_type="bridge",
        payload=cast(
            dict[str, Any],
            bridge.model_dump(mode="json") if hasattr(bridge, "model_dump") else bridge,
        ),
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
    )
    runner._on_step("bridge", bridge)
    persist_chapter_state_packet(runner._storage, bundle.layout, chapter_number, packet)

    plan_step = PlanChapterStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=runner._on_step,
    )
    # Load previous-draft known issues (causal/continuity/repair reports on disk)
    # so the plan avoids structural decisions that led to the same problems last time.
    prev_known_issues = load_prev_known_issues(runner, bundle, chapter_number)
    extension_ids: list[str] = []
    element_cards_by_id: dict[str, dict[str, Any]] = {}
    if (
        getattr(bundle, "blueprint", None)
        and getattr(bundle.blueprint, "element_selection", None)
        and getattr(bundle.blueprint.element_selection, "extension_elements", None)
    ):
        for item in list(bundle.blueprint.element_selection.extension_elements or []):
            element_id = str(getattr(item, "element_id", "") or "").strip()
            if not element_id:
                continue
            extension_ids.append(element_id)
            if hasattr(item, "model_dump"):
                element_cards_by_id[element_id] = item.model_dump(mode="json")
            elif isinstance(item, dict):
                element_cards_by_id[element_id] = dict(item)
            else:
                element_cards_by_id[element_id] = {"element_id": element_id}
    planning_element_hint = build_planning_hint(
        runner._storage,
        bundle.layout,
        chapter_number=chapter_number,
        lookback_chapters=getattr(runner._settings, "long_element_progress_lookback", 0),
        extension_ids=extension_ids or None,
    )
    schedule_hint = build_element_schedule_hint(
        runner._storage,
        bundle.layout,
        chapter_number=chapter_number,
        extension_ids=extension_ids,
        cards_by_id=element_cards_by_id,
        max_items=getattr(runner._settings, "long_element_schedule_max_mandated", 3),
    )
    planning_element_hint = merge_schedule_hint_into_progress_hint(
        planning_element_hint,
        schedule_hint,
    )
    dynamic_focus_ids = suggest_dynamic_focus_ids(
        chapter_outline=bundle.chapter_outline,
        extension_ids=extension_ids,
        planning_hint=planning_element_hint,
    )
    # ── Build time context for planning prompt injection ──────────────────
    prev_outline: ChapterOutline | None = None
    if chapter_number > 1:
        outline = getattr(bundle, "outline", None)
        if outline is not None:
            for ch in getattr(outline, "chapters", []):
                if getattr(ch, "chapter_number", 0) == chapter_number - 1:
                    prev_outline = ch
                    break
    time_context = build_time_context_for_planning(bundle.chapter_outline, prev_outline)

    # ── Build strand weave hints for planning prompt injection ────────────
    strand_hint: dict[str, Any] | None = None
    try:
        strand_path = bundle.layout.root / "states" / "strand_tracker.json"
        strand_raw = runner._storage.load_json(strand_path) if strand_path.exists() else {}
        if strand_raw:
            strand_tracker = StrandTracker.model_validate(strand_raw)
            _strand_config = None
            _style_profile = getattr(bundle, "style_profile", None)
            _profile_strand_config = get_profile_section(_style_profile, "strand_config")
            if _profile_strand_config is not None:
                _strand_config = build_strand_config_from_profile(_profile_strand_config)
            strand_hint = build_strand_hint_for_planning(
                strand_tracker, chapter_number, config=_strand_config
            )
    except Exception as exc:
        _logger.warning("strand_hint_load_failed | chapter=%d | error=%s", chapter_number, exc)

    plan_task_type = (
        TaskType.PLAN_CHAPTER_SCENES
        if getattr(runner._config, "writing_mode", "whole_chapter") == "scene_level"
        else TaskType.PLAN_CHAPTER
    )
    chapter_position = build_chapter_position(getattr(bundle, "outline", None), chapter_number)

    # ── Build lightweight theme + arc projection context ─────────────────
    from novel_forge.pipeline.long.services.theme_arc_projection import (
        build_theme_arc_context,
    )

    _blueprint = getattr(bundle, "blueprint", None)
    _story_bible = getattr(bundle, "story_bible", None)
    _themes = list(getattr(_story_bible, "themes", []) or []) if _story_bible else []
    _char_arcs = list(getattr(_blueprint, "character_arcs", []) or []) if _blueprint else []
    _narrative_phases = (
        list(getattr(_blueprint, "narrative_phases", []) or []) if _blueprint else []
    )
    _total_chapters = 0
    _outline = getattr(bundle, "outline", None)
    if _outline is not None:
        _chapters_list = list(getattr(_outline, "chapters", []) or [])
        _total_chapters = len(_chapters_list)
    theme_arc_context = (
        build_theme_arc_context(
            themes=_themes,
            character_arcs=_char_arcs,
            chapter_number=chapter_number,
            total_chapters=_total_chapters,
            narrative_phases=_narrative_phases,
        )
        if (_themes or _char_arcs)
        else None
    )

    plan_input_kwargs: dict[str, Any] = {
        "chapter_state_packet": packet,
        "memory_hints": memory_hints,
        "pov_hint": getattr(bundle, "pov_hint", ""),
        "style_profile": getattr(bundle, "style_profile", None),
        "editorial_contract": (
            bundle.editorial_contract.model_dump(mode="json")
            if getattr(bundle, "editorial_contract", None) is not None
            else None
        ),
        "editorial_readiness": getattr(bundle, "editorial_readiness", None),
        "known_issues_to_avoid": prev_known_issues or None,
        "narrative_contract": getattr(bundle, "narrative_contract", None),
        "element_selection": (
            bundle.blueprint.element_selection.model_dump(mode="json")
            if getattr(bundle, "blueprint", None)
            and getattr(bundle.blueprint, "element_selection", None)
            else None
        ),
        "element_progress_hint": planning_element_hint,
        "dynamic_element_focus": dynamic_focus_ids or None,
        "time_context": time_context,
        "strand_hint": strand_hint,
        "reading_power_hint": reading_power_hint,
        "story_bible": dump_story_bible_for_prompt(bundle.story_bible),
        "chapter_source_slice": getattr(bundle, "chapter_source_slice", None),
        "chapter_position": chapter_position,
        "task_type": plan_task_type,
        "theme_arc_context": theme_arc_context,
    }
    # Carry forward any transient replan context attached to the bundle.
    _bundle_replan_ctx = getattr(bundle, "replan_context", None)
    if _bundle_replan_ctx is not None:
        plan_input_kwargs["replan_context"] = _bundle_replan_ctx
    plan = await plan_step.run(
        PlanInput.from_kernel_context(
            bundle.chapter_outline,
            plan_kernel_context,
            **plan_input_kwargs,
        )
    )
    # Use the same helper after the one permitted whole-chapter replan so a
    # structural repair cannot discard existing hook/payoff guidance.
    plan = _inject_reading_power_closing_contract(
        plan,
        reading_power_hint if window_manager is not None else None,
        chapter_number=chapter_number,
    )
    plan = absorb_bridge_into_plan(plan, bridge)
    scene_plan_validation_report: dict[str, Any] | None = None
    writing_mode = getattr(runner._config, "writing_mode", "whole_chapter")
    if writing_mode == "whole_chapter":
        # Whole-chapter prose still depends on scene_intents.  Apply the same
        # deterministic structure check, but keep its artifact independent of
        # scene-mode validation so it cannot block the whole-chapter workflow.
        # Only objective high/critical defects trigger one constrained replan;
        # valid plans retain their current quality, prompt, and latency.
        from novel_forge.pipeline.long.services.generation.scene_writing import (
            plan_structure_audit_path,
            validate_scene_plan_locally,
            validation_issues_for_replan,
        )

        structure_audit = validate_scene_plan_locally(
            plan=plan,
            bridge=bridge,
            target_word_count=int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
        )
        structure_audit["mode"] = "whole_chapter"
        structure_audit["replanned"] = False
        if not bool(structure_audit.get("valid")):
            plan_input_kwargs["scene_plan_validation_issues"] = validation_issues_for_replan(
                structure_audit
            )
            plan = await plan_step.run(
                PlanInput.from_kernel_context(
                    bundle.chapter_outline,
                    plan_kernel_context,
                    **plan_input_kwargs,
                )
            )
            plan = _inject_reading_power_closing_contract(
                plan,
                reading_power_hint if window_manager is not None else None,
                chapter_number=chapter_number,
            )
            plan = absorb_bridge_into_plan(plan, bridge)
            structure_audit = validate_scene_plan_locally(
                plan=plan,
                bridge=bridge,
                target_word_count=int(
                    getattr(bundle.chapter_outline, "expected_word_count", 0) or 0
                ),
            )
            structure_audit["mode"] = "whole_chapter"
            structure_audit["replanned"] = True
        runner._storage.save_json(
            plan_structure_audit_path(bundle.layout, chapter_number),
            structure_audit,
        )
        runner._on_step("plan_structure_audit", structure_audit)
    if writing_mode == "scene_level":
        from novel_forge.pipeline.long.services.generation.scene_writing import (
            scene_plan_path,
            scene_plan_report_path,
            validate_scene_plan_with_llm,
            validation_issues_for_replan,
        )

        for validation_attempt in range(1, 3):
            runner._storage.save_json(
                scene_plan_path(bundle.layout, chapter_number),
                plan.model_dump(mode="json"),
            )
            scene_plan_validation_report = await validate_scene_plan_with_llm(
                runner=runner,
                bundle=bundle,
                bridge=bridge,
                plan=plan,
                chapter_number=chapter_number,
            )
            runner._storage.save_json(
                scene_plan_report_path(bundle.layout, chapter_number),
                scene_plan_validation_report,
            )
            runner._on_step("scene_plan_validation", scene_plan_validation_report)
            if bool(scene_plan_validation_report.get("valid")):
                break
            if validation_attempt >= 2:
                break
            repairable_codes = {"duplicate_scene_id", "invalid_dependency", "dependency_cycle"}
            repairable_scene_issues = [
                issue
                for issue in list(scene_plan_validation_report.get("issues") or [])
                if isinstance(issue, dict)
                and str(issue.get("code") or "") in repairable_codes
                and list(issue.get("scene_ids") or [])
            ]
            if repairable_scene_issues:
                from novel_forge.pipeline.long.services.init_repair import (
                    InitRepairContext,
                    InitRepairOrchestrator,
                )
                from novel_forge.pipeline.long.services.scene_plan_repair import (
                    ScenePlanRepairPolicy,
                )

                repair_outcome = await InitRepairOrchestrator(ScenePlanRepairPolicy()).repair(
                    plan,
                    InitRepairContext(
                        service_ctx=runner,
                        outline_ctx={},
                        total_chapters=int(getattr(bundle.outline, "total_chapters", 0) or 0),
                        artifacts={
                            "bridge": bridge,
                            "chapter_number": chapter_number,
                            "chapter_outline": bundle.chapter_outline,
                            "target_word_count": int(
                                getattr(bundle.chapter_outline, "expected_word_count", 0) or 0
                            ),
                            "scene_plan_validation_report": scene_plan_validation_report,
                        },
                    ),
                )
                runner._on_step(
                    "scene_plan_repair",
                    {
                        "chapter": chapter_number,
                        "repaired": repair_outcome.repaired,
                        "valid": repair_outcome.report.is_valid,
                        "attempts": repair_outcome.attempts_as_dicts(),
                    },
                )
                if repair_outcome.report.is_valid:
                    plan = absorb_bridge_into_plan(repair_outcome.payload, bridge)
                    continue
            plan_input_kwargs["scene_plan_validation_issues"] = validation_issues_for_replan(
                scene_plan_validation_report
            )
            plan = await plan_step.run(
                PlanInput.from_kernel_context(
                    bundle.chapter_outline,
                    plan_kernel_context,
                    **plan_input_kwargs,
                )
            )
            plan = absorb_bridge_into_plan(plan, bridge)
    if getattr(runner._settings, "long_retrieval_eval_point_a_enabled", False):
        try:
            from novel_forge.eval.retrieval_eval import (
                evaluate_retrieval_against_plan,
                persist_retrieval_eval_report,
            )

            retrieval_plan_input = PlanInput.from_kernel_context(
                bundle.chapter_outline,
                plan_kernel_context,
                **plan_input_kwargs,
            )
            prompt_contexts = build_plan_prompt_contexts(retrieval_plan_input, runner._settings)
            max_scenes = getattr(runner._settings, "long_retrieval_eval_max_scenes", None)
            report = evaluate_retrieval_against_plan(
                plan=plan,
                chapter_outline=bundle.chapter_outline,
                plan_canon_context=prompt_contexts.plan_canon_context,
                plan_memory_hints=prompt_contexts.plan_memory_hints,
                chapter_number=chapter_number,
                max_scenes=max_scenes,
                config_snapshot={
                    "tokenizer": "jieba",
                    "max_scenes": max_scenes,
                    "retrieval_scope": "plan_prompt_context",
                },
            )
            payload = report.to_payload().model_dump(mode="json")
            if getattr(runner._settings, "long_retrieval_eval_persist_report", True):
                persist_retrieval_eval_report(
                    runner._storage,
                    bundle.layout,
                    chapter_number,
                    report,
                )
            runner._on_step("retrieval_eval_planning", payload)
        except Exception as exc:
            _logger.warning(
                "retrieval_eval_planning_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
    # World-rule coverage is a Plan-owned gate: repair the plan before prose is
    # allowed to see it, never paper over a missing source rule in Draft.
    from novel_forge.pipeline.long.services.constraints.world_rule_plan_contract import (
        materialize_plan_world_rule_bindings,
        validate_plan_world_rule_coverage,
    )
    from novel_forge.pipeline.long.services.context.source_artifacts import (
        project_stage_source_cards,
    )

    plan = ChapterPlan.model_validate(materialize_plan_world_rule_bindings(plan))
    source_slice = getattr(bundle, "chapter_source_slice", None)
    source_cards = project_stage_source_cards(source_slice, stage="plan") if source_slice else {}
    world_rule_card = source_cards.get("world_rule_card")
    # Direct unit-level stage invocation can intentionally omit source artifacts;
    # preflight blocks that shape in real chapter runs.
    world_rule_issues = (
        validate_plan_world_rule_coverage(plan, world_rule_card) if source_slice else []
    )
    if world_rule_issues:
        plan_input_kwargs["world_rule_validation_issues"] = world_rule_issues
        plan = await plan_step.run(
            PlanInput.from_kernel_context(
                bundle.chapter_outline,
                plan_kernel_context,
                **plan_input_kwargs,
            )
        )
        plan = _inject_reading_power_closing_contract(
            plan,
            reading_power_hint if window_manager is not None else None,
            chapter_number=chapter_number,
        )
        plan = absorb_bridge_into_plan(plan, bridge)
        plan = ChapterPlan.model_validate(materialize_plan_world_rule_bindings(plan))
        world_rule_issues = validate_plan_world_rule_coverage(plan, world_rule_card)
    runner._on_step(
        "world_rule_plan_coverage",
        {
            "chapter": chapter_number,
            "rule_book_hash": (world_rule_card or {}).get("source_hash", ""),
            "issues": world_rule_issues,
            "valid": not world_rule_issues,
        },
    )
    if world_rule_issues:
        raise ConsistencyViolationError(
            ["世界规则未被章节计划充分吸收：" + "；".join(world_rule_issues[:5])],
            replan_target=RecoveryTarget.PLAN,
        )

    semantic_report = await _run_plan_semantic_consistency(
        runner,
        bundle,
        plan,
        chapter_number,
        raise_on_block=False,
    )
    if (
        semantic_report is not None
        and (
            bool(semantic_report.get("blocked"))
            or str(semantic_report.get("verdict") or "")
            in {"needs_repair", "reject", "ambiguous", "defer"}
        )
        and bool(getattr(runner._settings, "long_semantic_consistency_blocking", False))
    ):
        semantic_issues = [
            _text(
                item.get("summary")
                or item.get("description")
                or item.get("reason")
                or item.get("issue_type")
            )
            for item in semantic_report.get("issues", []) or []
            if isinstance(item, dict)
        ]
        if not any(semantic_issues):
            semantic_issues = [_text(semantic_report.get("summary"))]
        plan_input_kwargs["semantic_consistency_issues"] = [
            item for item in semantic_issues if item
        ][:12]
        plan = await plan_step.run(
            PlanInput.from_kernel_context(
                bundle.chapter_outline,
                plan_kernel_context,
                **plan_input_kwargs,
            )
        )
        plan = _inject_reading_power_closing_contract(
            plan,
            reading_power_hint if window_manager is not None else None,
            chapter_number=chapter_number,
        )
        plan = absorb_bridge_into_plan(plan, bridge)
        plan = ChapterPlan.model_validate(materialize_plan_world_rule_bindings(plan))
        world_rule_issues = validate_plan_world_rule_coverage(plan, world_rule_card)
        if world_rule_issues:
            raise ConsistencyViolationError(
                ["语义重规划后世界规则仍未覆盖：" + "；".join(world_rule_issues[:5])],
                replan_target=RecoveryTarget.PLAN,
            )
        await _run_plan_semantic_consistency(runner, bundle, plan, chapter_number)

    if bool(getattr(runner._settings, "long_upstream_compass_enabled", True)):
        location_transition_judgment = await _judge_location_transition_with_llm(
            runner,
            packet,
            bridge,
            plan,
            chapter_number,
        )
        compass_report = audit_upstream_compass(
            packet=packet,
            bridge=bridge,
            plan=plan,
            chapter_number=chapter_number,
            target_word_count=int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
            min_plan_scenes=int(getattr(runner._settings, "long_upstream_compass_min_scenes", 1)),
            word_budget_tolerance=float(
                getattr(runner._settings, "long_upstream_compass_word_budget_tolerance", 0.25)
            ),
            blocking_enabled=bool(
                getattr(runner._settings, "long_upstream_compass_blocking", True)
            ),
            location_transition_judgment=location_transition_judgment,
        )
        compass_payload = compass_report.model_dump()
        try:
            runner._storage.save_json(
                bundle.layout.reports_dir / f"chapter_{chapter_number:03d}_upstream_compass.json",
                compass_payload,
            )
        except OSError as exc:
            _logger.warning(
                "upstream_compass_report_save_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        runner._on_step("upstream_compass", compass_payload)
        if compass_report.blocking:
            messages = upstream_compass_blocking_messages(compass_report) or [
                f"章节 {chapter_number} Bridge/Plan 未通过上游罗盘门。"
            ]
            requires_source_revision = any(
                str((finding.metadata or {}).get("recovery_target")) == "manual"
                for finding in compass_report.findings
            )
            raise ConsistencyViolationError(
                messages,
                violation_kind=(
                    "upstream_source_conflict"
                    if requires_source_revision
                    else "upstream_plan_conflict"
                ),
                failed_stage="upstream_compass",
                replan_target=(
                    RecoveryTarget.MANUAL if requires_source_revision else RecoveryTarget.PLAN
                ),
            )
    plan_findings = audit_plan_contract(
        plan=plan,
        packet=packet,
        bridge=bridge,
        chapter_number=chapter_number,
        target_word_count=int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
        policy=GuidanceAuditPolicy(
            word_budget_tolerance=float(
                getattr(runner._settings, "long_guidance_plan_word_budget_tolerance", 0.20)
            )
        ),
    )
    if plan_findings:
        plan_tickets = compile_guidance_repair_tickets(plan_findings)
        report_payload = {
            "chapter": chapter_number,
            "stage": "plan",
            "findings": [finding.model_dump(mode="json") for finding in plan_findings],
            "repair_tickets": [ticket.model_dump(mode="json") for ticket in plan_tickets],
        }
        try:
            runner._storage.save_json(
                bundle.layout.reports_dir / f"chapter_{chapter_number:03d}_guidance_plan.json",
                report_payload,
            )
        except Exception as exc:
            _logger.warning(
                "guidance_plan_audit_report_save_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        runner._on_step("guidance_plan_audit_block", report_payload)
        messages = guidance_blocking_messages(plan_findings) or [
            f"章节 {chapter_number} plan 未通过本地指导契约审计。"
        ]
        raise ConsistencyViolationError(messages, replan_target=RecoveryTarget.PLAN)
    # Source semantics are inherited, never reclassified by the planning model.
    requirements = (source_cards.get("chapter_contract") or {}).get("guidance_requirements", [])
    if requirements:
        payload = plan.model_dump(mode="json")
        payload["guidance_requirements"] = requirements
        plan = ChapterPlan.model_validate(payload)
    runner._storage.save_json(
        bundle.layout.chapter_plan_path(chapter_number),
        plan.model_dump(mode="json"),
    )
    persist_stage_artifact(
        storage=runner._storage,
        layout=bundle.layout,
        project_id=getattr(bundle, "project_id", "") or "unknown",
        chapter_number=chapter_number,
        artifact_type="plan",
        payload=cast(
            dict[str, Any],
            plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan,
        ),
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        previous_artifact=bridge_artifact,
    )
    outline_focus_ids = [
        str(item).strip()
        for item in list(getattr(bundle.chapter_outline, "element_focus", []) or [])
        if str(item).strip()
    ]
    scheduled_focus_ids = outline_focus_ids or dynamic_focus_ids
    if scheduled_focus_ids:
        scheduled_entry = record_schedule(
            runner._storage,
            bundle.layout,
            chapter_number=chapter_number,
            scheduled_ids=scheduled_focus_ids,
            focus_source="outline" if outline_focus_ids else "dynamic",
        )
        runner._on_step(
            "element_progress_scheduled",
            {
                "chapter": chapter_number,
                "scheduled_element_ids": scheduled_entry.get("scheduled_element_ids", []),
                "focus_source": scheduled_entry.get("focus_source", ""),
            },
        )
    runner._on_step("plan", plan)

    # ── Time-constraint validation ───────────────────────────────────────
    time_report = validate_time_consistency(bundle.chapter_outline, prev_outline)
    if time_report.violations:
        runner._on_step(
            "time_validation",
            {
                "chapter": chapter_number,
                "violations": [
                    {
                        "severity": v.severity,
                        "type": v.violation_type,
                        "message": v.message,
                        "suggestion": v.suggestion,
                    }
                    for v in time_report.violations
                ],
                "time_summary": time_report.time_summary,
            },
        )
        _logger.warning(
            "time_validation | chapter=%d | violations=%d | %s",
            chapter_number,
            len(time_report.violations),
            time_report.time_summary,
        )

    return bridge, plan, memory_hints, scene_plan_validation_report
