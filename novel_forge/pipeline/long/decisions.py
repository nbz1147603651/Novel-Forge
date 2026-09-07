"""Decision framework for chapter flow orchestration.

This module extracts decision logic from chapter_flow.py into independent,
testable decision classes. Each decision encapsulates a specific judgment
that was previously hardcoded as if/elif/else in the review pipeline.

Design principles:
- Decisions are pure functions of their context (no side effects)
- Each decision returns a typed result enum or dataclass
- All thresholds are injected via context, not hardcoded
- Decisions can be unit-tested without constructing a full pipeline

Decision sections:
- Repair decisions: repair continuation, strategy, dynamic thresholds,
  best-effort acceptance, patch routing, and cross-dimension coordination.
- Quality gate decisions: post-repair recheck policy and final accept/block
  quality gate action.
- Rollback decisions: ledger/drift rollback verdicts and total repair budget.
- Causal regression detection: causal post-repair cross-dimension recheck policy.
- Migrated domain decisions: volume-mode and character stable-text update helpers.

Architecture:
    chapter_flow.py  ──uses──►  decisions.py
                                  ├── Repair decisions
                                  ├── Quality gate decisions
                                  ├── Rollback decisions
                                  ├── Causal regression decisions
                                  └── Migrated domain decisions
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from novel_forge.common.constants import severity_at_least
from novel_forge.core.config import LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT
from novel_forge.core.utils.pipeline_helpers import validate_threshold

_logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepairThresholds:
    """Thresholds governing repair loop behavior.

    All values are injected from Settings at the start of review_chapter_draft.
    """

    change_budget: float = 0.15
    """Max text change ratio per repair round before forcing full recheck."""

    minor_change_skip: float = 0.05
    """Below this ratio + quality ok → skip all post-repair rechecks."""

    moderate_change_skip_chapter_repair: float = 0.10
    """Below this ratio + quality ok → skip only ChapterRepair recheck."""

    causal_minor_change_skip: float = 0.015
    """Causal repairs: stricter skip threshold (plot logic is sensitive)."""

    continuity_score_threshold: float = 8.5
    """Continuity score target for repair exit."""

    must_fix_severity: str = "critical"
    """Severity level at/above which issues block early exit."""

    alignment_threshold: float = 7.0
    """Alignment threshold from config."""

    # Dynamic threshold configuration (new)
    critical_blocks_exit: bool = True
    """If True, critical issues always block exit regardless of score."""

    score_for_high_issues: float = 9.0
    """Higher score threshold when high-severity issues remain."""

    stagnation_delta: float = 0.3
    """Minimum score improvement to continue repair rounds."""

    rollback_delta: float = 0.5
    """Score decrease threshold that triggers rollback."""


@dataclass(frozen=True)
class DynamicRepairPolicy:
    """Per-round repair policy derived from current issue pressure."""

    change_budget: float
    score_threshold: float
    stagnation_delta: float
    issue_pressure: float


# ─── Repair Policy ──────────────────────────────────────────────────────────


class RepairVerdict(enum.Enum):
    """Outcome of a repair continuation decision."""

    CONTINUE = "continue"
    """More rounds needed."""

    COMPLETE = "complete"
    """Score passes and no must-fix issues remain."""

    EXHAUSTED = "exhausted"
    """All allocated rounds used up."""

    ROLLBACK = "rollback"
    """Repair introduced regression; revert to pre-repair text."""

    ROLLBACK_CONTINUE = "rollback_continue"
    """Repair rolled back but allow retry in next round with failure context."""

    STAGNATED = "stagnated"
    """No improvement detected (reserved for future use)."""


@dataclass(frozen=True)
class RepairContext:
    """Snapshot of state needed to decide whether to continue repairing."""

    current_round: int
    """0-based index of the just-completed round."""

    max_rounds: int
    """Maximum repair rounds allowed."""

    score: float
    """Current continuity/causal score after this round."""

    score_threshold: float
    """Target score for early exit."""

    must_fix_issues: tuple[Any, ...] = field(default_factory=tuple)
    """Issues at or above must_fix_severity that remain unresolved."""

    has_regression: bool = False
    """True if the Issue Ledger detected net-negative regression."""

    previous_score: float | None = None
    """Score from the previous round. Used to detect stagnation."""

    memory_guidance: dict[str, Any] | None = None
    """Optional memory-guided repair insights from _build_memory_guidance()."""

    stagnation_delta: float = 0.2
    """Minimum score change to consider as progress. If delta < stagnation_delta, mark as STAGNATED."""


def decide_repair_continuation(ctx: RepairContext) -> RepairVerdict:
    """Unified repair loop exit decision.

    Replaces the scattered if/elif/break logic in both the continuity
    and causal repair loops of chapter_flow.py.

    Uses severity-aware dynamic thresholds:
    - Critical issues always block exit (configurable)
    - High issues raise score threshold to 9.0
    - Medium/low issues use standard threshold
    - Stagnation detection: score delta < stagnation_delta (configurable via RepairContext)
    """
    if ctx.score >= 10.0:
        return RepairVerdict.COMPLETE

    if ctx.has_regression:
        return RepairVerdict.ROLLBACK

    # Severity-aware threshold adjustment
    has_critical = any(
        getattr(iss, "severity", "").lower() == "critical" for iss in ctx.must_fix_issues
    )
    has_high = any(getattr(iss, "severity", "").lower() == "high" for iss in ctx.must_fix_issues)

    # Critical issues: check for stagnation before continuing
    if has_critical:
        is_last_round = ctx.current_round + 1 >= ctx.max_rounds
        if is_last_round:
            return RepairVerdict.EXHAUSTED
        # Stagnation detection for critical issues:
        # If score barely changed and issue count unchanged, mark as stagnated
        if ctx.previous_score is not None and ctx.current_round >= 1:
            score_stagnated = abs(ctx.score - ctx.previous_score) < ctx.stagnation_delta
            if score_stagnated:
                return RepairVerdict.STAGNATED
        return RepairVerdict.CONTINUE

    # Dynamic score threshold based on severity
    if has_high:
        effective_threshold = 9.0  # Higher bar for high-severity issues
    else:
        effective_threshold = ctx.score_threshold

    is_last_round = ctx.current_round + 1 >= ctx.max_rounds
    score_ok = effective_threshold > 0.0 and ctx.score >= effective_threshold
    no_must_fix = not ctx.must_fix_issues

    if score_ok and no_must_fix:
        return RepairVerdict.COMPLETE

    if is_last_round:
        return RepairVerdict.EXHAUSTED

    if (
        ctx.previous_score is not None
        and ctx.current_round >= 1
        and abs(ctx.score - ctx.previous_score) < ctx.stagnation_delta
    ):
        return RepairVerdict.STAGNATED

    return RepairVerdict.CONTINUE


# ─── Memory-Guided Strategy Decision ────────────────────────────────────────


@dataclass(frozen=True)
class StrategyRecommendation:
    """Strategy recommendation based on memory guidance."""

    preferred_strategy: str = "patch"
    """Recommended repair strategy: patch | fulltext | rewrite"""

    reason: str = ""
    """Explanation for the recommendation."""

    confidence: float = 0.5
    """Confidence level 0-1."""

    warning: str = ""
    """Optional warning about potential risks."""


def decide_repair_strategy(ctx: RepairContext) -> StrategyRecommendation:
    """Decide repair strategy based on memory guidance.

    Uses historical repair outcomes to recommend:
    - If a specific strategy historically succeeds for similar issues → recommend it
    - If a strategy historically fails → avoid it
    - If similar issues repeatedly appear across chapters → suggest rewrite

    Args:
        ctx: RepairContext with optional memory_guidance

    Returns:
        StrategyRecommendation with preferred strategy and reasoning
    """
    guidance = ctx.memory_guidance

    if not guidance:
        return StrategyRecommendation(
            preferred_strategy="patch",
            reason="No memory guidance available, defaulting to patch (safest)",
            confidence=0.5,
        )

    success_rate = guidance.get("success_rate", 0.0)
    total_attempts = int(guidance.get("total_attempts", 0) or 0)
    avoid_strategies = guidance.get("avoid_strategies", [])
    recommended = guidance.get("recommended_strategies", [])
    warning = guidance.get("warning", "")

    if total_attempts <= 0:
        return StrategyRecommendation(
            preferred_strategy="patch",
            reason=(
                "Similar historical issues were found, but no repair outcome "
                "history is available; defaulting to patch"
            ),
            confidence=0.45,
            warning=warning if warning else "",
        )

    # Decision logic:
    # 1. If multiple attempts consistently failed → suggest a different approach
    if success_rate < 0.3 and total_attempts >= 3:
        return StrategyRecommendation(
            preferred_strategy="rewrite",
            reason=f"Historical success rate is very low ({success_rate:.0%}), "
            f"suggest completely different approach",
            confidence=0.6,
            warning=warning or "Previous repair attempts had low success rate",
        )

    # 2. If "patch" is in avoid_strategies → suggest fulltext
    avoid_lower = [s.lower() for s in avoid_strategies]
    if any("patch" in s or "局部" in s for s in avoid_lower):
        return StrategyRecommendation(
            preferred_strategy="fulltext",
            reason="Patch strategy historically caused regressions for similar issues",
            confidence=0.7,
            warning=warning or "Avoid patch strategy for this type of issue",
        )

    # 3. If "fulltext" is in recommended and has high success → suggest fulltext
    if success_rate >= 0.6:
        if any("fulltext" in s.lower() or "全文" in s for s in recommended):
            return StrategyRecommendation(
                preferred_strategy="fulltext",
                reason=f"Fulltext strategy has good success rate ({success_rate:.0%}) "
                f"for similar issues",
                confidence=0.8,
                warning=warning if warning else "",
            )

    # 4. Default to patch (safest for most cases)
    if recommended:
        reason = f"Recommended based on history: {recommended[0][:60]}"
    else:
        reason = "Defaulting to patch (safest)"

    return StrategyRecommendation(
        preferred_strategy="patch",
        reason=reason,
        confidence=max(0.5, success_rate),
        warning=warning if warning else "",
    )


# ─── Quality Gate ───────────────────────────────────────────────────────────


class PostRepairAction(enum.Enum):
    """What to do after a repair round regarding quality rechecks."""

    FULL_RECHECK = "full_recheck"
    """Change exceeded budget → run all quality checks."""

    SKIP_ALL_RECHECKS = "skip_all_rechecks"
    """Minor change + quality ok → skip everything."""

    SKIP_CHAPTER_REPAIR_ONLY = "skip_chapter_repair_only"
    """Moderate change + quality ok → skip ChapterRepair, run alignment + continuity."""

    SKIP_NO_OP_REPAIR = "skip_no_op_repair"
    """Repair was no-op and quality passes → skip."""


@dataclass(frozen=True)
class PostRepairContext:
    """Snapshot for post-repair recheck decision."""

    change_ratio: float
    """Text change ratio from this repair round."""

    thresholds: RepairThresholds
    """Injected threshold configuration."""

    alignment_score: float
    """Current alignment score."""

    continuity_score: float
    """Current continuity score."""

    has_prompt_leaks: bool
    """Whether chapter_repair_report has prompt leaks."""

    repair_was_no_op: bool = False
    """True if continuity repair was a no-op (nothing applied)."""

    max_issue_severity: str = "critical"
    """Highest severity among remaining continuity issues (critical/high/medium/low).
    Used to skip expensive rechecks when only low-severity issues remain."""


def decide_post_repair_action(ctx: PostRepairContext) -> PostRepairAction:
    """Determine what quality rechecks are needed after a repair round.

    Replaces the tiered if/elif chain in the continuity repair loop.
    """
    # No-op repair with passing quality → skip everything
    if ctx.repair_was_no_op:
        if ctx.continuity_score >= 10.0:
            return PostRepairAction.SKIP_NO_OP_REPAIR
        if (
            _alignment_passes(ctx.alignment_score, ctx.thresholds.alignment_threshold)
            and not ctx.has_prompt_leaks
        ):
            return PostRepairAction.SKIP_NO_OP_REPAIR

    # Budget exceeded → full recheck mandatory
    if ctx.change_ratio > ctx.thresholds.change_budget:
        return PostRepairAction.FULL_RECHECK

    quality_ok = (
        _alignment_passes(ctx.alignment_score, ctx.thresholds.alignment_threshold)
        and ctx.continuity_score >= ctx.thresholds.continuity_score_threshold
        and not ctx.has_prompt_leaks
    )

    if not quality_ok:
        return PostRepairAction.FULL_RECHECK

    # Quality is ok from here on

    # When the repair only touched low-severity issues and the text change is
    # minor, the expensive post-repair LLM rechecks are very unlikely to
    # surface new problems.  Skip them to save 2–3 LLM calls per chapter.
    _only_low_severity = ctx.max_issue_severity in ("low", "")
    if _only_low_severity and ctx.change_ratio < ctx.thresholds.minor_change_skip:
        return PostRepairAction.SKIP_ALL_RECHECKS

    if ctx.change_ratio < ctx.thresholds.moderate_change_skip_chapter_repair:
        return PostRepairAction.SKIP_CHAPTER_REPAIR_ONLY

    return PostRepairAction.FULL_RECHECK


class QualityGateAction(enum.Enum):
    """Action to take based on chapter evaluation score against minimum threshold."""

    ACCEPT = "accept"
    """Score meets minimum — chapter may proceed."""

    BLOCK_FORCE_REPLAN = "block_force_replan"
    """Score below minimum — block chapter save and force replan."""


def decide_quality_gate_action(
    eval_score: float,
    min_accept_score: float,
) -> QualityGateAction:
    """Determine whether a chapter passes the minimum quality gate.

    Args:
        eval_score: Overall evaluation score (0-10 scale).
        min_accept_score: Minimum acceptable score from Settings.long_min_accept_score.

    Returns:
        QualityGateAction.ACCEPT if score >= min_accept_score,
        QualityGateAction.BLOCK_FORCE_REPLAN otherwise.
    """
    if min_accept_score <= 0.0:
        return QualityGateAction.ACCEPT
    if eval_score >= min_accept_score:
        return QualityGateAction.ACCEPT
    return QualityGateAction.BLOCK_FORCE_REPLAN


# ─── Causal Post-Repair Regression Check ───────────────────────────────────


class CausalRegressionAction(enum.Enum):
    """Whether to run cross-dimension regression checks after causal repair."""

    SKIP = "skip"
    """Minor change + quality ok → no regression check needed."""

    FULL_RECHECK = "full_recheck"
    """Run alignment + continuity + chapter_repair."""

    RECHECK_SKIP_CHAPTER_REPAIR = "recheck_skip_chapter_repair"
    """Run alignment + continuity but skip the slow chapter_repair."""


@dataclass(frozen=True)
class CausalRegressionContext:
    """Context for deciding causal post-repair regression checks."""

    total_change_ratio: float
    thresholds: RepairThresholds
    alignment_score: float
    continuity_score: float
    has_prompt_leaks: bool


def decide_causal_regression_action(ctx: CausalRegressionContext) -> CausalRegressionAction:
    """Decide whether cross-dimension regression checks are needed after causal repair."""
    quality_ok = (
        _alignment_passes(ctx.alignment_score, ctx.thresholds.alignment_threshold)
        and ctx.continuity_score >= ctx.thresholds.continuity_score_threshold
        and not ctx.has_prompt_leaks
    )

    if ctx.total_change_ratio < ctx.thresholds.causal_minor_change_skip and quality_ok:
        return CausalRegressionAction.SKIP

    if ctx.total_change_ratio < ctx.thresholds.moderate_change_skip_chapter_repair and quality_ok:
        return CausalRegressionAction.RECHECK_SKIP_CHAPTER_REPAIR

    return CausalRegressionAction.FULL_RECHECK


# ─── Rollback Decision ──────────────────────────────────────────────────────


class RollbackVerdict(enum.Enum):
    """Whether to rollback a repair round."""

    KEEP = "keep"
    """Changes are acceptable, keep them."""

    ROLLBACK_REGRESSION = "rollback_regression"
    """Issue Ledger detected net-negative regression."""

    ROLLBACK_DRIFT = "rollback_drift"
    """Semantic drift with high-severity signals detected."""


@dataclass(frozen=True)
class DriftContext:
    """Context for semantic drift rollback decision."""

    has_drift: bool
    high_severity_count: int


def decide_rollback(
    *,
    ledger_has_regression: bool = False,
    ledger: Any | None = None,
    drift: DriftContext | None = None,
) -> RollbackVerdict:
    """Unified rollback decision for both continuity and causal repair."""
    if drift is not None and drift.has_drift and drift.high_severity_count > 0:
        return RollbackVerdict.ROLLBACK_DRIFT
    if ledger is not None:
        new_high_critical = list(getattr(ledger, "new_high_critical", []) or [])
        if not new_high_critical:
            return RollbackVerdict.KEEP

        adjusted_resolved = list(getattr(ledger, "adjusted_resolved_high_critical", []) or [])
        resolved_count = len(adjusted_resolved)
        new_count = len(new_high_critical)
        net_count = resolved_count - new_count
        net_weight = int(getattr(ledger, "high_critical_net_weight", 0) or 0)
        effectiveness = float(getattr(ledger, "repair_effectiveness_score", 0.0) or 0.0)
        introduced_critical = any(
            (getattr(issue, "severity", "") or "").lower() == "critical"
            for issue in new_high_critical
        )

        if net_count > 0:
            return RollbackVerdict.KEEP

        if net_count == 0 and net_weight > 0 and not introduced_critical and effectiveness >= 0.15:
            return RollbackVerdict.KEEP

        return RollbackVerdict.ROLLBACK_REGRESSION
    if ledger_has_regression:
        return RollbackVerdict.ROLLBACK_REGRESSION
    return RollbackVerdict.KEEP


# ─── Helper ─────────────────────────────────────────────────────────────────


def _alignment_passes(score: float, threshold: float) -> bool:
    return validate_threshold(score, threshold)


# Tunable weights for derive_dynamic_repair_policy — preserve the inter-
# constant relationships when adjusting (see derive_dynamic_repair_policy).
_STAGNATION_DELTA_FLOOR: float = 0.08
_COUNT_PRESSURE_SATURATION: int = 6
_ROLLBACK_PRESSURE_CAP: int = 2
_ROLLBACK_PRESSURE_PER_ROLLBACK: float = 0.15
_PRESSURE_WEIGHT_MAX: float = 0.5
_PRESSURE_WEIGHT_AVG: float = 0.25
_PRESSURE_WEIGHT_COUNT: float = 0.25
_ISSUE_PRESSURE_CAP: float = 1.0
_STAGNATION_PRESSURE_SENSITIVITY: float = 0.45
_CHANGE_BUDGET_HARD_CAP: float = 0.35
_CHANGE_BUDGET_HEADROOM: float = 0.15
_CHANGE_BUDGET_PRESSURE_GAIN: float = 0.8


def derive_dynamic_repair_policy(
    issues: list[Any],
    *,
    base_change_budget: float,
    base_score_threshold: float,
    base_stagnation_delta: float,
    rollback_history_len: int = 0,
) -> DynamicRepairPolicy:
    """Derive per-round repair policy from issue severity and density."""
    if not issues:
        return DynamicRepairPolicy(
            change_budget=round(base_change_budget, 4),
            score_threshold=round(base_score_threshold, 3),
            stagnation_delta=round(max(_STAGNATION_DELTA_FLOOR, base_stagnation_delta), 3),
            issue_pressure=0.0,
        )

    severity_weights = {
        "critical": 1.0,
        "high": 0.75,
        "medium": 0.45,
        "low": 0.2,
    }
    weights = [
        severity_weights.get((getattr(issue, "severity", "") or "").lower(), 0.25)
        for issue in issues
    ]
    max_pressure = max(weights, default=0.0)
    avg_pressure = sum(weights) / max(len(weights), 1)
    count_pressure = min(len(issues), _COUNT_PRESSURE_SATURATION) / float(
        _COUNT_PRESSURE_SATURATION
    )
    rollback_pressure = (
        min(max(rollback_history_len, 0), _ROLLBACK_PRESSURE_CAP) * _ROLLBACK_PRESSURE_PER_ROLLBACK
    )
    issue_pressure = min(
        _ISSUE_PRESSURE_CAP,
        max_pressure * _PRESSURE_WEIGHT_MAX
        + avg_pressure * _PRESSURE_WEIGHT_AVG
        + count_pressure * _PRESSURE_WEIGHT_COUNT
        + rollback_pressure,
    )

    dynamic_stagnation = max(
        _STAGNATION_DELTA_FLOOR,
        base_stagnation_delta * (1.0 - _STAGNATION_PRESSURE_SENSITIVITY * issue_pressure),
    )
    change_budget_cap = min(
        _CHANGE_BUDGET_HARD_CAP,
        max(base_change_budget, base_change_budget + _CHANGE_BUDGET_HEADROOM),
    )
    dynamic_change_budget = min(
        change_budget_cap,
        max(
            base_change_budget,
            base_change_budget * (1.0 + _CHANGE_BUDGET_PRESSURE_GAIN * issue_pressure),
        ),
    )

    return DynamicRepairPolicy(
        change_budget=round(dynamic_change_budget, 4),
        score_threshold=round(base_score_threshold, 3),
        stagnation_delta=round(dynamic_stagnation, 3),
        issue_pressure=round(issue_pressure, 3),
    )


def build_repair_thresholds(
    settings: Any,
    config: Any,
) -> RepairThresholds:
    """Construct RepairThresholds from Settings + ChapterRunnerConfig.

    Centralizes threshold resolution from the two config sources.
    """
    return RepairThresholds(
        change_budget=getattr(settings, "change_budget_threshold", 0.15),
        minor_change_skip=getattr(settings, "minor_change_skip_recheck_ratio", 0.05),
        moderate_change_skip_chapter_repair=getattr(
            settings, "moderate_change_skip_chapter_repair_ratio", 0.10
        ),
        causal_minor_change_skip=getattr(settings, "causal_minor_change_skip_recheck_ratio", 0.015),
        continuity_score_threshold=getattr(settings, "long_continuity_repair_threshold", 8.5),
        must_fix_severity=(
            getattr(settings, "repair_must_fix_severity", "critical") or "critical"
        ).lower(),
        alignment_threshold=getattr(config, "alignment_threshold", 7.0),
    )


def filter_must_fix_issues(
    issues: list[Any],
    must_fix_severity: str,
) -> list[Any]:
    """Filter issues at or above the must-fix severity level."""
    if must_fix_severity == "off":
        return []
    return [
        iss
        for iss in issues
        if severity_at_least(getattr(iss, "severity", "").lower(), must_fix_severity)
    ]


def filter_issues_by_round(
    issues: list[Any],
    current_round: int,
    has_rollback_history: bool = False,
    max_rounds: int = 3,
) -> list[Any]:
    """Progressively filter issues to repair based on round number and history.

    Strategy (双层渐进式):

    Round 0 (first):
      - All critical + high issues via patch (精确局部修复)
      - Patch is safer: only modifies targeted paragraphs

    Round 1+ (with rollback history):
      - Only critical issues via patch (进一步缩小范围)
      - Avoid introducing new issues by minimal changes

    Final round:
      - All remaining issues via fulltext (全局重写作为最后手段)
      - When patch strategy exhausted, accept higher risk for complete fix

    This reduces regression risk by:
    1. Starting with safest repair method (patch)
    2. Narrowing scope after rollback
    3. Only using fulltext as last resort
    """
    if not issues:
        return []

    is_final_round = current_round >= max_rounds - 1

    # Final round: use fulltext for all remaining issues
    if is_final_round:
        return issues

    # After rollback: narrow scope to only critical issues (patch mode)
    if has_rollback_history:
        return [iss for iss in issues if getattr(iss, "severity", "").lower() in ("critical",)]

    # First round: repair critical + high via patch
    return [iss for iss in issues if getattr(iss, "severity", "").lower() in ("critical", "high")]


# ─── Best Effort Accept Decision ────────────────────────────────────────────


@dataclass(frozen=True)
class BestEffortVerdict:
    """Result of best-effort acceptance decision."""

    should_accept: bool
    """Whether to accept the current best-effort result."""

    reason: str = ""
    """Explanation for the decision."""


@dataclass(frozen=True)
class BestEffortContext:
    """Context for best-effort acceptance decision."""

    original_score: float
    """Original continuity score before any repairs."""

    current_score: float
    """Current continuity score after repair attempts."""

    original_critical_count: int
    """Number of critical issues in the original draft."""

    current_critical_count: int
    """Number of critical issues remaining after repairs."""

    alignment_score: float
    """Current alignment score."""

    has_prompt_leaks: bool
    """Whether there are prompt leaks."""


def decide_best_effort_accept(
    ctx: BestEffortContext,
    *,
    hard_floor: float = 6.0,
) -> BestEffortVerdict:
    """Decide whether to accept a best-effort result after repair exhaustion.

    This prevents triggering a full chapter replan when repairs have made
    genuine progress but couldn't reach the target threshold.

    All conditions must be met to accept:
    - current_score > original_score (substantial improvement)
    - current_score >= original_score * 0.7 (not below 70% of original)
    - current_score >= hard_floor (absolute minimum floor, default 6.0)
    - alignment_score >= 7.0 (alignment threshold unchanged)
    - no prompt_leaks (safety red line)
    - current_critical_count <= original_critical_count (no new critical issues)
    """
    # Hard floor: never accept a score below the absolute minimum
    if ctx.current_score < hard_floor:
        return BestEffortVerdict(
            should_accept=False,
            reason=f"Current score {ctx.current_score} below hard floor {hard_floor} — quality too low to accept",
        )

    # Safety red lines: never accept with prompt leaks or poor alignment
    if ctx.has_prompt_leaks:
        return BestEffortVerdict(
            should_accept=False,
            reason="Prompt leaks detected — safety red line",
        )

    if ctx.alignment_score < 7.0:
        return BestEffortVerdict(
            should_accept=False,
            reason=f"Alignment score {ctx.alignment_score} below threshold 7.0",
        )

    # Must show substantial improvement
    if ctx.current_score <= ctx.original_score:
        return BestEffortVerdict(
            should_accept=False,
            reason=f"Current score {ctx.current_score} not better than original {ctx.original_score}",
        )

    # Must not degrade below 70% of original (safety net)
    if ctx.current_score < ctx.original_score * 0.7:
        return BestEffortVerdict(
            should_accept=False,
            reason=f"Current score {ctx.current_score} below 70% of original {ctx.original_score}",
        )

    # Must not introduce new critical issues
    if ctx.current_critical_count > ctx.original_critical_count:
        return BestEffortVerdict(
            should_accept=False,
            reason=f"Critical issues increased from {ctx.original_critical_count} to {ctx.current_critical_count}",
        )

    # All conditions met — accept with flag for human review
    return BestEffortVerdict(
        should_accept=True,
        reason=(
            f"Improvement from {ctx.original_score} to {ctx.current_score}, "
            f"critical issues: {ctx.original_critical_count} → {ctx.current_critical_count}"
        ),
    )


# ─── Cross-Dimension Coordination ───────────────────────────────────────────


class CrossDimensionAction(str, enum.Enum):
    """Action to take for downstream repair dimensions based on upstream result."""

    CONTINUE_NORMAL = "continue_normal"
    """Upstream succeeded — downstream can proceed with normal intensity."""

    REDUCE_SCOPE = "reduce_scope"
    """Upstream exhausted but best-effort accepted — downstream should reduce rounds."""

    SKIP_NON_CRITICAL = "skip_non_critical"
    """Upstream failed badly — skip downstream non-critical repairs, keep critical only."""


@dataclass(frozen=True)
class CrossDimensionContext:
    """Context for cross-dimension coordination decision."""

    upstream_repair_exhausted: bool
    """Whether the upstream repair loop (e.g. continuity) exhausted."""

    upstream_best_effort_accepted: bool
    """Whether upstream was accepted via best-effort."""

    upstream_score: float
    """Current upstream score after repair attempts."""

    upstream_hard_floor: float
    """Hard floor threshold for upstream dimension."""

    downstream_dimension: str
    """Name of downstream dimension (for logging)."""


# ─── Patch Routing Decision ─────────────────────────────────────────────────

# Known patchable issue types (location determined, no full-text awareness needed)
PATCHABLE_TYPES = frozenset(
    {
        "opening_gap",
        "location_jump",
        "pov_jump",
        "time_marker_invalid",
        "closing_gap",
        "ending_ambiguity",
        "bridge_contract_not_followed",
        "custody_break",
        "carry_forward_missing",
        "redundancy",
        "text_repetition",
        "sensory_anchor_repetition",
        "forbidden_element_violation",
        "forbidden_element_usage",
        "address_form_mismatch",
        "character_not_in_plan",
        "pov_intrusion",
        "prompt_leak",
    }
)

# Boundary issues: allow window patch even when rewrite_scope=chapter
BOUNDARY_PATCHABLE_TYPES = frozenset(
    {
        "opening_gap",
        "closing_gap",
        "ending_ambiguity",
        "bridge_contract_not_followed",
    }
)

# Scopes that indicate full-chapter or structural rewrites (never patch)
UNPATCHABLE_SCOPES = frozenset({"chapter", "全章", "全文", ""})


def decide_patch_routing(
    issue_type: str,
    scope: str,
    fix_mode: str = "",
    evidence: str = "",
    paragraph_start: int = 0,
    location_confidence: float = 0.0,
) -> bool:
    """Decide whether an issue can be fixed with a localized patch vs fulltext rewrite.

    Routing strategy priority:
    1. fix_mode="fulltext" → always fulltext
    2. Scope guard: structural/full-chapter issues → fulltext (except boundary types)
    3. Known patchable types → patch
    4. Evidence-based fallback: clear local scope with sufficient evidence → patch
    5. Paragraph anchor fallback: high-confidence location → patch
    6. Otherwise → fulltext

    Returns:
        True if issue should be patched, False if it needs fulltext repair.
    """
    _logger.debug(
        "patch_routing: evaluate | issue_type=%s | scope=%s | fix_mode=%s",
        issue_type,
        scope,
        fix_mode,
    )

    # Strategy 1: Explicit fulltext mode
    if fix_mode == "fulltext":
        _logger.debug("patch_routing: skip | reason=fix_mode_fulltext")
        return False

    # Strategy 2: Scope guard — structural issues must not be patched
    if scope in UNPATCHABLE_SCOPES:
        if issue_type in BOUNDARY_PATCHABLE_TYPES:
            _logger.debug("patch_routing: boundary_patch | scope=%s", scope)
            return True
        _logger.debug("patch_routing: skip | reason=unpatchable_scope | scope=%s", scope)
        return False

    # Strategy 3: Known patchable types
    if issue_type in PATCHABLE_TYPES:
        _logger.debug("patch_routing: patchable")
        return True

    # Strategy 4: Evidence-based fallback
    if scope and len(evidence.strip()) >= 6:
        _logger.debug("patch_routing: evidence_fallback | evidence_len=%d", len(evidence.strip()))
        return True

    # Strategy 5: Paragraph anchor fallback
    has_para_anchor = paragraph_start > 0
    effective_confidence = location_confidence if has_para_anchor else 0.0
    if has_para_anchor and (effective_confidence == 0.0 or effective_confidence >= 0.65):
        _logger.debug("patch_routing: para_anchor | confidence=%.2f", effective_confidence)
        return True

    # Strategy 6: Default to fulltext
    _logger.debug(
        "patch_routing: fallback_to_fulltext | scope=%s | evidence_len=%d | "
        "para_anchor=%s | confidence=%.2f",
        scope,
        len(evidence.strip()),
        has_para_anchor,
        effective_confidence,
    )
    return False


def decide_cross_dimension_action(
    ctx: CrossDimensionContext,
) -> CrossDimensionAction:
    """Decide how downstream repair dimensions should behave based on upstream result.

    When continuity repair fails without best-effort acceptance, causal and
    reading-power repairs are unlikely to succeed either.  This decision
    reduces wasted LLM calls by lowering downstream intensity.
    """
    if not ctx.upstream_repair_exhausted:
        return CrossDimensionAction.CONTINUE_NORMAL

    if ctx.upstream_best_effort_accepted:
        return CrossDimensionAction.REDUCE_SCOPE

    if ctx.upstream_score < ctx.upstream_hard_floor:
        return CrossDimensionAction.SKIP_NON_CRITICAL

    return CrossDimensionAction.REDUCE_SCOPE


def resolve_total_repair_rounds_cap(settings: Any) -> int:
    """Resolve the absolute repair-round cap with the same default as Settings."""
    try:
        value = getattr(
            settings,
            "long_total_repair_rounds_cap",
            LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT,
        )
        if value is None:
            return LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT
        return int(value)
    except (TypeError, ValueError):
        return LONG_TOTAL_REPAIR_ROUNDS_CAP_DEFAULT


# ---------------------------------------------------------------------------
# Volume-mode decision (migrated from services/outline_helpers.py)
# ---------------------------------------------------------------------------


def should_use_volume_mode(
    *,
    volume_mode: Literal["auto", "on", "off"],
    total_chapters: int,
    expected_total_words: int,
    chapter_threshold: int,
    word_threshold: int,
) -> bool:
    """Decide whether to enable volume mode based on explicit setting or thresholds."""
    if volume_mode == "on":
        return True
    if volume_mode == "off":
        return False
    return total_chapters >= chapter_threshold or expected_total_words >= word_threshold


# ---------------------------------------------------------------------------
# Character stable-text update decision (migrated from services/character_intro_policy.py)
# ---------------------------------------------------------------------------


def should_update_stable_text(existing: Any, incoming: Any) -> bool:
    """Conservatively update stable identity fields only when more specific."""
    from novel_forge.pipeline.long.services.character_intro_policy import (
        usable_profile_text,
    )

    old = usable_profile_text(existing)
    new = usable_profile_text(incoming)
    if not new:
        return False
    if not old:
        return True
    return len(new) >= len(old) + 6 and old not in new
