"""Causal repair: validation, repair loop, and alignment repair helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, cast

from novel_forge.core.domain.world_context import (
    format_address_rules_for_prompt,
    render_world_context_rules,
)
from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.core.schemas.chapter import CausalValidationReport
from novel_forge.core.schemas.review import CrossDimensionIssueLink
from novel_forge.core.utils.pipeline_helpers import has_prompt_leaks
from novel_forge.core.utils.semantic_drift import detect_drift

# Shared helpers from quality_checks
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.decisions import (
    CausalRegressionAction,
    CausalRegressionContext,
    RepairContext,
    RepairThresholds,
    decide_causal_regression_action,
    decide_repair_strategy,
)
from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairLoopResult,
    RepairRoundContext,
    _RepairRoundKernel,
    resolve_max_issues_per_round,
)
from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep
from novel_forge.pipeline.steps.causal_validation_step import (
    CausalValidationInput,
    CausalValidationStep,
)

_logger = get_logger("pipeline.causal_repair")

_CAUSAL_CRITIQUE_TYPES = frozenset(
    {
        "causal_break",
        "causal_gap",
        "causal_error",
        "causal_contradiction",
        "opening_causal_gap",
        "missing_causal_transition",
        "event_without_cause",
        "unmotivated_decision",
        "question_resolved_too_early",
        "question_ignored",
    }
)


def _compact_causal_issue_text(value: Any) -> str:
    """Normalize issue text enough to match serialized critique entries."""
    return "".join(str(value or "").split()).lower()


def _causal_bigram_similarity(left: str, right: str) -> float:
    """Small CJK-friendly similarity helper for matching causal critique summaries."""
    if not left or not right:
        return 0.0
    left_bigrams = {left[i : i + 2] for i in range(max(0, len(left) - 1))} or {left}
    right_bigrams = {right[i : i + 2] for i in range(max(0, len(right) - 1))} or {right}
    union = left_bigrams | right_bigrams
    return len(left_bigrams & right_bigrams) / len(union) if union else 0.0


def _is_causal_critique_type(value: Any) -> bool:
    text = _compact_causal_issue_text(value)
    return text in _CAUSAL_CRITIQUE_TYPES or "causal" in text or "因果" in text


def _find_current_causal_critique_signature(
    episodic_memory: Any,
    issue: Any,
    *,
    current_chapter: int,
) -> str | None:
    """Find the persisted CriticAgent causal entry matching a causal-validator issue."""
    critique_index = getattr(episodic_memory, "_critique_index", {}) or {}
    issue_type = _compact_causal_issue_text(getattr(issue, "issue_type", ""))
    summary = _compact_causal_issue_text(getattr(issue, "summary", ""))
    if not summary:
        return None

    best_signature: str | None = None
    best_score = 0.0
    for sig, entry in critique_index.items():
        if getattr(entry, "chapter_number", None) != current_chapter:
            continue
        entry_type = _compact_causal_issue_text(getattr(entry, "issue_type", ""))
        if entry_type != issue_type and not _is_causal_critique_type(entry_type):
            continue
        entry_summary = _compact_causal_issue_text(getattr(entry, "summary", ""))
        if not entry_summary:
            continue
        if entry_summary == summary:
            return str(sig)
        if entry_summary in summary or summary in entry_summary:
            score = 0.9
        else:
            score = _causal_bigram_similarity(entry_summary, summary)
        if score > best_score:
            best_score = score
            best_signature = str(sig)

    return best_signature if best_score >= 0.45 else None


def _causal_repair_strategy_label(issues: list[Any]) -> str:
    """Approximate the repair routing mode for memory telemetry."""
    issue_types = {str(getattr(issue, "issue_type", "") or "").strip().lower() for issue in issues}
    if not issue_types:
        return "causal_repair"
    patch_only = {"opening_causal_gap"}
    window_types = {
        "missing_causal_transition",
        "event_without_cause",
        "unmotivated_decision",
        "question_resolved_too_early",
        "question_ignored",
    }
    if issue_types <= patch_only:
        return "patch"
    if issue_types <= window_types:
        return "window"
    return "fulltext"


def _record_causal_repair_memory_result(
    *,
    episodic_memory: Any,
    chapter_number: int,
    round_num: int,
    pre_issues: list[Any],
    ledger: Any,
    score_before: float,
    score_after: float,
    strategy: str,
) -> int:
    """Persist causal repair outcomes back onto matching CriticAgent entries."""
    record_result = getattr(episodic_memory, "record_repair_result", None)
    if not callable(record_result):
        return 0

    if getattr(ledger, "has_regression", False):
        result = "regression"
    elif getattr(ledger, "resolved", None) or getattr(ledger, "downgraded", None):
        result = "success"
    else:
        result = "no_op"

    new_issue_types = [
        str(getattr(issue, "issue_type", "") or "")
        for issue in getattr(ledger, "new_high_critical", []) or []
        if getattr(issue, "issue_type", "")
    ]

    recorded = 0
    seen_signatures: set[str] = set()
    for issue in pre_issues:
        signature = _find_current_causal_critique_signature(
            episodic_memory,
            issue,
            current_chapter=chapter_number,
        )
        if not signature or signature in seen_signatures:
            continue
        if record_result(
            signature,
            chapter=chapter_number,
            round_num=round_num,
            strategy=strategy,
            result=result,
            new_issues=new_issue_types,
            score_before=score_before,
            score_after=score_after,
        ):
            seen_signatures.add(signature)
            recorded += 1
    return recorded


def _build_character_notes_for_pipeline(runner: Any, bundle: Any) -> str:
    """Extract a brief character ability summary for the causal validation prompt."""
    try:
        chars_path = bundle.layout.characters_path
        if not chars_path.exists():
            return ""
        raw = runner._storage.load_json(chars_path)
        chars = raw if isinstance(raw, list) else raw.get("characters", [])
        lines: list[str] = []
        for c in chars:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            backstory = (c.get("backstory") or "").strip()
            role = (c.get("role") or "").strip()
            if name and backstory:
                lines.append(f"- {name}（{role}）：{backstory}")
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_character_profiles_for_repair(packet: Any) -> list[dict[str, Any]]:
    """Extract character profiles suitable for causal repair context."""
    profiles: list[dict[str, Any]] = []

    char_profiles = getattr(packet, "character_profiles", None) or []
    for p in char_profiles:
        if isinstance(p, dict):
            entry: dict[str, Any] = {}
            for key in (
                "name",
                "identity",
                "gender",
                "abilities",
                "goals",
                "personality",
                "social_status",
                "backstory",
            ):
                val = p.get(key, "")
                if val:
                    entry[key] = val
            if entry.get("name"):
                profiles.append(entry)

    if not profiles:
        canon_ctx = getattr(packet, "canon_context", None)
        if canon_ctx is None:
            chars_dict: dict[str, Any] = {}
        elif isinstance(canon_ctx, dict):
            chars_dict = canon_ctx.get("characters", {})
        elif hasattr(canon_ctx, "characters"):
            chars_dict = canon_ctx.characters
        else:
            chars_dict = {}

        for name, state in chars_dict.items():
            if isinstance(state, dict):
                entry = {"name": name}
                if state.get("gender"):
                    entry["gender"] = state["gender"]
                if state.get("location"):
                    entry["identity"] = f"位于{state['location']}"
                profiles.append(entry)

    return profiles


async def run_causal_validation(
    runner: Any,
    bundle: Any,
    bridge: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    previous_chapter_ending: str = "",
    *,
    recheck_mode: bool = False,
    must_resolve_summaries: list[str] | None = None,
    must_resolve_issue_ids: list[str] | None = None,
    recheck_strategy: str = "targeted_with_global_guard",
    prior_issues: list[dict[str, Any]] | None = None,
    repaired_issue_types: list[str] | None = None,
    patch_only_repair: bool = False,
    strict_review: bool = False,
) -> Any:
    """Run local causal-chain validation and persist the report."""
    causal_step = CausalValidationStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    causal_link = getattr(bridge, "causal_link", None)
    if causal_link is None:
        causal_link_payload = {}
    elif hasattr(causal_link, "model_dump"):
        causal_link_payload = causal_link.model_dump(mode="json")
    elif isinstance(causal_link, dict):
        causal_link_payload = causal_link
    else:
        causal_link_payload = {}
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    causal_validate_kernel_context = (
        kernel_composer.compose_causal_validate_input(chapter_number)
        if kernel_composer is not None
        else {}
    )

    report = await causal_step.run(
        CausalValidationInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_bridge=bridge,
            causal_link=causal_link_payload,
            character_notes=_build_character_notes_for_pipeline(runner, bundle),
            previous_chapter_ending=previous_chapter_ending,
            recheck_mode=recheck_mode,
            recheck_strategy=cast(
                Literal["strict_targeted", "targeted_with_global_guard"], recheck_strategy
            ),
            must_resolve_summaries=must_resolve_summaries or [],
            must_resolve_issue_ids=must_resolve_issue_ids or [],
            prior_issues=prior_issues or [],
            repaired_issue_types=repaired_issue_types or [],
            patch_only_repair=patch_only_repair,
            strict_review=strict_review,
            kernel_context=causal_validate_kernel_context,
        )
    )
    causal_payload = report.model_dump(mode="json")
    causal_payload["source_text_hash"] = source_text_hash(current_text)
    runner._storage.save_json(
        bundle.layout.chapter_causal_report_path(chapter_number),
        causal_payload,
    )
    runner._on_step("causal_validation", report)
    return report


async def causal_repair_edit(
    runner: Any,
    *,
    bundle: Any,
    packet: Any,
    bridge: Any,
    current_text: str,
    chapter_number: int,
    causal_report: Any,
    trace: Any,
    repair_round: int = 1,
    prev_round_issues: list[str] | None = None,
    per_issue_rounds: dict[str, int] | None = None,
    must_fix_summaries: list[str] | None = None,
    must_fix_issue_ids: list[str] | None = None,
    memory_guidance: dict[str, Any] | None = None,
) -> str:
    """Thin pipeline wrapper around CausalRepairStep."""
    from novel_forge.pipeline.long.repair_causal import run_causal_repair
    from novel_forge.pipeline.steps.causal_repair_step import (
        CausalRepairInput,
        CausalRepairStep,
    )

    causal_link: dict[str, Any] = {}
    raw_link = getattr(bridge, "causal_link", None)
    if raw_link is not None:
        if hasattr(raw_link, "model_dump"):
            dumped_link = raw_link.model_dump(mode="json")
            causal_link = dict(dumped_link) if isinstance(dumped_link, dict) else {}
        elif isinstance(raw_link, dict):
            causal_link = dict(raw_link)

    char_profiles = _extract_character_profiles_for_repair(packet)
    prev_ending = getattr(packet, "previous_chapter_ending", "") or ""

    _plan_scenes: list[dict[str, Any]] = []
    _forbidden_elements: list[str] = []
    _forbidden_elements_soft: list[str] = []
    _chapter_plan_payload: dict[str, Any] | None = None
    try:
        _plan_path = bundle.layout.chapter_plan_path(chapter_number)
        if runner._storage.exists(_plan_path):
            _plan_raw = runner._storage.load_json(_plan_path)
            _chapter_plan_payload = _plan_raw if isinstance(_plan_raw, dict) else None
            for _scene in (_plan_raw or {}).get("scene_intents", []):
                if isinstance(_scene, dict) and _scene.get("summary"):
                    _plan_scenes.append(
                        {
                            "scene_id": str(_scene.get("scene_id", "")),
                            "summary": str(_scene.get("summary", "")),
                            "purpose": str(_scene.get("purpose", "")),
                        }
                    )
            _forbidden_elements = [
                str(e) for e in (_plan_raw or {}).get("forbidden_elements", []) if str(e).strip()
            ]
            _forbidden_elements_soft = [
                str(e)
                for e in (_plan_raw or {}).get("forbidden_elements_soft", [])
                if str(e).strip()
            ]
    except Exception as exc:
        _logger.debug(
            "Failed to build _plan_scenes from causal bridge, using empty | error=%s", exc
        )

    step = CausalRepairStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )
    _max_causal_rounds = max(
        1,
        int(getattr(runner._settings, "long_causal_max_repair_rounds", 3) or 3),
    )
    _repair_attempt_guidance = build_repair_attempt_guidance(
        domain="causal",
        round_number=repair_round,
        max_rounds=_max_causal_rounds,
        issues=list(getattr(causal_report, "issues", []) or []),
        previous_issues=list(prev_round_issues or []),
        current_score=float(getattr(causal_report, "causal_score", 0.0) or 0.0),
        score_threshold=float(
            getattr(runner._settings, "long_causal_repair_threshold", 8.0) or 8.0
        ),
    )
    runner._on_step(
        "repair_attempt_guidance",
        {
            "chapter": chapter_number,
            "dimension": "causal",
            "round": repair_round,
            "max_rounds": _max_causal_rounds,
            "strategy": _repair_attempt_guidance.get("strategy_id"),
            "cumulative_tokens": getattr(trace, "total_tokens", 0),
        },
    )
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    causal_repair_kernel_context = (
        kernel_composer.compose_causal_repair_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    payload = CausalRepairInput(
        chapter_number=chapter_number,
        chapter_text=current_text,
        causal_link=causal_link,
        chapter_bridge=bridge,
        causal_report=causal_report,
        must_fix_summaries=must_fix_summaries or [],  # type: ignore[arg-type]
        must_fix_issue_ids=tuple(must_fix_issue_ids or ()),
        previous_chapter_ending=prev_ending,
        repair_round=repair_round,
        prev_round_issues=tuple(prev_round_issues) if prev_round_issues is not None else None,
        per_issue_rounds=per_issue_rounds or {},
        character_profiles=tuple(char_profiles),
        chapter_plan_scenes=tuple(_plan_scenes),
        forbidden_elements=tuple(_forbidden_elements),
        forbidden_elements_soft=tuple(_forbidden_elements_soft),
        style_profile=getattr(bundle, "style_profile", None),
        editorial_contract=getattr(bundle, "editorial_contract", None),
        memory_guidance=memory_guidance,
        repair_attempt_guidance=_repair_attempt_guidance,
        time_convention=getattr(bundle.story_bible, "time_convention", "")
        if getattr(bundle, "story_bible", None)
        else "",
        address_rules=format_address_rules_for_prompt(bundle.story_bible)
        if getattr(bundle, "story_bible", None)
        else "",
        world_context_rules=render_world_context_rules(bundle.story_bible)
        if getattr(bundle, "story_bible", None)
        else "",
        kernel_context=causal_repair_kernel_context,
        chapter_state_packet=packet,
        chapter_outline=getattr(bundle, "chapter_outline", None),
        chapter_plan=_chapter_plan_payload,
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
    )
    result = await run_causal_repair(step, payload)

    runner._on_step(
        "causal_repair_edit",
        {
            "chapter": chapter_number,
            "repair_round": repair_round,
            "applied": result.applied,
            "patches_applied": result.patches_applied,
            "patches_attempted": result.patches_attempted,
            "failure_reason": result.failure_reason,
            "issue_count": len(result.issues),
        },
    )

    if result.applied:
        runner._storage.save_text(
            bundle.layout.chapter_draft_path(chapter_number, 97),
            result.revised_text,
        )

    return result.revised_text


async def _repair_missing_plan_literals_with_patch(
    runner: Any,
    *,
    bundle: Any,
    current_text: str,
    chapter_number: int,
    missing_required_literals: list[dict[str, str]],
    kernel_context: dict[str, Any],
) -> str:
    """Ask the patch LLM to locate and repair explicit Plan literal contracts."""
    from novel_forge.core.utils.patch_utils import split_paragraphs
    from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput
    from novel_forge.pipeline.steps.repair.patch_bridge import PatchCompatIssue

    paragraph_count = max(1, len(split_paragraphs(current_text)))
    issues: list[PatchCompatIssue] = []
    for index, obligation in enumerate(missing_required_literals, start=1):
        literal = str(obligation.get("literal") or "").strip()
        if not literal:
            continue
        scene_id = str(obligation.get("scene_id") or "?")
        contract_id = str(obligation.get("contract_id") or f"literal-{index}")
        reason = str(obligation.get("reason") or "").strip()
        placement_hint = str(obligation.get("placement_hint") or "").strip()
        issues.append(
            PatchCompatIssue(
                issue_id=f"plan-literal:{contract_id}",
                severity="critical",
                summary=f"{scene_id} 缺少 Plan 字面合同「{literal}」",
                issue_type="missing_plan_literal",
                repair_surface="chapter_text",
                location=placement_hint or scene_id,
                paragraph_start=1,
                paragraph_end=paragraph_count,
                location_confidence=0.0,
                anchor_type="llm_locator_from_plan_contract",
                fix_mode="insert",
                fix_suggestion=(
                    f"由你根据全章正文和 Plan 声明的放置提示选择语义合理的落点，"
                    f"以最小幅度补入「{literal}」，必须逐字一致。"
                    f"声明理由：{reason or '未补充'}；"
                    f"放置提示：{placement_hint or scene_id}。"
                    "只返回可安全应用的局部 patch，不得改写无关段落。"
                ),
                postconditions=[
                    {
                        "validator_id": "plan_literal_exact_match",
                        "description": f"修复后正文必须逐字包含「{literal}」",
                    }
                ],
            )
        )

    if not issues:
        return current_text

    before_count = len(missing_required_literals)
    runner._on_step(
        "alignment_literal_patch_attempt",
        {
            "chapter": chapter_number,
            "missing_required_literal_count": before_count,
            "targets": [
                {
                    "issue_id": item.issue_id,
                    "location": item.location,
                    "locator_owner": "patch_llm",
                }
                for item in issues
            ],
        },
    )
    patch_step = ChapterPatchStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
    )
    patch_result = await patch_step.run(
        PatchInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            issues=issues,
            context_size=0,
            must_fix_summaries=[item.summary for item in issues],
            style_profile=getattr(bundle, "style_profile", None),
            kernel_context=kernel_context,
            propagate_failure=False,
        )
    )
    candidate = str(getattr(patch_result, "revised_text", "") or "").strip()
    if not candidate or has_prompt_leaks(candidate):
        candidate = current_text
    compact_candidate = "".join(candidate.split())
    remaining_targets = [
        item
        for item in missing_required_literals
        if str(item.get("literal") or "").strip()
        and "".join(str(item.get("literal") or "").split()) not in compact_candidate
    ]
    after_count = len(remaining_targets)
    improved = after_count < before_count
    runner._on_step(
        "alignment_literal_patch_result",
        {
            "chapter": chapter_number,
            "patches_attempted": int(getattr(patch_result, "patches_attempted", 0) or 0),
            "patches_applied": int(getattr(patch_result, "patches_applied", 0) or 0),
            "missing_before": before_count,
            "missing_after": after_count,
            "improved": improved,
            "postcondition_passed": after_count == 0,
            "fallback": bool(getattr(patch_result, "fallback", False)),
        },
    )
    return candidate if improved else current_text


async def alignment_repair_edit(
    runner: Any,
    *,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    alignment_report: Any,
    trace: Any,
    repair_round: int = 1,
    max_rounds: int = 1,
) -> str:
    """Run one targeted alignment edit with objective plan obligations."""
    from novel_forge.core.constants import TaskType
    from novel_forge.pipeline.long.services.plan_obligations import (
        assess_plan_literal_coverage,
    )
    from novel_forge.pipeline.steps.edit_step import EditInput, EditStep

    def _to_string_list(value: Any) -> list[str]:
        items: list[str] = []
        if isinstance(value, list):
            iterable = value
        elif value:
            iterable = [value]
        else:
            iterable = []
        seen: set[str] = set()
        for item in iterable:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items

    def _build_alignment_edit_directives(report: Any, chapter_plan: Any) -> dict[str, list[str]]:
        missing_main_points = _to_string_list(getattr(report, "missing_main_points", []))
        repair_actions = _to_string_list(getattr(report, "repair_actions", []))
        weak_subplot_points = _to_string_list(getattr(report, "weak_subplot_points", []))
        supportive_subplot_points = _to_string_list(
            getattr(report, "supportive_subplot_points", [])
        )
        preserve_points = _to_string_list(
            [
                *supportive_subplot_points,
                *getattr(chapter_plan, "required_state_transitions", []),
                *getattr(chapter_plan, "key_revelations", []),
                *getattr(chapter_plan, "foreshadowing_plan", []),
            ],
        )
        return {
            "must_cover": missing_main_points,
            "repair_actions": repair_actions,
            "weak_subplot_points": weak_subplot_points,
            "preserve_points": preserve_points,
        }

    alignment_directives = _build_alignment_edit_directives(alignment_report, plan)
    literal_coverage = assess_plan_literal_coverage(plan, current_text)
    missing_required_literals = list(literal_coverage["missing_required_literals"])

    runner._on_step(
        "alignment_repair_attempt",
        {
            "chapter": chapter_number,
            "score": round(float(alignment_report.alignment_score), 2),
            "repair_actions": alignment_report.repair_actions,
            "must_cover": alignment_directives["must_cover"],
            "weak_subplot_points": alignment_directives["weak_subplot_points"],
            "repair_round": repair_round,
            "max_rounds": max_rounds,
            "missing_required_literals": missing_required_literals,
        },
    )
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    edit_kernel_context = (
        kernel_composer.compose_edit_input(chapter_number) if kernel_composer is not None else {}
    )
    if missing_required_literals:
        literal_patched_text = await _repair_missing_plan_literals_with_patch(
            runner,
            bundle=bundle,
            current_text=current_text,
            chapter_number=chapter_number,
            missing_required_literals=missing_required_literals,
            kernel_context=edit_kernel_context,
        )
        if literal_patched_text != current_text:
            remaining_literal_coverage = assess_plan_literal_coverage(
                plan,
                literal_patched_text,
            )
            missing_required_literals = list(
                remaining_literal_coverage["missing_required_literals"]
            )
            if not missing_required_literals:
                runner._storage.save_text(
                    bundle.layout.chapter_draft_path(chapter_number, 99),
                    literal_patched_text,
                )
                runner._on_step(
                    "alignment_repair_edit",
                    {
                        "revised_text": literal_patched_text,
                        "edit_notes": ["Plan literal patch post-condition passed"],
                        "iteration": max(1, int(repair_round or 1)),
                    },
                )
                return literal_patched_text
            current_text = literal_patched_text

    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards

    canon_context = (
        packet.canon_context
        if isinstance(packet.canon_context, dict)
        else {
            "characters": {
                name: (state.model_dump(mode="json") if hasattr(state, "model_dump") else state)
                for name, state in getattr(packet.canon_context, "characters", {}).items()
            }
        }
    )
    edit_ctx = {
        "chapter_number": chapter_number,
        "target_word_count": getattr(bundle.chapter_outline, "expected_word_count", 0),
        "stage_cards": build_stage_cards(
            stage="edit",
            packet=packet,
            chapter_outline=bundle.chapter_outline,
            bridge=bridge,
            plan=plan,
            canon_context=canon_context,
            style_profile=getattr(bundle, "style_profile", None),
            editorial_contract=(
                bundle.editorial_contract.model_dump(mode="json")
                if getattr(bundle, "editorial_contract", None) is not None
                else None
            ),
            editorial_readiness=getattr(bundle, "editorial_readiness", None),
            pov_hint=getattr(bundle, "pov_hint", ""),
            kernel_context=edit_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            settings=runner._settings,
        ),
        "alignment_report": (
            alignment_report.model_dump(mode="json")
            if hasattr(alignment_report, "model_dump")
            else alignment_report
        ),
        "alignment_must_cover": alignment_directives["must_cover"],
        "alignment_repair_actions": alignment_directives["repair_actions"],
        "alignment_weak_subplot_points": alignment_directives["weak_subplot_points"],
        "alignment_preserve_points": alignment_directives["preserve_points"],
        "alignment_required_literals": missing_required_literals,
        "repair_attempt_guidance": build_repair_attempt_guidance(
            domain="generic",
            round_number=max(1, int(repair_round or 1)),
            max_rounds=max(1, int(max_rounds or 1)),
            issues=[
                {
                    "issue_type": "alignment",
                    "summary": item,
                }
                for item in (
                    alignment_directives["must_cover"]
                    or alignment_directives["repair_actions"]
                    or alignment_directives["weak_subplot_points"]
                )
            ],
            current_score=float(getattr(alignment_report, "alignment_score", 0.0) or 0.0),
            score_threshold=float(
                getattr(runner._settings, "long_alignment_threshold", 7.0) or 7.0
            ),
        ),
    }
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text=current_text,
            context=edit_ctx,
            max_rounds=1,
            iteration=max(1, int(repair_round or 1)),
            kernel_context=edit_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
    )
    revised = edit_result.revised_text.strip() or current_text
    if not edit_result.revised_text.strip():
        _logger.warning("第%d章对齐修复LLM返回空文本，保留原文", chapter_number)
    runner._storage.save_text(
        bundle.layout.chapter_draft_path(chapter_number, 99),
        revised,
    )
    runner._on_step("alignment_repair_edit", edit_result)
    return revised


async def recheck_alignment(
    runner: Any,
    bundle: Any,
    packet: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
) -> Any:
    """Re-run alignment check after a repair edit."""
    alignment_step = AlignmentStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
    )
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    alignment_kernel_context = (
        kernel_composer.compose_alignment_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    alignment_report = await alignment_step.run(
        AlignmentInput(
            chapter_outline=bundle.chapter_outline,
            chapter_plan=plan,
            chapter_text=current_text,
            kernel_context=alignment_kernel_context,
        )
    )
    alignment_payload = alignment_report.model_dump(mode="json")
    alignment_payload["source_text_hash"] = source_text_hash(current_text)
    runner._storage.save_json(
        bundle.layout.alignment_report_path(chapter_number),
        alignment_payload,
    )
    runner._on_step("alignment_after_self_repair", alignment_report)
    return alignment_report


def _detect_cross_dimension_links(
    *,
    causal_issues_before: list[Any],
    continuity_issues_after: list[Any],
    source_dimension: str,
    target_dimension: str,
) -> list[CrossDimensionIssueLink]:
    """Detect paragraph-overlap links between repaired causal issues and new continuity issues.

    After causal repair, if new continuity issues appear whose paragraph range
    overlaps with a repaired causal issue, a ``CrossDimensionIssueLink`` is created
    to record the suspected causal relationship.
    """
    links: list[CrossDimensionIssueLink] = []
    if not causal_issues_before or not continuity_issues_after:
        return links

    def _positive_int(value: Any) -> int:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return 0
        return ivalue if ivalue > 0 else 0

    # Build set of repaired causal issue IDs and their paragraph ranges
    repaired_ranges: list[tuple[str, int, int]] = []
    for issue in causal_issues_before:
        issue_id = str(getattr(issue, "issue_id", "") or "").strip()
        if not issue_id:
            continue
        para_start = _positive_int(getattr(issue, "paragraph_start", 0))
        para_end = _positive_int(getattr(issue, "paragraph_end", 0)) or para_start
        repaired_ranges.append((issue_id, para_start, para_end))

    if not repaired_ranges:
        return links

    for cont_issue in continuity_issues_after:
        cont_id = str(getattr(cont_issue, "issue_id", "") or "").strip()
        cont_start = _positive_int(getattr(cont_issue, "paragraph_start", 0))
        cont_end = _positive_int(getattr(cont_issue, "paragraph_end", 0)) or cont_start
        if not cont_id or not cont_start:
            continue
        for src_id, src_start, src_end in repaired_ranges:
            # Check paragraph range overlap
            if src_start <= cont_end and cont_start <= src_end:
                links.append(
                    CrossDimensionIssueLink(
                        source_issue_id=src_id,
                        target_issue_id=cont_id,
                        source_dimension=source_dimension,
                        target_dimension=target_dimension,
                        link_type="suspected_regression",
                        confidence=0.5,
                        evidence=(
                            f"因果修复问题段落{src_start}-{src_end}与"
                            f"新连续性 issues 段落{cont_start}-{cont_end}重叠"
                        ),
                    )
                )
                break  # One link per continuity issue
    return links


@dataclass
class CausalRepairLoopResult:
    """Result returned by _execute_causal_repair_loop."""

    current_text: str
    causal_report: Any
    alignment_report: Any
    continuity_report: Any
    chapter_repair_report: Any | None
    causal_warnings: list[str]
    repair_exhausted: bool = False
    rounds_used: int = 0
    """Number of repair rounds actually executed (for total rounds cap tracking)."""
    applied: bool = False
    """True if causal repair left a text change in the returned chapter text."""
    rolled_back: bool = False
    """True if a causal repair attempt was rejected or rolled back."""
    best_effort_accepted: bool = False
    """True if the causal repair result was accepted via best-effort decision."""
    best_effort_reason: str = ""
    """Human-readable reason for the best-effort acceptance decision."""
    needs_human_review: bool = False
    """True if the chapter should be flagged for human review after best-effort accept."""
    cross_dimension_links: list[CrossDimensionIssueLink] = field(default_factory=list)
    """Links between causal issues repaired and new issues detected in other dimensions."""
    verification_evidence: dict[str, Any] = field(default_factory=dict)
    """Original-validator evidence bound to the returned working candidate."""


class CausalRepairRunner(_RepairRoundKernel[CausalValidationReport]):
    """Causal-specific repair loop runner.

    Encapsulates the causal repair loop with domain-specific hooks:
    - Initial causal validation before loop
    - Per-issue attempt tracking with CausalRepairStep.issue_signature
    - Stagnation detection with custom threshold
    - Cross-dimension regression checks after loop
    - Anchor recalibration on round 2+
    """

    def __init__(
        self,
        runner: Any,
        bundle: Any,
        packet: Any,
        bridge: Any,
        plan: Any,
        trace: Any,
        config: RepairLoopConfig,
        on_step: Callable[[str, Any], None],
        chapter_number: int,
        repair_thresholds: RepairThresholds,
        prev_chapter_ending: str,
        alignment_report: Any,
        continuity_report: Any,
        chapter_repair_report: Any | None,
    ) -> None:
        super().__init__(config, on_step, chapter_number, trace=trace)
        self._runner = runner
        self._bundle = bundle
        self._packet = packet
        self._bridge = bridge
        self._plan = plan
        self._trace = trace
        self._repair_thresholds = repair_thresholds
        self._prev_chapter_ending = prev_chapter_ending
        self._alignment_report = alignment_report
        self._continuity_report = continuity_report
        self._chapter_repair_report = chapter_repair_report
        self._recheck_must_resolve: list[str] = []
        self._recheck_must_resolve_ids: list[str] = []
        self._recheck_prior_issues: list[dict[str, Any]] = []
        self._recheck_repaired_types: list[str] = []
        self._recheck_patch_only: bool = False
        self._loop_start_text: str = ""
        self._initial_report: CausalValidationReport | None = None
        self._repaired_issues_for_links: list[Any] = []
        self._repaired_issue_signatures: set[str] = set()
        self._rolled_back: bool = False
        self._causal_warnings: list[str] = []
        self._original_on_step = on_step
        self._on_step = self._on_step_with_warning_capture

    def _on_step_with_warning_capture(self, event: str, payload: Any) -> None:
        if isinstance(payload, dict) and "error" in payload and "_error" in event:
            self._causal_warnings.append(str(payload["error"]))
        self._original_on_step(event, payload)

    def repair_skip_precheck(
        self, initial_report: CausalValidationReport, ctx: RepairRoundContext[CausalValidationReport]
    ) -> tuple[bool, str]:
        """Skip repair if validation unavailable or score is perfect."""
        status = str(getattr(initial_report, "validation_status", "ok") or "ok").lower()
        if status != "ok":
            fail_mode = str(
                getattr(self._runner._settings, "causal_validation_fail_mode", "warn_unknown")
                or "warn_unknown"
            ).lower()
            msg = f"第{self._chapter_number}章因果链校验不可用（{fail_mode}），当前结果未知，建议人工复核。"
            self._causal_warnings.append(msg)
            self._on_step(
                "causal_validation_unavailable",
                {
                    "chapter": self._chapter_number,
                    "status": status,
                    "mode": fail_mode,
                    "summary": getattr(initial_report, "summary", ""),
                },
            )
            return True, "validation_unavailable"
        if self.compute_score(initial_report) >= 10.0 and ctx.must_fix_issues:
            return True, "score_perfect_with_issues"
        return False, ""

    should_skip_repair = repair_skip_precheck

    async def on_round_start(self, ctx: RepairRoundContext[CausalValidationReport]) -> None:
        """Pre-round: memory guidance + anchor recalibration (round 2+)."""
        from novel_forge.pipeline.long.stages.chapter_repair_support import (
            _build_memory_guidance,
            _get_runner_episodic_memory,
        )

        episodic_memory = _get_runner_episodic_memory(self._runner)
        memory_guidance = await _build_memory_guidance(
            episodic_memory=episodic_memory,
            current_issues=ctx.must_fix_issues,
            current_chapter=self._chapter_number,
        )
        if memory_guidance:
            strategy = decide_repair_strategy(
                RepairContext(
                    current_round=ctx.round_number,
                    max_rounds=self._config.max_rounds,
                    score=ctx.score,
                    score_threshold=self._config.score_threshold,
                    must_fix_issues=tuple(ctx.must_fix_issues),
                    previous_score=ctx.previous_score,
                    memory_guidance=memory_guidance,
                )
            )
            memory_guidance["strategy_recommendation"] = {
                "preferred_strategy": strategy.preferred_strategy,
                "reason": strategy.reason,
                "confidence": strategy.confidence,
                "warning": strategy.warning,
            }
            self._on_step(
                "causal_memory_guidance_added",
                {
                    "chapter": self._chapter_number,
                    "round": ctx.round_number + 1,
                    "matched_issues": len(memory_guidance.get("matched_issues", [])),
                    "success_rate": memory_guidance.get("success_rate"),
                    "preferred_strategy": strategy.preferred_strategy,
                },
            )
        ctx.extra["causal_memory_guidance"] = memory_guidance

        # Anchor recalibration (round 2+, 0-indexed so round_number >= 1)
        if ctx.round_number >= 1 and ctx.must_fix_issues:
            from novel_forge.core.utils.patch_utils import recalibrate_issue_anchors

            conf_floor = getattr(
                self._runner._settings,
                "long_anchor_recalibration_confidence_floor",
                0.5,
            )
            recal_results = recalibrate_issue_anchors(
                ctx.must_fix_issues,
                ctx.current_text,
                confidence_floor=conf_floor,
            )
            degraded = sum(1 for r in recal_results if r["anchor_degraded"])
            if degraded > 0:
                self._on_step(
                    "anchor_recalibration",
                    {
                        "chapter": self._chapter_number,
                        "round": ctx.round_number + 1,
                        "total_issues": len(recal_results),
                        "degraded_anchors": degraded,
                    },
                )
            for r in recal_results:
                iss = r["issue"]
                if hasattr(iss, "paragraph_start"):
                    iss.paragraph_start = r["paragraph_start"]
                if hasattr(iss, "paragraph_end"):
                    iss.paragraph_end = r["paragraph_end"]
                if hasattr(iss, "anchor_type"):
                    iss.anchor_type = r["anchor_type"]
                if hasattr(iss, "location_confidence"):
                    iss.location_confidence = r["location_confidence"]

    async def execute_repair(self, ctx: RepairRoundContext[CausalValidationReport]) -> str:
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

        prev_issue_summaries = [
            getattr(i, "summary", "") or str(getattr(i, "issue_type", ""))
            for i in ctx.must_fix_issues
            if CausalRepairStep.issue_signature(i)
            in {sig for sig, cnt in ctx.issue_attempts.items() if cnt > 1}
        ]
        memory_guidance = ctx.extra.get("causal_memory_guidance")
        return await causal_repair_edit(
            self._runner,
            bundle=self._bundle,
            packet=self._packet,
            bridge=self._bridge,
            current_text=ctx.current_text,
            chapter_number=self._chapter_number,
            causal_report=ctx.report,
            trace=self._trace,
            repair_round=ctx.round_number + 1,
            prev_round_issues=prev_issue_summaries,
            per_issue_rounds=dict(ctx.issue_attempts),
            must_fix_summaries=[
                getattr(i, "summary", "") for i in ctx.must_fix_issues if getattr(i, "summary", "")
            ],
            must_fix_issue_ids=[
                str(getattr(i, "issue_id", "") or "").strip()
                for i in ctx.must_fix_issues
                if str(getattr(i, "issue_id", "") or "").strip()
            ],
            memory_guidance=memory_guidance,
        )

    async def on_repair_success(
        self, ctx: RepairRoundContext[CausalValidationReport], revised_text: str
    ) -> str:
        """Post-repair: store recheck params for evaluate()."""
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

        must_fix = ctx.must_fix_issues
        self._recheck_must_resolve = [
            getattr(i, "summary", "") for i in must_fix if getattr(i, "summary", "")
        ]
        self._recheck_must_resolve_ids = [
            str(getattr(i, "issue_id", "") or "").strip()
            for i in must_fix
            if str(getattr(i, "issue_id", "") or "").strip()
        ]
        self._recheck_prior_issues = [
            {
                "issue_id": str(getattr(i, "issue_id", "") or "").strip(),
                "issue_type": (getattr(i, "issue_type", "") or "").lower(),
                "severity": (getattr(i, "severity", "") or "medium").lower(),
                "location": getattr(i, "location", "") or "",
                "summary": getattr(i, "summary", "") or "",
            }
            for i in must_fix
        ]
        for issue in must_fix:
            signature = self.issue_signature(issue)
            if signature and signature not in self._repaired_issue_signatures:
                self._repaired_issue_signatures.add(signature)
                self._repaired_issues_for_links.append(issue)
        repaired_types = sorted(
            {
                (getattr(i, "issue_type", "") or "").lower()
                for i in must_fix
                if (getattr(i, "issue_type", "") or "").strip()
            }
        )
        patch_only_types = set(CausalRepairStep.patch_only_issue_types())
        self._recheck_patch_only = bool(repaired_types) and all(
            t in patch_only_types for t in repaired_types
        )
        self._recheck_repaired_types = repaired_types
        return revised_text

    async def evaluate(self, text: str) -> Any:
        return await run_causal_validation(
            self._runner,
            self._bundle,
            self._bridge,
            text,
            self._chapter_number,
            self._trace,
            previous_chapter_ending=self._prev_chapter_ending,
            recheck_mode=True,
            must_resolve_summaries=self._recheck_must_resolve,
            must_resolve_issue_ids=self._recheck_must_resolve_ids,
            recheck_strategy=getattr(
                self._runner._settings, "recheck_strategy", "targeted_with_global_guard"
            ),
            prior_issues=self._recheck_prior_issues,
            repaired_issue_types=self._recheck_repaired_types,
            patch_only_repair=self._recheck_patch_only,
        )

    async def on_recheck_success(
        self,
        ctx: RepairRoundContext[CausalValidationReport],
        new_report: CausalValidationReport,
        ledger: Any,
    ) -> None:
        """Post-recheck: record causal repair memory results."""
        from novel_forge.pipeline.long.stages.chapter_repair_support import (
            _get_runner_episodic_memory,
        )

        episodic_memory = _get_runner_episodic_memory(self._runner)
        score_before = float(getattr(ctx.report, "causal_score", 0.0) or 0.0)
        score_after = float(getattr(new_report, "causal_score", 0.0) or 0.0)
        recorded = _record_causal_repair_memory_result(
            episodic_memory=episodic_memory,
            chapter_number=self._chapter_number,
            round_num=ctx.round_number + 1,
            pre_issues=ctx.pre_issues,
            ledger=ledger,
            score_before=score_before,
            score_after=score_after,
            strategy=_causal_repair_strategy_label(ctx.pre_issues),
        )
        if recorded:
            self._on_step(
                "causal_repair_memory_results_recorded",
                {
                    "chapter": self._chapter_number,
                    "round": ctx.round_number + 1,
                    "recorded": recorded,
                },
            )

    async def on_loop_exit(self, ctx: RepairRoundContext[CausalValidationReport]) -> None:
        """Post-loop: warning for remaining issues + cross-dimension regression."""
        from novel_forge.pipeline.long.stages.chapter_repair_support import (
            run_post_repair_checks,
        )

        # Post-loop warning if must-fix issues remain
        if ctx.must_fix_issues:
            message = (
                f"因果链校验发现 {len(self.extract_issues(ctx.report) or [])} 个问题，"
                f"其中高优先级 {len(ctx.must_fix_issues)} 个，建议人工复核。"
            )
            self._causal_warnings.append(message)
            self._on_step(
                "causal_validation_warning",
                {
                    "chapter": self._chapter_number,
                    "message": message,
                    "causal_score": self.compute_score(ctx.report) if ctx.report else 0.0,
                    "issue_count": len(self.extract_issues(ctx.report) or []),
                    "high_priority_issue_count": len(ctx.must_fix_issues),
                },
            )

        # Cross-dimension regression check
        total_change = self._text_change_ratio(self._loop_start_text, ctx.current_text)
        rounds_used = ctx.round_number + 1 if ctx.any_applied else 0
        if rounds_used > 0 and ctx.current_text != self._loop_start_text:
            regression_ctx = CausalRegressionContext(
                total_change_ratio=total_change,
                thresholds=self._repair_thresholds,
                alignment_score=self._get_alignment_score(),
                continuity_score=getattr(self._continuity_report, "continuity_score", 0.0),
                has_prompt_leaks=self._has_prompt_leaks(),
            )
            regression_action = decide_causal_regression_action(regression_ctx)

            if regression_action == CausalRegressionAction.SKIP:
                self._on_step(
                    "causal_post_repair_regression_skipped",
                    {
                        "chapter": self._chapter_number,
                        "causal_repair_rounds": rounds_used,
                        "change_ratio": round(total_change, 4),
                        "reason": "minor_change_and_quality_ok",
                    },
                )
            else:
                self._on_step(
                    "causal_post_repair_regression_start",
                    {"chapter": self._chapter_number, "causal_repair_rounds": rounds_used},
                )
                skip_chapter_repair = (
                    regression_action == CausalRegressionAction.RECHECK_SKIP_CHAPTER_REPAIR
                )
                try:
                    (
                        self._alignment_report,
                        self._continuity_report,
                        self._chapter_repair_report,
                    ) = await run_post_repair_checks(
                        self._runner,
                        self._bundle,
                        self._packet,
                        self._bridge,
                        self._plan,
                        self._loop_start_text,
                        ctx.current_text,
                        self._chapter_number,
                        self._alignment_report,
                        self._continuity_report,
                        self._chapter_repair_report,
                        self._trace,
                        skip_chapter_repair=skip_chapter_repair,
                    )
                    self._on_step(
                        "causal_post_repair_regression_done",
                        {
                            "chapter": self._chapter_number,
                            "alignment_score": round(
                                float(getattr(self._alignment_report, "alignment_score", 0.0)), 2
                            ),
                            "continuity_score": round(
                                float(
                                    getattr(self._continuity_report, "continuity_score", 0.0)
                                ),
                                2,
                            ),
                            "continuity_issues": len(
                                getattr(self._continuity_report, "issues", []) or []
                            ),
                        },
                    )
                except Exception as exc:
                    self._on_step(
                        "causal_post_repair_regression_failed",
                        {
                            "chapter": self._chapter_number,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    self._rolled_back = True

    def extract_issues(self, report: Any) -> list[Any]:
        return list(getattr(report, "issues", []) or [])

    def compute_score(self, report: Any) -> float:
        return float(getattr(report, "causal_score", 10.0) or 10.0)

    def issue_signature(self, issue: Any) -> str:
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

        return CausalRepairStep.issue_signature(issue)

    def build_extra_result(
        self,
        ctx: RepairRoundContext[CausalValidationReport],
        result: RepairLoopResult[CausalValidationReport],
    ) -> RepairLoopResult[CausalValidationReport]:
        """Convert RepairLoopResult to CausalRepairLoopResult."""
        loop_start = self._loop_start_text or result.current_text
        current_text = loop_start if self._rolled_back else result.current_text
        rounds = result.rounds_used
        repaired_issues = (
            list(self._repaired_issues_for_links)
            or list(ctx.pre_issues or [])
            or list(getattr(self._initial_report, "issues", []) or [])
        )
        cross_links = _detect_cross_dimension_links(
            causal_issues_before=repaired_issues,
            continuity_issues_after=list(
                getattr(self._continuity_report, "issues", []) or []
            ),
            source_dimension="causal",
            target_dimension="continuity",
        )
        return CausalRepairLoopResult(
            current_text=current_text,
            causal_report=result.report,
            alignment_report=self._alignment_report,
            continuity_report=self._continuity_report,
            chapter_repair_report=self._chapter_repair_report,
            causal_warnings=list(self._causal_warnings),
            repair_exhausted=result.repair_exhausted,
            rounds_used=rounds,
            applied=bool(rounds > 0 and current_text != loop_start),
            rolled_back=self._rolled_back,
            best_effort_accepted=result.best_effort_accepted,
            best_effort_reason=result.best_effort_reason,
            needs_human_review=result.needs_human_review,
            cross_dimension_links=cross_links,
            verification_evidence=dict(result.extra.get("candidate_verification") or {}),
        )

    async def run(
        self,
        current_text: str,
        initial_report: CausalValidationReport,
    ) -> CausalRepairLoopResult:
        """Override run to ensure all paths return CausalRepairLoopResult."""
        self._loop_start_text = current_text
        self._initial_report = initial_report
        result = await super().run(current_text=current_text, initial_report=initial_report)
        if isinstance(result, CausalRepairLoopResult):
            return result
        return self.build_extra_result(
            RepairRoundContext[CausalValidationReport](
                current_text=result.current_text,
                report=result.report,
            ),
            result,
        )

    # Helper overrides for ABC _detect_drift (causal-specific character tracking)
    def _get_pov_character(self) -> str:
        return getattr(self._bundle.chapter_outline, "pov_character", "") or ""

    def _get_known_characters(self) -> list[str]:
        return [
            name
            for name in (getattr(self._bundle.chapter_outline, "required_characters", []) or [])
            if name
        ]

    def _get_chapter_outline(self) -> Any:
        return self._bundle.chapter_outline

    def _detect_drift(self, ctx: RepairRoundContext[CausalValidationReport]) -> Any:
        return detect_drift(
            ctx.pre_round_text,
            ctx.current_text,
            pov_character=self._get_pov_character(),
            known_characters=self._get_known_characters(),
            chapter_outline=self._get_chapter_outline(),
        )

    def _get_alignment_score(self) -> float:
        return float(getattr(self._alignment_report, "alignment_score", 7.0) or 7.0)

    def _has_prompt_leaks(self) -> bool:
        return bool(has_prompt_leaks(self._chapter_repair_report))


async def _execute_causal_repair_loop(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    on_step: Callable[[str, Any], None],
    *,
    current_text: str,
    alignment_report: Any,
    continuity_report: Any,
    chapter_repair_report: Any | None,
    chapter_number: int,
    trace: Any,
    repair_thresholds: RepairThresholds,
    prev_chapter_ending: str,
    max_causal_rounds: int | None = None,
    initial_causal_report: Any | None = None,
) -> CausalRepairLoopResult:
    """Run the causal validation and repair loop.

    Delegates to CausalRepairRunner which encapsulates the loop logic.
    """
    failure_policy = RepairFailurePolicy(on_step, logger=_logger)

    if initial_causal_report is not None:
        causal_report = initial_causal_report
        _logger.debug("causal_validation: reused quality-stage report | chapter=%d", chapter_number)
    else:
        try:
            causal_report = await run_causal_validation(
                runner,
                bundle,
                bridge,
                current_text,
                chapter_number,
                trace,
                previous_chapter_ending=prev_chapter_ending,
            )
        except Exception as exc:
            outcome = failure_policy.validation_failed(
                snapshot=RepairRoundSnapshot(
                    stage=RepairDimension.CAUSAL,
                    chapter_number=chapter_number,
                    text=current_text,
                ),
                exc=exc,
                action="skip_causal_validation",
                warning=f"因果链校验失败，建议人工复核：{type(exc).__name__}: {exc}",
            )
            return CausalRepairLoopResult(
                current_text=current_text,
                causal_report=None,
                alignment_report=alignment_report,
                continuity_report=continuity_report,
                chapter_repair_report=chapter_repair_report,
                causal_warnings=[outcome.warning],
                rounds_used=0,
            )

    _max_rounds = (
        max_causal_rounds
        if max_causal_rounds is not None
        else max(0, getattr(runner._settings, "long_causal_max_repair_rounds", 2))
    )
    _causal_threshold = getattr(runner._settings, "long_causal_threshold", 5.0)
    _causal_enabled = bool(getattr(runner._settings, "long_causal_repair_enabled", True))
    _hard_floor = max(
        float(getattr(runner._settings, "long_causal_hard_block_threshold", 4.0) or 0.0),
        float(getattr(runner._settings, "long_best_effort_accept_floor", 6.0) or 0.0),
    )

    config = RepairLoopConfig(
        max_rounds=_max_rounds,
        must_fix_severity=getattr(runner._settings, "repair_must_fix_severity", "critical")
        or "critical",
        score_threshold=_causal_threshold,
        change_budget=repair_thresholds.change_budget,
        stagnation_delta=repair_thresholds.stagnation_delta,
        enabled=_causal_enabled,
        hard_floor=_hard_floor,
        max_issues_per_round=resolve_max_issues_per_round(
            runner._settings,
            "long_causal_repair_max_issues_per_round",
        ),
    )

    causal_runner = CausalRepairRunner(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        trace=trace,
        config=config,
        on_step=on_step,
        chapter_number=chapter_number,
        repair_thresholds=repair_thresholds,
        prev_chapter_ending=prev_chapter_ending,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=chapter_repair_report,
    )

    return await causal_runner.run(
        current_text=current_text,
        initial_report=causal_report,
    )
