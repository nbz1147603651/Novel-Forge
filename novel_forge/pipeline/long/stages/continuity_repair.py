"""Continuity repair: repair loop, post-repair checks, and critique conversion."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Callable, cast

from novel_forge.common.constants import severity_at_least
from novel_forge.core.domain.world_context import (
    format_address_rules_for_prompt,
    render_world_context_rules,
)
from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.utils.pipeline_helpers import has_prompt_leaks
from novel_forge.core.utils.semantic_drift import detect_drift  # noqa: F401
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.decisions import (
    RepairContext,
    RepairThresholds,
    decide_repair_strategy,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.stages.chapter_repair_support import (
    _build_memory_guidance,
    _get_runner_episodic_memory,
    run_post_repair_checks,
)
from novel_forge.pipeline.long.stages.chapter_repair_support import (
    _convert_critique_to_continuity as _convert_critique_to_continuity,
)
from novel_forge.pipeline.long.stages.continuity_repair_quality import (
    QualityGateResult as QualityGateResult,
)
from novel_forge.pipeline.long.stages.continuity_repair_quality import (
    guard_repair_quality_gate as guard_repair_quality_gate,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import _compute_text_similarity
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    RepairLoopResult,
    RepairRoundContext,
    _RepairRoundKernel,
    resolve_max_issues_per_round,
)
from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep

_logger = get_logger("pipeline.continuity_repair")


def _truncate_at_boundary(text: str, max_len: int = 20) -> str | None:
    """Truncate text at the nearest punctuation/space boundary not exceeding max_len.

    Boundaries include: ，。！？；：、""''""''()（）【】「」『』\\n\\t
    Returns None if text is empty or result would be shorter than 2 chars.
    """
    if not text:
        return None
    text = text.strip()
    if len(text) <= max_len:
        return text if len(text) >= 2 else None

    candidate = text[:max_len]
    # Find the last boundary character in the candidate
    boundaries = "，。！？；：、''\"\"()（）【】「」『』\n\t "
    last_boundary_pos = -1
    for i in range(len(candidate) - 1, -1, -1):
        if candidate[i] in boundaries:
            last_boundary_pos = i
            break

    if last_boundary_pos > 0:
        result = candidate[:last_boundary_pos].strip()
        return result if len(result) >= 2 else None
    # No boundary found, return candidate stripped if long enough
    result = candidate.strip()
    return result if len(result) >= 2 else None


def _extract_pattern_from_issue(issue: Any) -> str | None:
    """Extract a concrete banned phrase from an unresolved repetition issue.

    Prioritizes issue.evidence (actual text from chapter) over issue.summary
    (description). Tries quoted text first, then truncates at a natural boundary.
    Returns None if nothing useful can be extracted.
    """
    _QUOTE_RE = r'[""]([^""]{2,30})[""]|[\u2018\u2019]([^\u2018\u2019]{2,30})[\u2018\u2019]'
    _REPETITION_KEYWORDS = {"重复", "雷同", "模板", "句式", "套话", "陈词", "滥调"}

    def _extract_quoted(text: str) -> str | None:
        if not text:
            return None
        quoted = re.findall(_QUOTE_RE, text)
        for match in quoted:
            phrase = match[0] or match[1]
            if phrase and len(phrase.strip()) >= 2:
                return str(phrase).strip()
        return None

    evidence = getattr(issue, "evidence", "") or ""
    if result := _extract_quoted(evidence):
        return result
    if evidence:
        if result := _truncate_at_boundary(evidence, max_len=20):
            return result

    summary = getattr(issue, "summary", "") or ""
    if result := _extract_quoted(summary):
        return result
    if summary and any(kw in summary for kw in _REPETITION_KEYWORDS):
        if result := _truncate_at_boundary(summary, max_len=20):
            return result

    return None


def _build_per_issue_rounds(
    current_issues: list[Any],
    previous_issues: list[Any],
    current_round: int,
) -> dict[str, int]:
    """Build per-issue round tracking for continuity repair.

    Tracks how many rounds each issue has persisted across repair attempts.
    Similar to the causal repair per_issue_rounds mechanism.

    Args:
        current_issues: Issues detected in current round
        previous_issues: Issues from previous round snapshot
        current_round: Current repair round number (0-based)

    Returns:
        Dict mapping issue signature/summary -> round count
    """
    per_issue: dict[str, int] = {}

    # Build a set of previous issue summaries for quick lookup
    prev_issue_sigs = set()
    for iss in previous_issues:
        sig = getattr(iss, "summary", "") or getattr(iss, "issue_type", "")
        if sig:
            prev_issue_sigs.add(sig)

    # For each current issue, determine how many rounds it has persisted
    for iss in current_issues:
        sig = getattr(iss, "summary", "") or getattr(iss, "issue_type", "")
        if not sig:
            continue

        if sig in prev_issue_sigs:
            # Issue persisted from previous round - increment counter
            per_issue[sig] = current_round + 1
        else:
            # New issue this round
            per_issue[sig] = 1

    return per_issue


def _is_en_language(language: str) -> bool:
    """Return True if the language code indicates English output."""
    key = str(language or "zh").strip().lower().replace("_", "-")
    return key in {"en", "en-us", "en-gb", "english"} or key.startswith("en")


def _build_continuity_escalation_note(
    per_issue_rounds: dict[str, int],
    current_round: int,
    language: str = "zh",
) -> str | None:
    """Build escalation note for continuity repair prompt injection.

    Creates a warning message telling the LLM which issues have persisted
    through multiple repair rounds and need a completely different strategy.

    Args:
        per_issue_rounds: Dict mapping issue signature -> round count
        current_round: Current repair round number (0-based)
        language: Output language code (zh or en)

    Returns:
        Escalation note string or None if no escalation needed
    """
    if current_round == 0:
        return None

    # Find issues that have persisted through multiple rounds
    stubborn_issues = []
    for sig, rounds in per_issue_rounds.items():
        if rounds > 1:
            # Truncate signature for readability
            short_sig = sig[:60] + "..." if len(sig) > 60 else sig
            if _is_en_language(language):
                stubborn_issues.append(f"- [{short_sig}] Attempted {rounds} rounds without resolution")
            else:
                stubborn_issues.append(f"- [{short_sig}] 已尝试 {rounds} 轮未解决")

    if not stubborn_issues:
        return None

    if _is_en_language(language):
        return (
            "[Repair Experience Upgrade] The following issues have persisted through multiple "
            "repair rounds without resolution. Please adopt a **completely different** repair "
            "strategy and avoid repeating previous patterns:\n"
            + "\n".join(stubborn_issues)
            + "\n\nPlease carefully analyze why previous strategies failed and try to address "
            "the root cause."
        )
    return (
        "【修复经验升级】以下问题已经历多轮修复仍未解决，"
        "请采用**完全不同**的修复策略，避免重复之前的模式：\n"
        + "\n".join(stubborn_issues)
        + "\n\n请仔细分析为什么之前的策略失败，并尝试从根本原因入手解决。"
    )






def _compact_issue_text(value: Any) -> str:
    """Normalize issue text enough to match reports after serialization."""
    return "".join(str(value or "").split()).lower()


def _find_current_critique_signature(
    episodic_memory: Any,
    issue: Any,
    *,
    current_chapter: int,
) -> str | None:
    """Find the persisted critique entry matching a current chapter issue."""
    critique_index = getattr(episodic_memory, "_critique_index", {}) or {}
    issue_type = _compact_issue_text(getattr(issue, "issue_type", ""))
    summary = _compact_issue_text(getattr(issue, "summary", ""))
    if not issue_type or not summary:
        return None

    for sig, entry in critique_index.items():
        if getattr(entry, "chapter_number", None) != current_chapter:
            continue
        if _compact_issue_text(getattr(entry, "issue_type", "")) != issue_type:
            continue
        if _compact_issue_text(getattr(entry, "summary", "")) == summary:
            return str(sig)
    return None


def _record_continuity_repair_memory_result(
    *,
    episodic_memory: Any,
    chapter_number: int,
    round_num: int,
    pre_issues: list[Any],
    continuity_repair: Any,
    ledger: Any,
    score_before: float,
    score_after: float,
) -> int:
    """Persist repair outcome back onto matching critique entries.

    The memory-guidance search is only useful once critique entries learn which
    strategies worked or caused regressions.  We match current-chapter critique
    entries by issue type + summary and attach the round result.
    """
    record_result = getattr(episodic_memory, "record_repair_result", None)
    if not callable(record_result):
        return 0

    repaired_types = {
        str(issue_type or "").lower()
        for issue_type in getattr(continuity_repair, "repaired_issue_types", []) or []
    }
    if not repaired_types:
        return 0

    if getattr(ledger, "has_regression", False):
        result = "regression"
    elif getattr(ledger, "resolved", None) or getattr(ledger, "downgraded", None):
        result = "success"
    else:
        result = "no_op"

    strategy = "patch" if getattr(continuity_repair, "patch_only", False) else "fulltext"
    new_issue_types = [
        str(getattr(issue, "issue_type", "") or "")
        for issue in getattr(ledger, "new_high_critical", []) or []
        if getattr(issue, "issue_type", "")
    ]

    recorded = 0
    seen_signatures: set[str] = set()
    for issue in pre_issues:
        issue_type = str(getattr(issue, "issue_type", "") or "").lower()
        if issue_type not in repaired_types:
            continue
        signature = _find_current_critique_signature(
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






async def pre_evaluate_repair_alignment(
    *,
    original_text: str,
    repaired_text: str,
    chapter_outline: Any,
    chapter_plan: Any,
    alignment_input: AlignmentInput,
    runner: Any,
    trace: Any,
    known_original_score: float | None = None,
) -> tuple[float, str | None]:
    """Pre-evaluate repair alignment before applying the repaired text.

    Calls AlignmentStep to assess the alignment score of the repaired text
    and predicts the score change compared to the original text.

    This is a **diagnostic-only** function: it logs warnings if the predicted
    score drops significantly but does NOT veto the repair.

    Args:
        original_text: Chapter text before repair.
        repaired_text: Chapter text after repair.
        chapter_outline: The chapter outline for alignment checking.
        chapter_plan: The chapter plan for alignment checking.
        alignment_input: Original AlignmentInput (for extracting context fields).
        runner: The pipeline runner (provides router, builder, settings).
        trace: The current trace context.
        known_original_score: If provided, skip the redundant LLM call for
            original text alignment and use this score directly.

    Returns:
        Tuple of (predicted_score_change, warning_message_or_none).
        Positive change means improvement, negative means degradation.
    """
    try:
        alignment_step = AlignmentStep(
            runner._router, runner._builder, settings=runner._settings, trace=trace
        )

        repaired_alignment = await alignment_step.run(
            AlignmentInput(
                chapter_outline=chapter_outline,
                chapter_plan=chapter_plan,
                chapter_text=repaired_text,
                kernel_context=alignment_input.kernel_context,
            )
        )

        repaired_score = float(getattr(repaired_alignment, "alignment_score", 0.0))

        # Use known score when available to avoid redundant LLM call.
        # The original text's alignment was already computed in the prior
        # review pass; recomputing it here wastes ~8K tokens per repair round.
        if known_original_score is not None:
            original_score = known_original_score
        else:
            original_alignment = await alignment_step.run(
                AlignmentInput(
                    chapter_outline=chapter_outline,
                    chapter_plan=chapter_plan,
                    chapter_text=original_text,
                    kernel_context=alignment_input.kernel_context,
                )
            )
            original_score = float(getattr(original_alignment, "alignment_score", 0.0))

        score_change = round(repaired_score - original_score, 2)

        warning_msg = None
        if score_change < -1.0:
            warning_msg = (
                f"预评估: 修复后对齐分数预测下降 {abs(score_change):.1f} 分 "
                f"({original_score:.1f} → {repaired_score:.1f})。"
                f"missing_main_points: {repaired_alignment.missing_main_points}, "
                f"weak_subplot_points: {repaired_alignment.weak_subplot_points}"
            )
            _logger.warning("pre_evaluate_repair_alignment: %s", warning_msg)
        else:
            _logger.info(
                "pre_evaluate_repair_alignment: 分数变化 %.1f → %.1f (Δ=%.2f)",
                original_score,
                repaired_score,
                score_change,
            )

        return score_change, warning_msg

    except Exception as exc:
        _logger.warning("pre_evaluate_repair_alignment: 评估失败 (跳过): %s", exc, exc_info=True)
        return 0.0, None


async def run_continuity_repair(
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    continuity_report: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    *,
    must_fix_issues: list[Any] | None = None,
    memory_hints: dict[str, Any] | None = None,
    current_alignment_report: Any | None = None,
) -> tuple[str, Any]:
    """Run continuity repair on the chapter text.

    Returns:
        Tuple of (revised_text, continuity_repair).
    """
    from novel_forge.core.schemas.continuity import RepairPlan
    from novel_forge.pipeline.long.repair import run_continuity_repair as _run_repair
    from novel_forge.pipeline.steps.continuity_artifact_repair import BridgeArtifactRepairer
    from novel_forge.pipeline.steps.continuity_repair_step import (
        ContinuityRepairInput,
        ContinuityRepairResult,
        ContinuityRepairStep,
    )

    kernel_composer = await load_story_kernel_composer(runner, bundle)
    continuity_repair_kernel_context = (
        kernel_composer.compose_for_step("continuity_repair", chapter_number)
        if kernel_composer is not None
        else {}
    )
    alignment_kernel_context = (
        kernel_composer.compose_alignment_input(chapter_number)
        if kernel_composer is not None
        else {}
    )

    artifact_repair = BridgeArtifactRepairer.repair(
        bridge=bridge,
        chapter_state_packet=packet,
        chapter_outline=getattr(bundle, "chapter_outline", None),
        issues=list(getattr(continuity_report, "issues", []) or []),
    )
    if artifact_repair.applied:
        bridge = artifact_repair.revised_bridge
        updated_issues = []
        for issue in list(getattr(continuity_report, "issues", []) or []):
            if BridgeArtifactRepairer._owns_bridge_artifact(issue):
                if hasattr(issue, "model_copy"):
                    issue = issue.model_copy(
                        update={
                            "status": "artifact_fixed",
                            "blocking": False,
                            "diagnostic_note": "bridge_artifact repaired before text repair",
                        }
                    )
                elif isinstance(issue, dict):
                    issue = {
                        **issue,
                        "status": "artifact_fixed",
                        "blocking": False,
                        "diagnostic_note": "bridge_artifact repaired before text repair",
                    }
            updated_issues.append(issue)
        if hasattr(continuity_report, "model_copy"):
            continuity_report = continuity_report.model_copy(update={"issues": updated_issues})
        try:
            runner._storage.save_json(
                bundle.layout.chapter_bridge_path(chapter_number),
                bridge.model_dump(mode="json"),
            )
        except Exception as exc:
            _logger.warning(
                "continuity_artifact_repair_save_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        runner._on_step(
            "continuity_artifact_repair",
            {
                "chapter": chapter_number,
                "surface": "bridge_artifact",
                "applied": True,
                "operations": [
                    {
                        "path": op.path,
                        "old_value": op.old_value,
                        "new_value": op.new_value,
                        "reason": op.reason,
                    }
                    for op in artifact_repair.operations
                ],
            },
        )

    # ── Score-gate: skip expensive full-chapter rewrite for near-clean chapters ──
    threshold = getattr(runner._settings, "long_continuity_repair_threshold", 8.5)
    if threshold > 0.0:
        score = getattr(continuity_report, "continuity_score", 0.0)

        def _is_open_issue(issue: Any) -> bool:
            if isinstance(issue, dict):
                status = issue.get("status", "open")
            else:
                status = getattr(issue, "status", "open")
            return str(status or "open").strip().lower() == "open"

        issues = [
            issue
            for issue in (getattr(continuity_report, "issues", []) or [])
            if _is_open_issue(issue)
        ]
        hard_issue_types = {
            "bridge_contract_not_followed",
            "location_jump",
            "custody_break",
            "pov_intrusion",
            "prompt_leak",
            "time_marker_invalid",
        }
        # Respect repair_must_fix_severity: if user set "medium" or above, any issue
        # at that level bypasses the score-gate and forces repair.
        _must_fix_sev = (
            getattr(runner._settings, "repair_must_fix_severity", "critical") or "critical"
        ).lower()

        has_must_fix_issue = _must_fix_sev != "off" and any(
            severity_at_least(getattr(iss, "severity", "").lower(), _must_fix_sev) for iss in issues
        )
        has_hard_structural_issue = any(
            (getattr(iss, "issue_type", "") or "").lower() in hard_issue_types
            and severity_at_least((getattr(iss, "severity", "") or "").lower(), "high")
            for iss in issues
        )
        has_opening_boundary_issue = any(
            (getattr(iss, "issue_type", "") or "").lower()
            in {"opening_gap", "bridge_contract_not_followed"}
            and (
                getattr(iss, "validator_id", "") == "opening_transition_validator"
                or "开头" in str(getattr(iss, "location", "") or "")
                or "开场" in str(getattr(iss, "location", "") or "")
                or (getattr(iss, "issue_type", "") or "").lower() == "opening_gap"
            )
            and severity_at_least((getattr(iss, "severity", "") or "").lower(), "medium")
            for iss in issues
        )
        _skip_repair = False
        _skip_reason = ""

        if score >= 10.0:
            # Perfect score: LLM evaluation found zero issues — skip unconditionally.
            # A perfect score contradicts the existence of must-fix issues; trust the
            # score over the issue list to avoid unnecessary repair loops.
            _skip_repair = True
            _skip_reason = (
                f"continuity_score {score:.1f} == 10.0 (perfect), skipping repair unconditionally"
            )
        elif (
            not has_must_fix_issue
            and not has_hard_structural_issue
            and not has_opening_boundary_issue
            and score >= threshold
        ):
            _skip_repair = True
            _skip_reason = (
                f"continuity_score {score:.1f} >= threshold {threshold:.1f}, "
                f"no {_must_fix_sev}+ issues and no hard structural issues — repair skipped"
            )

        if _skip_repair:
            _logger.info(
                "continuity_repair skipped | continuity_score=%.1f | threshold=%.1f | chapter=%d",
                score,
                threshold,
                chapter_number,
            )
            no_op_plan = RepairPlan(no_op=True)
            no_op_result = ContinuityRepairResult(
                revised_text=current_text,
                repair_plan=no_op_plan,
                applied=False,
                failure_reason=_skip_reason,
                revised_bridge=bridge if artifact_repair.applied else None,
            )
            runner._storage.save_json(
                bundle.layout.repair_plan_path(chapter_number),
                no_op_plan.model_dump(mode="json"),
            )
            runner._on_step("continuity_repair", no_op_result)
            return current_text, no_op_result

    continuity_repair_step = ContinuityRepairStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    pre_repair_text = current_text
    pre_repair_score = getattr(continuity_report, "continuity_score", 0.0)
    pre_repair_issue_count = len(getattr(continuity_report, "issues", []) or [])

    continuity_repair = await _run_repair(
        continuity_repair_step,
        ContinuityRepairInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_state_packet=packet,
            chapter_bridge=bridge,
            chapter_plan=plan,
            continuity_report=continuity_report,
            chapter_outline=bundle.chapter_outline,
            style_profile=getattr(bundle, "style_profile", None),
            editorial_contract=getattr(bundle, "editorial_contract", None),
            must_fix_issues=tuple(must_fix_issues or []),
            memory_context=memory_hints,
            time_convention=getattr(bundle.story_bible, "time_convention", "")
            if getattr(bundle, "story_bible", None)
            else "",
            address_rules=format_address_rules_for_prompt(bundle.story_bible)
            if getattr(bundle, "story_bible", None)
            else "",
            world_context_rules=render_world_context_rules(bundle.story_bible)
            if getattr(bundle, "story_bible", None)
            else "",
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
            kernel_context=continuity_repair_kernel_context,
        ),
    )
    if artifact_repair.applied:
        continuity_repair = dataclasses.replace(continuity_repair, revised_bridge=bridge)

    if continuity_repair.applied:
        # ── Pre-evaluation: predict alignment impact before applying repair ──
        try:
            alignment_input_for_eval = AlignmentInput(
                chapter_outline=bundle.chapter_outline,
                chapter_plan=plan,
                chapter_text=current_text,
                kernel_context=alignment_kernel_context,
            )
            # Pass known alignment score to avoid redundant LLM call
            _known_align_score: float | None = None
            if current_alignment_report is not None:
                _known_align_score = float(
                    getattr(current_alignment_report, "alignment_score", 0.0) or 0.0
                )
            score_change, warning = await pre_evaluate_repair_alignment(
                original_text=current_text,
                repaired_text=continuity_repair.revised_text,
                chapter_outline=bundle.chapter_outline,
                chapter_plan=plan,
                alignment_input=alignment_input_for_eval,
                runner=runner,
                trace=trace,
                known_original_score=_known_align_score,
            )
            if warning:
                runner._on_step(
                    "pre_repair_alignment_warning",
                    {"chapter": chapter_number, "score_change": score_change, "warning": warning},
                )
        except Exception as exc:
            _logger.warning("pre_evaluate_repair_alignment integration error: %s", exc)

        change_ratio = _compute_text_similarity(pre_repair_text, continuity_repair.revised_text)
        runner._on_step(
            "continuity_repair_effect",
            {
                "chapter": chapter_number,
                "pre_score": round(pre_repair_score, 2),
                "pre_issue_count": pre_repair_issue_count,
                "change_ratio": round(change_ratio, 4),
                "word_count_delta": count_chapter_words(continuity_repair.revised_text)
                - count_chapter_words(pre_repair_text),
            },
        )

        if change_ratio > 0.98:
            _logger.info(
                "continuity_repair: minimal change (%.1f%%), might not have addressed issues",
                change_ratio * 100,
            )

    current_text = continuity_repair.revised_text
    runner._storage.save_json(
        bundle.layout.repair_plan_path(chapter_number),
        continuity_repair.repair_plan.model_dump(mode="json"),
    )
    runner._on_step("continuity_repair", continuity_repair)

    return current_text, continuity_repair






def _continuity_recheck_summaries_from_issues(issues: Any, *, limit: int = 12) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    if not isinstance(issues, (list, tuple, set)):
        return summaries
    for issue in list(issues)[:limit]:
        summary = str(
            (issue.get("summary") if isinstance(issue, dict) else getattr(issue, "summary", ""))
            or ""
        ).strip()
        if not summary or summary in seen:
            continue
        seen.add(summary)
        summaries.append(summary[:240])
    return summaries




@dataclass
class ContinuityRepairLoopResult:
    """Result returned by _execute_continuity_repair_loop."""

    current_text: str
    continuity_repair: Any
    alignment_report: Any
    continuity_report: Any
    chapter_repair_report: Any | None
    repair_exhausted: bool
    rounds_used: int = 0
    """Number of repair rounds actually executed (for total rounds cap tracking)."""
    applied: bool = False
    """True if continuity repair left a text change in the returned chapter text."""
    rolled_back: bool = False
    """True if a continuity repair attempt was rejected or rolled back."""

    # === 新增：改进1和改进4支持 ===
    rollback_history: list[dict[str, Any]] = field(default_factory=list)
    """History of rollback attempts with failure context (改进1)."""

    best_effort_accepted: bool = False
    """Whether best-effort acceptance was applied (改进4)."""

    best_effort_reason: str = ""
    """Reason for best-effort acceptance (改进4)."""

    needs_human_review: bool = False
    """Whether this chapter needs human review flag (改进4)."""

    verification_evidence: dict[str, Any] = field(default_factory=dict)
    """Original-validator evidence bound to the returned working candidate."""


class ContinuityRepairRunner(_RepairRoundKernel[ContinuityReport]):
    """Continuity-specific repair loop runner.

    Development note: the active chapter path enters through the v2
    ``run_continuity_repair_v2`` helper, which delegates to
    ``_execute_continuity_repair_loop`` as this domain executor.

    Encapsulates the continuity repair loop with domain-specific hooks:
    - Memory guidance and escalation notes
    - Anchor recalibration
    - Prescreen checks
    - Post-repair checks (alignment, continuity, chapter repair)
    - Memory result recording
    - Banned phrases feedback loop
    - Rollback with retry limit
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
        alignment_report: Any,
        continuity_report: Any,
        chapter_repair_report: Any | None,
        memory_hints: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config, on_step, chapter_number, trace=trace)
        self._runner = runner
        self._bundle = bundle
        self._packet = packet
        self._bridge = bridge
        self._plan = plan
        self._trace = trace
        self._repair_thresholds = repair_thresholds
        self._alignment_report = alignment_report
        self._continuity_report = continuity_report
        self._chapter_repair_report = chapter_repair_report
        self._memory_hints = memory_hints or {}
        self._loop_start_text = ""
        self._pre_repair_text_snapshot = ""
        self._rolled_back = False
        self._recheck_failure_reason: str | None = None

    async def execute_repair(self, ctx: RepairRoundContext[Any]) -> str:
        memory_hints = dict(ctx.memory_hints or self._memory_hints)
        memory_hints["repair_attempt_guidance"] = ctx.extra.get(
            "repair_attempt_guidance"
        ) or build_repair_attempt_guidance(
            domain="continuity",
            round_number=ctx.round_number + 1,
            max_rounds=self._config.max_rounds,
            issues=list(ctx.must_fix_issues),
            previous_issues=ctx.pre_issues,
            current_score=ctx.score,
            score_threshold=self._config.score_threshold,
            previous_score=ctx.previous_score,
        )
        try:
            revised_text, continuity_repair = await run_continuity_repair(
                self._runner,
                self._bundle,
                self._packet,
                self._bridge,
                self._plan,
                self._continuity_report,
                ctx.current_text,
                self._chapter_number,
                self._trace,
                must_fix_issues=ctx.must_fix_issues,
                memory_hints=memory_hints,
                current_alignment_report=self._alignment_report,
            )
            ctx.extra["continuity_repair"] = continuity_repair
            revised_bridge = getattr(continuity_repair, "revised_bridge", None)
            if revised_bridge is not None:
                self._bridge = revised_bridge
            return revised_text
        except Exception as exc:
            # Store failure reason for build_extra_result
            ctx.extra["repair_failure_reason"] = f"{type(exc).__name__}: {exc}"
            raise

    async def evaluate(self, text: str) -> Any:
        try:
            pre_repair_text = self._pre_repair_text_snapshot
            if not pre_repair_text:
                self._on_step(
                    "continuity_repair_pre_snapshot_missing",
                    {
                        "chapter": self._chapter_number,
                        "reason": "pre_repair_text_snapshot_empty",
                    },
                )
                pre_repair_text = text
            (
                alignment_report,
                continuity_report,
                chapter_repair_report,
            ) = await run_post_repair_checks(
                self._runner,
                self._bundle,
                self._packet,
                self._bridge,
                self._plan,
                pre_repair_text,
                text,
                self._chapter_number,
                self._alignment_report,
                self._continuity_report,
                self._chapter_repair_report,
                self._trace,
                memory_hints=self._memory_hints,
                # Skip full 4-critic CriticAgent recheck within the repair loop.
                # Only continuity dimension changed; strengths/character/causal
                # will be re-evaluated at the finalize stage.  This saves 3 LLM
                # calls per repair round (~20K tokens each).
                skip_critic_agent=True,
            )
            self._alignment_report = alignment_report
            self._continuity_report = continuity_report
            self._chapter_repair_report = chapter_repair_report
            return continuity_report
        except Exception as exc:
            # Store recheck failure reason for build_extra_result
            if not self._recheck_failure_reason:
                self._recheck_failure_reason = f"{type(exc).__name__}: {exc}"
            raise

    def extract_issues(self, report: Any) -> list[Any]:
        return list(getattr(report, "issues", []) or [])

    def compute_score(self, report: Any) -> float:
        return float(getattr(report, "continuity_score", 0.0) or 0.0)

    # Helper overrides for ABC _detect_drift (continuity-specific character tracking)
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

    def _get_alignment_score(self) -> float:
        return float(getattr(self._alignment_report, "alignment_score", 7.0) or 7.0)

    def _has_prompt_leaks(self) -> bool:
        return bool(has_prompt_leaks(self._chapter_repair_report))

    async def run(
        self,
        current_text: str,
        initial_report: ContinuityReport,
    ) -> Any:
        """Override run to ensure all paths return ContinuityRepairLoopResult."""
        result = await super().run(current_text=current_text, initial_report=initial_report)
        # Convert to ContinuityRepairLoopResult if not already
        if isinstance(result, ContinuityRepairLoopResult):
            return result
        # Early exits from ABC don't call build_extra_result, so convert here
        return self.build_extra_result(
            RepairRoundContext[ContinuityReport](
                current_text=result.current_text,
                report=result.report,
            ),
            result,
        )

    async def on_round_start(self, ctx: RepairRoundContext[ContinuityReport]) -> None:
        """Pre-round setup: memory guidance and anchor recalibration."""
        memory_hints = dict(ctx.memory_hints or self._memory_hints)

        # Build memory guidance
        episodic_memory = _get_runner_episodic_memory(self._runner)
        memory_guidance = await _build_memory_guidance(
            episodic_memory=episodic_memory,
            current_issues=ctx.issues,
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
            memory_hints["memory_guidance"] = memory_guidance
            self._on_step(
                "continuity_memory_guidance_added",
                {
                    "chapter": self._chapter_number,
                    "round": ctx.round_number + 1,
                    "matched_issues": len(memory_guidance.get("matched_issues", [])),
                    "success_rate": memory_guidance.get("success_rate"),
                    "preferred_strategy": strategy.preferred_strategy,
                },
            )

        # Anchor recalibration (round 1+)
        if ctx.round_number >= 1 and ctx.must_fix_issues:
            from novel_forge.core.utils.patch_utils import recalibrate_issue_anchors
            conf_floor = getattr(
                self._runner._settings, "long_anchor_recalibration_confidence_floor", 0.5
            )
            recal_results = recalibrate_issue_anchors(
                ctx.must_fix_issues,
                ctx.current_text,
                confidence_floor=conf_floor,
            )
            degraded_count = sum(1 for r in recal_results if r["anchor_degraded"])
            if degraded_count > 0:
                self._on_step(
                    "anchor_recalibration",
                    {
                        "chapter": self._chapter_number,
                        "round": ctx.round_number + 1,
                        "total_issues": len(recal_results),
                        "degraded_anchors": degraded_count,
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

        ctx.memory_hints = memory_hints

    async def on_recheck_success(
        self,
        ctx: RepairRoundContext[ContinuityReport],
        new_report: ContinuityReport,
        ledger: Any,
    ) -> None:
        """Post-recheck: banned phrases feedback and memory result recording."""
        # Banned phrases feedback
        for iss in ledger.unresolved:
            if "重复" in iss.issue_type or "模板" in iss.summary or "雷同" in iss.summary:
                phrase = _extract_pattern_from_issue(iss)
                if phrase and phrase not in self._packet.banned_phrases:
                    self._packet.banned_phrases.append(phrase)

        # Memory result recording
        episodic_memory = _get_runner_episodic_memory(self._runner)
        continuity_repair = ctx.extra.get("continuity_repair")
        if continuity_repair and episodic_memory:
            score_before = float(getattr(ctx.report, "continuity_score", 0.0) or 0.0)
            score_after = float(getattr(new_report, "continuity_score", 0.0) or 0.0)
            recorded = _record_continuity_repair_memory_result(
                episodic_memory=episodic_memory,
                chapter_number=self._chapter_number,
                round_num=ctx.round_number + 1,
                pre_issues=ctx.pre_issues,
                continuity_repair=continuity_repair,
                ledger=ledger,
                score_before=score_before,
                score_after=score_after,
            )
            if recorded:
                self._on_step(
                    "continuity_repair_memory_results_recorded",
                    {
                        "chapter": self._chapter_number,
                        "round": ctx.round_number + 1,
                        "count": recorded,
                    },
                )

    def build_extra_result(
        self, ctx: RepairRoundContext[ContinuityReport], result: RepairLoopResult[ContinuityReport]
    ) -> Any:
        """Convert RepairLoopResult to ContinuityRepairLoopResult."""
        continuity_repair = ctx.extra.get("continuity_repair")
        if continuity_repair is None:
            from novel_forge.core.schemas.continuity import RepairPlan
            from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult

            failure_reason = ctx.extra.get("repair_failure_reason") or getattr(
                self, "_recheck_failure_reason", None
            )
            continuity_repair = ContinuityRepairResult(
                revised_text=result.current_text,
                repair_plan=RepairPlan(no_op=True),
                applied=False,
                failure_reason=failure_reason,
            )
        else:
            # Check if repair was rolled back (current text != repaired text)
            repaired_text = getattr(continuity_repair, "revised_text", "")
            if repaired_text and repaired_text != result.current_text:
                # Repair was rolled back, mark as not applied and add failure reason
                failure_reason = getattr(self, "_recheck_failure_reason", None)
                continuity_repair = dataclasses.replace(
                    continuity_repair,
                    applied=False,
                    failure_reason=failure_reason,
                )

        loop_start_text = getattr(self, "_loop_start_text", result.current_text)
        rolled_back = getattr(self, "_rolled_back", False)

        return ContinuityRepairLoopResult(
            current_text=result.current_text,
            continuity_repair=continuity_repair,
            alignment_report=self._alignment_report,
            continuity_report=result.report,
            chapter_repair_report=self._chapter_repair_report,
            repair_exhausted=result.repair_exhausted,
            rounds_used=result.rounds_used,
            applied=bool(getattr(continuity_repair, "applied", False) and result.current_text != loop_start_text),
            rolled_back=rolled_back,
            rollback_history=result.rollback_history,
            best_effort_accepted=result.best_effort_accepted,
            best_effort_reason=result.best_effort_reason,
            needs_human_review=result.needs_human_review,
            verification_evidence=dict(result.extra.get("candidate_verification") or {}),
        )


async def _execute_continuity_repair_loop(
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
    cont_max_rounds: int,
    cont_must_fix_sev: str,
    cont_threshold: float,
    memory_hints: dict[str, Any] | None = None,
) -> ContinuityRepairLoopResult:
    """Run the continuity repair for-loop.

    Delegates to ContinuityRepairRunner which encapsulates the loop logic.
    """
    config = RepairLoopConfig(
        max_rounds=cont_max_rounds,
        must_fix_severity=cont_must_fix_sev,
        score_threshold=cont_threshold,
        change_budget=repair_thresholds.change_budget,
        stagnation_delta=repair_thresholds.stagnation_delta,
        rollback_retry_limit=getattr(runner._settings, "cont_rollback_retry_limit", 2),
        hard_floor=max(
            float(getattr(runner._settings, "long_continuity_hard_block_threshold", 4.0) or 0.0),
            float(getattr(runner._settings, "long_best_effort_accept_floor", 6.0) or 0.0),
        ),
        max_issues_per_round=resolve_max_issues_per_round(
            runner._settings,
            "long_continuity_repair_max_issues_per_round",
        ),
    )

    cont_runner = ContinuityRepairRunner(
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
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=chapter_repair_report,
        memory_hints=memory_hints,
    )

    # Store loop start text for applied calculation
    cont_runner._loop_start_text = current_text
    cont_runner._pre_repair_text_snapshot = current_text
    cont_runner._rolled_back = False

    result = cast(
        ContinuityRepairLoopResult,
        await cont_runner.run(
            current_text=current_text,
            initial_report=continuity_report,
        ),
    )
    return result
