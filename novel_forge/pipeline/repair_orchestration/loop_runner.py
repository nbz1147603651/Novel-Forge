"""Repair Orchestration v2 loop engine for active chapter repair handlers.

Three active chapter-repair executors in Novel Forge share most of their loop
structure:
- ``_execute_continuity_repair_loop`` in ``stages/continuity_repair.py``
- ``_execute_causal_repair_loop`` in ``stages/causal_repair.py``
- ``_execute_reading_power_repair_loop`` in ``stages/reading_power_repair.py``

This module owns the common loop semantics still used by continuity, causal,
and reading-power handlers while those domain stages are being migrated behind
Repair Orchestration v2. Subclasses implement domain-specific hooks while
inheriting shared loop logic: dedup, drift detection, change budget, issue
ledger, rollback, and best-effort acceptance.

Common loop structure (per round):
1. Pre-round snapshot (save current_text)
2. Issue filtering (filter_issues_by_round / filter_must_fix_issues)
3. Repair step execution (domain-specific)
4. Intermediate dedup (run_self_repetition_check)
5. Semantic drift detection (detect_drift + decide_rollback)
6. Change budget check (_text_change_ratio + threshold)
7. Re-evaluation + Issue ledger diff (diff_issues) + Repair continuation decision
8. Best-effort acceptance at exhaustion (decide_best_effort_accept)

Usage::

    class MyRepairRunner(_RepairRoundKernel[MyReport]):
        async def execute_repair(self, ctx: RepairRoundContext[MyReport]) -> str: ...
        async def evaluate(self, text: str) -> tuple[float, list[Any]]: ...
        def extract_issues(self, report: MyReport) -> list[Any]: ...
        def compute_score(self, report: MyReport) -> float: ...

    runner = MyRepairRunner(...)
    result = await runner.run(current_text="...", initial_report=report, ...)
"""

from __future__ import annotations

import abc
import asyncio
import inspect
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar, cast

from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.core.utils.issue_ledger import diff_issues
from novel_forge.core.utils.semantic_drift import detect_drift
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.decisions import (
    BestEffortContext,
    DriftContext,
    RepairContext,
    RepairVerdict,
    RollbackVerdict,
    decide_best_effort_accept,
    decide_repair_continuation,
    decide_rollback,
    derive_dynamic_repair_policy,
    filter_issues_by_round,
    filter_must_fix_issues,
)
from novel_forge.pipeline.long.human_decision import (
    HumanDecisionOption,
    HumanDecisionRequest,
    request_human_decision,
)
from novel_forge.pipeline.long.repair_safety import RepairFailurePolicy, RepairRoundSnapshot
from novel_forge.pipeline.repair_orchestration.audit_events import (
    build_repair_audit_summary,
    emit_repair_audit_event,
    emit_repair_audit_summary,
    issue_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.loop_components import RepairLoopExecutor
from novel_forge.pipeline.repair_orchestration.text_utils import text_change_ratio

ReportT = TypeVar("ReportT")
"""Generic type variable for domain-specific report types."""

_logger = get_logger("pipeline.repair_orchestration.loop_runner")


def resolve_max_issues_per_round(
    settings: Any,
    specific_key: str,
    *,
    default: int = 1,
) -> int:
    """Resolve the per-round repair focus cap from stage-specific or global settings."""

    raw = getattr(settings, specific_key, None)
    if raw is None:
        raw = getattr(settings, "long_repair_max_issues_per_round", default)
    try:
        return max(0, int(raw if raw is not None else default))
    except (TypeError, ValueError):
        return max(0, int(default))


# ─── Configuration ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepairLoopConfig:
    """Configuration for a repair loop instance.

    All thresholds are injected at construction time, never hardcoded.
    """

    max_rounds: int = 3
    """Maximum number of repair rounds."""

    change_budget: float = 0.15
    """Max text change ratio per round before forcing rollback."""

    must_fix_severity: str = "critical"
    """Severity level at/above which issues block early exit."""

    score_threshold: float = 8.5
    """Target score for repair exit."""

    hard_floor: float = 6.0
    """Absolute minimum score for best-effort acceptance."""

    stagnation_delta: float = 0.3
    """Minimum score improvement to consider as progress."""

    enabled: bool = True
    """Whether this repair loop is enabled."""

    rollback_retry_limit: int = 2
    """Max rollback retries before exiting (continuity-specific)."""

    rollback_enabled: bool = True
    """Whether drift/regression rollbacks are allowed."""

    recheck_enabled: bool = True
    """Whether to run the domain recheck after each repair round."""

    max_execution_seconds: float = 600.0
    """Safety-net timeout for the whole loop. Non-positive disables it."""

    max_issues_per_round: int = 0
    """Maximum must-fix issues sent to one repair call. Non-positive disables the cap."""


# ─── Round Context ───────────────────────────────────────────────────────────


@dataclass
class RepairRoundContext(Generic[ReportT]):
    """Mutable context passed through each repair round.

    This is NOT frozen — fields are updated in-place by the loop.
    Use ``dataclasses.replace()`` only when creating snapshots.
    """

    round_number: int = 0
    """0-based round index."""

    current_text: str = ""
    """Current chapter text (mutated by repair steps)."""

    pre_round_text: str = ""
    """Text snapshot before this round's repair."""

    report: ReportT | None = None
    """Current domain-specific report."""

    issues: list[Any] = field(default_factory=list)
    """Current list of issues to fix."""

    must_fix_issues: list[Any] = field(default_factory=list)
    """Issues at or above must_fix_severity."""

    score: float = 0.0
    """Current score from the report."""

    previous_score: float | None = None
    """Score from the previous round."""

    pre_issues: list[Any] = field(default_factory=list)
    """Issues snapshot before this round (for ledger diff)."""

    issue_attempts: dict[str, int] = field(default_factory=dict)
    """Per-issue attempt tracking (signature -> count)."""

    stagnation_count: int = 0
    """Consecutive rounds with insufficient improvement."""

    # ── Continuity-specific fields ──
    rollback_history: list[dict[str, Any]] = field(default_factory=list)
    """History of rollback attempts (continuity-specific)."""

    memory_hints: dict[str, Any] = field(default_factory=dict)
    """Memory guidance hints (continuity-specific)."""

    # ── Reading-power-specific fields ──
    any_applied: bool = False
    """Whether any repair was applied (reading-power-specific)."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Domain-specific extra data (continuity_repair, etc.)."""


# ─── Checkpoint ──────────────────────────────────────────────────────────────


@dataclass
class RepairLoopCheckpoint:
    """In-memory checkpoint for a repair loop round."""

    stage: str
    round_number: int
    current_text: str
    report_data: dict[str, Any] = field(default_factory=dict)
    issues_snapshot: list[dict[str, Any]] = field(default_factory=list)
    rollback_history: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


# ─── Result ──────────────────────────────────────────────────────────────────


@dataclass
class RepairLoopResult(Generic[ReportT]):
    """Result returned by a repair loop.

    Domain-specific subclasses can add extra fields.
    """

    current_text: str
    """Final chapter text after all rounds."""

    report: ReportT | None
    """Final domain-specific report."""

    repair_exhausted: bool = False
    """True if all rounds used without reaching target."""

    rounds_used: int = 0
    """Number of repair rounds actually executed."""

    best_effort_accepted: bool = False
    """Whether best-effort acceptance was applied."""

    best_effort_reason: str = ""
    """Reason for best-effort acceptance."""

    needs_human_review: bool = False
    """Whether this chapter needs human review flag."""

    # ── Continuity-specific ──
    rollback_history: list[dict[str, Any]] = field(default_factory=list)
    """History of rollback attempts (continuity-specific)."""

    # ── Extra domain data ──
    extra: dict[str, Any] = field(default_factory=dict)
    """Domain-specific extra data (causal_warnings, alignment_report, etc.)."""


# ─── Abstract Base Runner ────────────────────────────────────────────────────


class _RepairRoundKernel(abc.ABC, Generic[ReportT]):
    """Generic repair loop runner.

    Subclasses implement 4 abstract hooks:
    - ``execute_repair``: domain-specific repair step
    - ``evaluate``: domain-specific evaluation
    - ``extract_issues``: extract issues from report
    - ``compute_score``: compute score from report

    Optional hooks for domain-specific behavior:
    - ``on_round_start``: pre-round setup (memory guidance, anchor recalibration)
    - ``on_repair_success``: post-repair hooks (prescreen, memory recording)
    - ``on_recheck_success``: post-recheck hooks (ledger extras, banned phrases)
    - ``on_loop_exit``: post-loop hooks (cross-dimension regression, warnings)
    - ``build_extra_result``: add domain-specific fields to result
    - ``get_original_score``: get original score for best-effort comparison
    - ``get_original_critical_count``: get original critical count
    - ``issue_signature``: compute issue fingerprint for dedup
    - ``filter_issues_for_round``: domain-specific issue filtering
    - ``repair_skip_precheck``: early-exit check before loop starts
    """

    def __init__(
        self,
        config: RepairLoopConfig,
        on_step: Callable[[str, Any], None],
        chapter_number: int,
        trace: Any | None = None,
    ) -> None:
        self._config = config
        self._on_step = on_step
        self._chapter_number = chapter_number
        self._trace = trace
        self._logger = get_logger(f"pipeline.{self.__class__.__name__.lower()}")
        self._checkpoint: RepairLoopCheckpoint | None = None
        self._loop_executor = RepairLoopExecutor[ReportT](self)

    # ── Abstract hooks (must implement) ──

    @abc.abstractmethod
    async def execute_repair(self, ctx: RepairRoundContext[ReportT]) -> str:
        """Execute domain-specific repair step.

        Args:
            ctx: Current round context with text, issues, must_fix_issues, etc.

        Returns:
            Revised chapter text.

        Any exception raised here is classified and handled by
        ``RepairFailurePolicy`` in ``run``.
        """

    @abc.abstractmethod
    async def evaluate(self, text: str) -> ReportT:
        """Run domain-specific evaluation on text.

        Args:
            text: Chapter text to evaluate.

        Returns:
            Domain-specific report.

        Any exception raised here is classified and handled by
        ``RepairFailurePolicy`` in ``run``.
        """

    @abc.abstractmethod
    def extract_issues(self, report: ReportT) -> list[Any]:
        """Extract issues list from a report.

        Args:
            report: Domain-specific report.

        Returns:
            List of issue objects.
        """

    @abc.abstractmethod
    def compute_score(self, report: ReportT) -> float:
        """Compute numeric score from a report.

        Args:
            report: Domain-specific report.

        Returns:
            Score value (typically 0-10).
        """

    # ── Optional hooks (override as needed) ──

    async def on_round_start(self, ctx: RepairRoundContext[ReportT]) -> None:
        """Pre-round setup hook. Override for memory guidance, anchor recalibration, etc.

        Default: no-op.
        """

    async def on_repair_success(self, ctx: RepairRoundContext[ReportT], revised_text: str) -> str:
        """Post-repair hook called after successful repair but before dedup.

        Override for prescreen checks, memory recording, etc.
        Return the (possibly modified) revised text.
        Return None or raise to signal prescreen failure (text will be rolled back).

        Default: return revised_text unchanged.
        """
        del ctx
        return revised_text

    async def on_recheck_success(
        self,
        ctx: RepairRoundContext[ReportT],
        new_report: ReportT,
        ledger: Any,
    ) -> None:
        """Post-recheck hook called after issue ledger diff.

        Override for memory result recording, banned phrase feedback, etc.

        Default: no-op.
        """

    async def on_loop_exit(self, ctx: RepairRoundContext[ReportT]) -> None:
        """Post-loop hook called after the main loop exits.

        Override for cross-dimension regression checks, warnings, etc.

        Default: no-op.
        """

    def build_extra_result(
        self, ctx: RepairRoundContext[ReportT], result: RepairLoopResult[ReportT]
    ) -> RepairLoopResult[ReportT]:
        """Add domain-specific fields to the result.

        Override to populate extra dict or subclass-specific fields.

        Default: return result unchanged.
        """
        return result

    def get_original_score(self, initial_report: ReportT) -> float:
        """Get original score for best-effort comparison.

        Default: compute_score(initial_report).
        """
        return self.compute_score(initial_report)

    def get_original_critical_count(self, initial_report: ReportT) -> int:
        """Get original critical issue count for best-effort comparison.

        Default: count issues with severity == 'critical'.
        """
        return sum(
            1
            for i in self.extract_issues(initial_report)
            if getattr(i, "severity", "").lower() == "critical"
        )

    def issue_signature(self, issue: Any) -> str:
        """Compute a unique fingerprint for an issue.

        Prefer ``issue_id`` when available (stable across repair rounds even if
        the LLM rephrases the summary).  Fall back to a content-based signature
        for older issue shapes that lack an ID.
        """
        issue_id = str(self._issue_value(issue, "issue_id", "") or "").strip()
        if issue_id:
            return f"id:{issue_id}"
        return self._content_signature(issue)

    def _content_signature(self, issue: Any) -> str:
        """Content-based fallback signature (issue_type:summary)."""
        return f"{self._issue_value(issue, 'issue_type')}:{self._issue_value(issue, 'summary')}"

    def _issue_value(self, issue: Any, key: str, default: Any = "") -> Any:
        if isinstance(issue, dict):
            return issue.get(key, default)
        return getattr(issue, key, default)

    def _issue_severity_rank(self, issue: Any) -> int:
        severity = str(self._issue_value(issue, "severity", "") or "").lower()
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(severity, 4)

    def _issue_has_location_signal(self, issue: Any) -> bool:
        for key in (
            "paragraph_start",
            "paragraph_end",
            "location",
            "evidence",
            "evidence_quote",
            "anchor_text",
            "quote",
            "issue_id",
            "ticket_id",
        ):
            value = self._issue_value(issue, key, "")
            if value not in ("", None, [], ()):
                return True
        return False

    def _focus_issues_for_round(
        self,
        issues: list[Any],
        ctx: RepairRoundContext[ReportT],
    ) -> list[Any]:
        limit = int(self._config.max_issues_per_round or 0)
        if limit <= 0 or len(issues) <= limit:
            return issues
        ranked = sorted(
            enumerate(issues),
            key=lambda item: (
                self._issue_severity_rank(item[1]),
                0 if self._issue_has_location_signal(item[1]) else 1,
                int(ctx.issue_attempts.get(self.issue_signature(item[1]), 0) or 0),
                item[0],
            ),
        )
        selected_pairs = ranked[:limit]
        skipped_pairs = ranked[limit:]
        selected = [issue for _, issue in selected_pairs]
        self._on_step(
            "repair_round_focus",
            {
                "chapter": self._chapter_number,
                "dimension": self._stage_name(),
                "round": ctx.round_number + 1,
                "max_issues_per_round": limit,
                "selected_count": len(selected_pairs),
                "skipped_count": len(skipped_pairs),
                "selected_issues": [self._serialize_issue(issue) for _, issue in selected_pairs],
                "skipped_issue_signatures": [
                    self.issue_signature(issue) for _, issue in skipped_pairs[:8]
                ],
            },
        )
        return selected

    def filter_issues_for_round(
        self,
        issues: list[Any],
        current_round: int,
        ctx: RepairRoundContext[ReportT],
    ) -> list[Any]:
        """Filter issues based on round number.

        Default: use filter_issues_by_round from decisions.py.
        Override for domain-specific filtering (e.g., reading-power uses custom logic).
        """
        has_rollback = len(ctx.rollback_history) > 0
        return filter_issues_by_round(
            issues,
            current_round=current_round,
            has_rollback_history=has_rollback,
            max_rounds=self._config.max_rounds,
        )

    def _promote_issues_below_hard_floor(
        self,
        issues: list[Any],
        must_fix_issues: list[Any],
        score: float,
    ) -> list[Any]:
        """Treat all surfaced issues as blocking when score is below the hard floor."""

        if must_fix_issues or not issues or score >= self._config.hard_floor:
            return must_fix_issues
        self._logger.info(
            "score %.2f below hard_floor %.2f, promoting all %d issues to must-fix",
            score,
            self._config.hard_floor,
            len(issues),
        )
        return list(issues)

    def repair_skip_precheck(
        self, initial_report: ReportT, ctx: RepairRoundContext[ReportT]
    ) -> tuple[bool, str]:
        """Check if repair should be skipped before entering the loop.

        Returns:
            (skip, reason) tuple.

        Default: no skip.
        """
        legacy_hook = self.__class__.__dict__.get("should_skip_repair")
        if legacy_hook is not None:
            return cast(tuple[bool, str], legacy_hook(self, initial_report, ctx))
        del initial_report, ctx
        return False, ""

    should_skip_repair = repair_skip_precheck

    def _text_change_ratio(self, before: str, after: str) -> float:
        return text_change_ratio(before, after)

    # ── Checkpoint and timeout safety ──

    def save_checkpoint(self, stage: str, ctx: RepairRoundContext[ReportT]) -> None:
        """Save a lightweight in-memory checkpoint for rollback and diagnostics."""
        report_data: dict[str, Any] = {}
        if ctx.report is not None:
            if hasattr(ctx.report, "model_dump"):
                report_data = ctx.report.model_dump(mode="json")
            elif hasattr(ctx.report, "__dict__"):
                report_data = dict(ctx.report.__dict__)

        self._checkpoint = RepairLoopCheckpoint(
            stage=stage,
            round_number=ctx.round_number,
            current_text=ctx.current_text,
            report_data=report_data,
            issues_snapshot=[self._serialize_issue(issue) for issue in ctx.issues],
            rollback_history=list(ctx.rollback_history),
            extra=dict(ctx.extra),
        )

    def restore_from_checkpoint(self) -> RepairRoundContext[ReportT] | None:
        """Restore the last in-memory checkpoint, if one exists."""
        if self._checkpoint is None:
            return None
        return RepairRoundContext[ReportT](
            round_number=self._checkpoint.round_number,
            current_text=self._checkpoint.current_text,
            pre_round_text=self._checkpoint.current_text,
            issues=list(self._checkpoint.issues_snapshot),
            pre_issues=list(self._checkpoint.issues_snapshot),
            rollback_history=list(self._checkpoint.rollback_history),
            extra=dict(self._checkpoint.extra),
        )

    def cleanup_partial_state(self, ctx: RepairRoundContext[ReportT]) -> str:
        """Return the safest text after an interrupted or timed-out round."""
        if ctx.pre_round_text:
            return ctx.pre_round_text
        if self._checkpoint is not None:
            return self._checkpoint.current_text
        return ctx.current_text

    def _serialize_issue(self, issue: Any) -> dict[str, Any]:
        if isinstance(issue, dict):
            return dict(issue)
        if hasattr(issue, "model_dump"):
            dumped = issue.model_dump(mode="json")
            return dict(dumped) if isinstance(dumped, dict) else {}
        return {
            "issue_type": getattr(issue, "issue_type", ""),
            "severity": getattr(issue, "severity", ""),
            "summary": getattr(issue, "summary", ""),
        }

    def _audit_surface(self) -> str:
        """Return the repair surface label used by unified audit events."""

        return "chapter_text"

    def _remember_audit_issue_id(
        self,
        ctx: RepairRoundContext[ReportT],
        bucket: str,
        issue_id: str,
    ) -> None:
        if not issue_id:
            return
        values = ctx.extra.setdefault(bucket, [])
        if isinstance(values, list) and issue_id not in values:
            values.append(issue_id)

    def _audit_issue_ids(self, issues: list[Any], ctx: RepairRoundContext[ReportT]) -> list[str]:
        issue_ids: list[str] = []
        for issue in issues:
            try:
                payload = issue_audit_payload(
                    issue,
                    event_type="repair_audit_identity",
                    dimension=self._stage_name(),
                    surface=self._audit_surface(),
                    chapter=self._chapter_number,
                    round_number=ctx.round_number + 1 if ctx.round_number >= 0 else 0,
                )
            except Exception:
                continue
            issue_id = str(payload.get("issue_id") or "")
            if issue_id and issue_id not in issue_ids:
                issue_ids.append(issue_id)
        return issue_ids

    def _emit_issue_audit_event(
        self,
        event_type: str,
        issue: Any,
        ctx: RepairRoundContext[ReportT],
        *,
        status: str,
        repair_action: str = "",
        source_text_hash: str = "",
        target_text_hash: str = "",
        failure_kind: str = "",
        fallback_action: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Emit a canonical issue audit event and return the issue id."""

        try:
            payload = issue_audit_payload(
                issue,
                event_type=event_type,
                dimension=self._stage_name(),
                surface=self._audit_surface(),
                chapter=self._chapter_number,
                round_number=ctx.round_number + 1 if ctx.round_number >= 0 else 0,
                status=status,
                repair_action=repair_action,
                source_text_hash=source_text_hash,
                target_text_hash=target_text_hash,
                failure_kind=failure_kind,
                fallback_action=fallback_action,
                extra=extra,
            )
            emit_repair_audit_event(self._on_step, payload)
            return str(payload.get("issue_id") or "")
        except Exception as exc:
            self._logger.debug(
                "failed to emit repair audit event | stage=%s | event_type=%s | error=%s",
                self._stage_name(),
                event_type,
                exc,
            )
            return ""

    def _emit_issues_audit_event(
        self,
        event_type: str,
        issues: list[Any],
        ctx: RepairRoundContext[ReportT],
        *,
        status: str,
        repair_action: str = "",
        bucket: str = "",
        source_text_hash: str = "",
        target_text_hash: str = "",
        failure_kind: str = "",
        fallback_action: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        for issue in issues:
            issue_id = self._emit_issue_audit_event(
                event_type,
                issue,
                ctx,
                status=status,
                repair_action=repair_action,
                source_text_hash=source_text_hash,
                target_text_hash=target_text_hash,
                failure_kind=failure_kind,
                fallback_action=fallback_action,
                extra=extra,
            )
            if bucket:
                self._remember_audit_issue_id(ctx, bucket, issue_id)

    def _emit_loop_audit_summary(
        self,
        ctx: RepairRoundContext[ReportT],
        result: RepairLoopResult[ReportT],
        *,
        status: str,
        failure_kind: str = "",
        fallback_action: str = "",
    ) -> None:
        remaining_issues = list(self.extract_issues(ctx.report) if ctx.report is not None else [])
        remaining_issue_ids = self._audit_issue_ids(remaining_issues, ctx)
        try:
            payload = build_repair_audit_summary(
                dimension=self._stage_name(),
                surface=self._audit_surface(),
                chapter=self._chapter_number,
                rounds_used=result.rounds_used,
                status=status,
                selected_issue_ids=list(ctx.extra.get("repair_audit_selected_issue_ids") or []),
                attempted_issue_ids=list(ctx.extra.get("repair_audit_attempted_issue_ids") or []),
                verified_issue_ids=list(ctx.extra.get("repair_audit_verified_issue_ids") or []),
                finalized_issue_ids=list(ctx.extra.get("repair_audit_finalized_issue_ids") or []),
                remaining_issue_ids=remaining_issue_ids,
                failure_kind=failure_kind,
                fallback_action=fallback_action,
                extra={
                    "repair_exhausted": result.repair_exhausted,
                    "best_effort_accepted": result.best_effort_accepted,
                    "needs_human_review": result.needs_human_review,
                    "rollback_count": len(result.rollback_history),
                    "final_score": ctx.score,
                },
            )
            emit_repair_audit_summary(self._on_step, payload)
        except Exception as exc:
            self._logger.debug(
                "failed to emit repair audit summary | stage=%s | error=%s",
                self._stage_name(),
                exc,
            )

    def _timed_out(self, started_at: float) -> bool:
        limit = float(self._config.max_execution_seconds or 0.0)
        return limit > 0 and (time.monotonic() - started_at) > limit

    async def _with_remaining_timeout(
        self,
        awaitable: Any,
        *,
        started_at: float,
        operation: str,
        round_number: int,
    ) -> Any:
        """Bound one repair operation by the loop's remaining time budget."""
        limit = float(self._config.max_execution_seconds or 0.0)
        if limit <= 0:
            return await awaitable
        elapsed = time.monotonic() - started_at
        remaining = limit - elapsed
        if remaining <= 0:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise TimeoutError(
                f"{self._stage_name()} {operation} timed out before round "
                f"{round_number} (limit={limit:.3f}s)"
            )
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except TimeoutError as exc:
            raise TimeoutError(
                f"{self._stage_name()} {operation} exceeded remaining loop budget "
                f"{remaining:.3f}s in round {round_number} (limit={limit:.3f}s)"
            ) from exc

    def _outer_runner(self) -> Any:
        return getattr(self, "_runner", self)

    def _runtime_settings(self) -> Any:
        settings = getattr(self._outer_runner(), "_settings", None)
        if callable(settings):
            return settings()
        return settings

    def _repair_repeated_issue_guard_enabled(self) -> bool:
        settings = self._runtime_settings()
        return bool(getattr(settings, "long_repair_repeated_issue_guard_enabled", True))

    def _repair_repeated_issue_threshold(self) -> int:
        settings = self._runtime_settings()
        raw = getattr(settings, "long_repair_repeated_issue_threshold", 3)
        try:
            return max(1, int(raw or 3))
        except (TypeError, ValueError):
            return 3

    def _repair_repeated_issue_action(self) -> str:
        return "escalate_fulltext"

    def _build_repeated_issue_guard(
        self, ctx: RepairRoundContext[ReportT]
    ) -> dict[str, Any] | None:
        if not self._repair_repeated_issue_guard_enabled():
            return None
        threshold = self._repair_repeated_issue_threshold()
        repeated: list[dict[str, Any]] = []
        for issue in ctx.must_fix_issues:
            signature = self.issue_signature(issue)
            attempts = int(ctx.issue_attempts.get(signature, 0) or 0)
            if attempts >= threshold:
                repeated.append(
                    {
                        "signature": signature,
                        "attempts": attempts,
                        "issue": self._serialize_issue(issue),
                    }
                )
        if not repeated:
            return None
        return {
            "chapter": self._chapter_number,
            "dimension": self._stage_name(),
            "round": ctx.round_number + 1,
            "threshold": threshold,
            "action": self._repair_repeated_issue_action(),
            "issues": repeated,
        }

    def _memory_guidance_for_advisor(self, ctx: RepairRoundContext[ReportT]) -> dict[str, Any]:
        candidates = (
            ctx.extra.get("causal_memory_guidance"),
            ctx.extra.get("reading_power_memory_guidance"),
            (ctx.memory_hints or {}).get("memory_guidance"),
            ctx.extra.get("memory_guidance"),
        )
        for candidate in candidates:
            if isinstance(candidate, dict):
                return dict(candidate)
        return {}

    def _merge_strategy_recommendation(
        self,
        ctx: RepairRoundContext[ReportT],
        guidance: dict[str, Any],
        recommendation: dict[str, Any],
    ) -> None:
        guidance["strategy_recommendation"] = dict(recommendation)
        guidance["preferred_strategy"] = recommendation.get("preferred_strategy", "")
        guidance["strategy_id"] = recommendation.get("strategy_id", guidance.get("strategy_id"))
        memory_guidance = self._memory_guidance_for_advisor(ctx)
        memory_guidance["strategy_recommendation"] = dict(recommendation)
        if ctx.memory_hints is None:
            ctx.memory_hints = {}
        ctx.memory_hints["memory_guidance"] = memory_guidance
        ctx.extra["memory_guidance"] = memory_guidance

    def _apply_repeated_issue_guidance(
        self,
        ctx: RepairRoundContext[ReportT],
        guidance: dict[str, Any],
        guard: dict[str, Any],
    ) -> None:
        recommendation = {
            "strategy_id": "repeated_issue_guard_fulltext",
            "preferred_strategy": "fulltext",
            "source": "repeated_issue_guard",
            "reason": "同一必修问题多次未解决，下一轮倾向扩大修复窗口。",
            "guard": guard,
        }
        self._merge_strategy_recommendation(ctx, guidance, recommendation)
        guidance["repeated_issue_guard"] = guard
        guidance["fulltext_escalation_requested"] = True

    def _human_decision_mode(self) -> str:
        settings = self._runtime_settings()
        control_mode = str(
            getattr(settings, "repair_control_mode", "ai_assisted") or "ai_assisted"
        ).strip()
        if control_mode == "ai_auto":
            return "auto_allow"
        return "ask_high_risk"

    def _human_decision_provider(self) -> Any:
        runner = self._outer_runner()
        return getattr(runner, "_human_decision_provider", None) or getattr(
            runner, "human_decision_provider", None
        )

    def _project_id(self) -> str:
        for source in (self._outer_runner(), getattr(self, "_bundle", None)):
            value = getattr(source, "project_id", "")
            if value:
                return str(value)
            layout = getattr(source, "layout", None)
            value = getattr(layout, "project_id", "")
            if value:
                return str(value)
        return ""

    async def _confirm_strategy_escalation(
        self,
        *,
        ctx: RepairRoundContext[ReportT],
        kind: str,
        preferred_strategy: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> bool:
        mode = self._human_decision_mode()
        if mode == "off":
            return True
        if mode == "auto_allow":
            return True
        settings = self._runtime_settings()
        timeout = int(getattr(settings, "long_repair_human_decision_timeout_s", 300) or 300)
        request = HumanDecisionRequest(
            decision_id=f"{kind}:{self._chapter_number}:{ctx.round_number + 1}:{uuid.uuid4().hex[:8]}",
            kind=kind,
            project_id=self._project_id(),
            chapter_number=self._chapter_number,
            title="高风险修复确认",
            message=reason,
            options=(
                HumanDecisionOption(
                    "allow_escalation",
                    "允许升级",
                    "允许下一轮按建议扩大修复范围。",
                ),
                HumanDecisionOption(
                    "continue_without_escalation",
                    "不升级继续",
                    "保留当前确定性修复策略并继续复检。",
                ),
                HumanDecisionOption("cancel", "取消", "不应用升级建议，继续当前安全路径。"),
            ),
            default_option="continue_without_escalation",
            timeout_seconds=timeout,
            risk="high" if preferred_strategy == "rewrite" else "medium",
            cost_hint=f"建议策略：{preferred_strategy}",
            metadata=dict(metadata),
        )
        response = await request_human_decision(
            provider=self._human_decision_provider(),
            on_step=self._on_step,
            request=request,
        )
        ctx.extra.setdefault("human_decisions", []).append(
            {
                "decision_id": request.decision_id,
                "kind": kind,
                "choice": response.choice,
                "timed_out": response.timed_out,
                "preferred_strategy": preferred_strategy,
            }
        )
        return response.choice == "allow_escalation"

    async def _confirm_change_budget_risk(
        self,
        *,
        ctx: RepairRoundContext[ReportT],
        change_ratio: float,
        threshold: float,
    ) -> bool:
        mode = self._human_decision_mode()
        if mode in {"off", "auto_allow"}:
            return True
        settings = self._runtime_settings()
        timeout = int(getattr(settings, "long_repair_human_decision_timeout_s", 300) or 300)
        request = HumanDecisionRequest(
            decision_id=(
                f"repair_change_budget:{self._chapter_number}:"
                f"{ctx.round_number + 1}:{uuid.uuid4().hex[:8]}"
            ),
            kind="repair_change_budget_risk",
            project_id=self._project_id(),
            chapter_number=self._chapter_number,
            title="修复变更预算确认",
            message=(
                f"本轮修复改动率约 {change_ratio:.1%}，已超过变更预算阈值 "
                f"{threshold:.1%} 的 50%。是否继续使用本轮修复并进入复检？"
            ),
            options=(
                HumanDecisionOption(
                    "continue_repair",
                    "继续复检",
                    "保留本轮修复，继续执行后续复检、账本和回滚判断。",
                ),
                HumanDecisionOption(
                    "rollback_repair",
                    "回滚本轮",
                    "丢弃本轮修复，回到修复前文本。",
                ),
            ),
            default_option="continue_repair",
            timeout_seconds=timeout,
            risk="medium",
            cost_hint=f"change_ratio={change_ratio:.4f}, threshold={threshold:.4f}",
            metadata={
                "change_ratio": change_ratio,
                "threshold": threshold,
                "dimension": self._stage_name(),
                "round": ctx.round_number + 1,
            },
        )
        response = await request_human_decision(
            provider=self._human_decision_provider(),
            on_step=self._on_step,
            request=request,
        )
        ctx.extra.setdefault("human_decisions", []).append(
            {
                "decision_id": request.decision_id,
                "kind": "repair_change_budget_risk",
                "choice": response.choice,
                "timed_out": response.timed_out,
                "change_ratio": change_ratio,
            }
        )
        return response.choice != "rollback_repair"

    async def _maybe_apply_strategy_advisor(
        self,
        *,
        ctx: RepairRoundContext[ReportT],
        guidance: dict[str, Any],
        repeated_issue_guard: dict[str, Any] | None,
    ) -> None:
        from novel_forge.pipeline.long.repair_strategy_advisor import (
            diagnose_repair_strategy,
            should_run_strategy_advisor,
        )

        settings = self._runtime_settings()
        runner = self._outer_runner()
        router = getattr(runner, "_router", None)
        builder = getattr(runner, "_builder", None)
        if settings is None or router is None or builder is None:
            return
        issues = [self._serialize_issue(issue) for issue in ctx.issues]
        must_fix_issues = [self._serialize_issue(issue) for issue in ctx.must_fix_issues]
        memory_guidance = self._memory_guidance_for_advisor(ctx)
        mode = str(getattr(settings, "long_repair_strategy_advisor_mode", "guarded") or "guarded")
        if not should_run_strategy_advisor(
            mode=mode,
            repeated_issue_guard=repeated_issue_guard,
            rollback_history=ctx.rollback_history,
            memory_guidance=memory_guidance,
            must_fix_issues=must_fix_issues,
        ):
            return
        try:
            diagnosis = await diagnose_repair_strategy(
                router=router,
                builder=builder,
                settings=settings,
                on_step=self._on_step,
                dimension=self._stage_name(),
                chapter_number=self._chapter_number,
                round_number=ctx.round_number + 1,
                current_score=ctx.score,
                previous_score=ctx.previous_score,
                issues=issues,
                must_fix_issues=must_fix_issues,
                issue_attempts=ctx.issue_attempts,
                rollback_history=ctx.rollback_history,
                memory_guidance=memory_guidance,
                repeated_issue_guard=repeated_issue_guard,
            )
        except Exception as exc:
            self._on_step(
                "repair_strategy_diagnosis_failed",
                {
                    "chapter": self._chapter_number,
                    "dimension": self._stage_name(),
                    "round": ctx.round_number + 1,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        if diagnosis is None:
            self._on_step(
                "repair_strategy_diagnosis",
                {
                    "chapter": self._chapter_number,
                    "dimension": self._stage_name(),
                    "round": ctx.round_number + 1,
                    "accepted": False,
                    "reason": "invalid_or_empty_diagnosis",
                },
            )
            return
        floor = float(
            getattr(settings, "long_repair_strategy_advisor_confidence_floor", 0.65) or 0.65
        )
        payload = {
            "chapter": self._chapter_number,
            "dimension": self._stage_name(),
            "round": ctx.round_number + 1,
            "accepted": diagnosis.confidence >= floor,
            "confidence_floor": floor,
            **diagnosis.to_guidance(),
        }
        self._on_step("repair_strategy_diagnosis", payload)
        ctx.extra["repair_strategy_diagnosis"] = diagnosis.to_guidance()
        if diagnosis.confidence < floor:
            return
        if diagnosis.preferred_strategy == "rewrite":
            allowed = await self._confirm_strategy_escalation(
                ctx=ctx,
                kind="repair_strategy_rewrite",
                preferred_strategy=diagnosis.preferred_strategy,
                reason=diagnosis.reason or "策略诊断建议 rewrite。",
                metadata=diagnosis.to_guidance(),
            )
            if not allowed:
                return
        recommendation = diagnosis.to_guidance()
        recommendation["strategy_id"] = f"advisor_{diagnosis.preferred_strategy}"
        recommendation["source"] = "repair_strategy_diagnosis"
        self._merge_strategy_recommendation(ctx, guidance, recommendation)

    # ── Main loop ──

    async def run(
        self,
        current_text: str,
        initial_report: ReportT,
    ) -> RepairLoopResult[ReportT]:
        """Execute the repair loop.

        Args:
            current_text: Starting chapter text.
            initial_report: Initial evaluation report.

        Returns:
            RepairLoopResult with final text, report, and metadata.
        """
        ctx = RepairRoundContext[ReportT](
            current_text=current_text,
            report=initial_report,
            issues=list(self.extract_issues(initial_report) or []),
            score=self.compute_score(initial_report),
        )
        def issue_contracts(issues: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
            payloads: list[dict[str, Any]] = []
            issue_ids: list[str] = []
            for index, issue in enumerate(issues, start=1):
                audited_ids = self._audit_issue_ids([issue], ctx)
                issue_id = audited_ids[0] if audited_ids else (
                    "long-"
                    + uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{self._stage_name()}:{self._chapter_number}:{index}:"
                        f"{self._serialize_issue(issue)!r}",
                    ).hex[:20]
                )
                payload = self._serialize_issue(issue)
                payload["_repair_issue_id"] = issue_id
                payloads.append(payload)
                if issue_id not in issue_ids:
                    issue_ids.append(issue_id)
            return payloads, issue_ids

        initial_issue_payloads, initial_issue_ids = issue_contracts(ctx.issues)
        ctx.extra["candidate_verification"] = {
            "dimension": self._stage_name(),
            "validator_id": f"{self._stage_name()}_original_recheck_v1",
            "initial_issues": initial_issue_payloads,
            "original_issue_ids": initial_issue_ids,
            "resolved_issue_ids": [],
            "residual_issue_ids": list(initial_issue_ids),
            "regression_issue_ids": [],
            "recheck_performed": False,
            "gate_passed": False,
            "source_text_hash": text_hash(current_text),
            "candidate_text_hash": text_hash(current_text),
            "score": self.compute_score(initial_report),
            "score_threshold": self._config.score_threshold,
        }
        if not self._config.enabled:
            result = RepairLoopResult(
                current_text=current_text,
                report=initial_report,
                repair_exhausted=False,
                rounds_used=0,
                extra=dict(ctx.extra),
            )
            self._emit_loop_audit_summary(ctx, result, status="disabled")
            return result

        ctx.extra.setdefault("repair_audit_selected_issue_ids", [])
        ctx.extra.setdefault("repair_audit_attempted_issue_ids", [])
        ctx.extra.setdefault("repair_audit_verified_issue_ids", [])
        ctx.extra.setdefault("repair_audit_finalized_issue_ids", [])
        skip, reason = self.repair_skip_precheck(initial_report, ctx)
        if skip:
            self._on_step(
                f"{self._stage_name()}_repair_skipped",
                {"chapter": self._chapter_number, "reason": reason},
            )
            result = RepairLoopResult(
                current_text=current_text,
                report=initial_report,
                repair_exhausted=False,
                rounds_used=0,
                extra=dict(ctx.extra),
            )
            self._emit_loop_audit_summary(ctx, result, status=f"skipped:{reason}")
            return result

        original_score = self.get_original_score(initial_report)
        original_critical_count = self.get_original_critical_count(initial_report)
        started_at = time.monotonic()
        failure_policy = RepairFailurePolicy(self._on_step, logger=self._logger)

        ctx.must_fix_issues = filter_must_fix_issues(ctx.issues, self._config.must_fix_severity)
        ctx.must_fix_issues = self._promote_issues_below_hard_floor(
            ctx.issues,
            ctx.must_fix_issues,
            original_score,
        )
        ctx.pre_issues = list(ctx.issues)
        targeted_payloads, targeted_issue_ids = issue_contracts(ctx.must_fix_issues)
        candidate_verification = dict(ctx.extra["candidate_verification"])
        candidate_verification.update(
            initial_issues=targeted_payloads,
            original_issue_ids=targeted_issue_ids,
            residual_issue_ids=list(targeted_issue_ids),
        )
        ctx.extra["candidate_verification"] = candidate_verification

        if not ctx.must_fix_issues:
            self._on_step(
                f"{self._stage_name()}_repair_skipped",
                {"chapter": self._chapter_number, "reason": "no_must_fix_issues"},
            )
            result = RepairLoopResult(
                current_text=current_text,
                report=initial_report,
                repair_exhausted=False,
                rounds_used=0,
                extra=dict(ctx.extra),
            )
            self._emit_loop_audit_summary(ctx, result, status="skipped:no_must_fix_issues")
            return result

        repair_exhausted = False

        for round_num in range(self._config.max_rounds):
            if self._timed_out(started_at):
                self._on_step(
                    f"{self._stage_name()}_repair_timeout",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "max_execution_seconds": self._config.max_execution_seconds,
                        "action": "cleanup_partial_state",
                    },
                )
                ctx.current_text = self.cleanup_partial_state(ctx)
                repair_exhausted = True
                break
            ctx.round_number = round_num
            ctx.pre_round_text = ctx.current_text
            self.save_checkpoint(self._stage_name(), ctx)

            # ── Pre-round setup ──
            await self.on_round_start(ctx)

            # ── Issue filtering ──
            selected = self._loop_executor.issue_selector.select_for_round(ctx, round_num)
            ctx.must_fix_issues = list(selected.must_fix_issues)
            source_hash = selected.source_text_hash
            self._loop_executor.audit.selected(
                ctx,
                list(ctx.must_fix_issues),
                source_text_hash=source_hash,
            )

            # ── Track issue attempts ──
            attempts = self._loop_executor.issue_selector.record_attempts(ctx)
            self._loop_executor.audit.attempted(
                ctx,
                list(ctx.must_fix_issues),
                source_text_hash=source_hash,
                attempts=attempts,
            )

            repeated_issue_guard = self._build_repeated_issue_guard(ctx)
            if repeated_issue_guard:
                ctx.extra["repeated_issue_guard"] = repeated_issue_guard
                self._on_step("repair_repeated_issue_guard", repeated_issue_guard)

            ctx.extra["repair_attempt_guidance"] = build_repair_attempt_guidance(
                domain=self._stage_name(),
                round_number=round_num + 1,
                max_rounds=self._config.max_rounds,
                issues=list(ctx.must_fix_issues),
                previous_issues=ctx.pre_issues,
                current_score=ctx.score,
                score_threshold=self._config.score_threshold,
                previous_score=ctx.previous_score,
            )
            guidance = ctx.extra["repair_attempt_guidance"]
            if (
                repeated_issue_guard
                and repeated_issue_guard.get("action") == "escalate_fulltext"
            ):
                allow_guard_escalation = await self._confirm_strategy_escalation(
                    ctx=ctx,
                    kind="repair_repeated_issue_guard",
                    preferred_strategy="fulltext",
                    reason="同一必修问题已经重复进入修复队列，建议下一轮扩大为 fulltext 倾向。",
                    metadata=repeated_issue_guard,
                )
                if allow_guard_escalation:
                    self._apply_repeated_issue_guidance(ctx, guidance, repeated_issue_guard)
                else:
                    guidance["repeated_issue_guard"] = repeated_issue_guard
                    guidance["fulltext_escalation_requested"] = False

            await self._maybe_apply_strategy_advisor(
                ctx=ctx,
                guidance=guidance,
                repeated_issue_guard=repeated_issue_guard,
            )
            self._on_step(
                "repair_attempt_guidance",
                {
                    "chapter": self._chapter_number,
                    "dimension": self._stage_name(),
                    "round": round_num + 1,
                    "max_rounds": self._config.max_rounds,
                    "strategy": ctx.extra["repair_attempt_guidance"].get("strategy_id"),
                    "cumulative_tokens": (
                        self._trace.total_tokens if self._trace is not None else 0
                    ),
                },
            )

            # ── Execute repair ──
            try:
                revised_text = await self._with_remaining_timeout(
                    self.execute_repair(ctx),
                    started_at=started_at,
                    operation="repair",
                    round_number=round_num + 1,
                )
            except Exception as exc:
                outcome = failure_policy.repair_failed(
                    snapshot=RepairRoundSnapshot(
                        stage=self._stage_name(),
                        chapter_number=self._chapter_number,
                        round_number=round_num + 1,
                        text=ctx.pre_round_text,
                        report=ctx.report,
                    ),
                    exc=exc,
                )
                ctx.current_text = outcome.current_text
                ctx.report = outcome.report
                repair_exhausted = outcome.repair_exhausted
                self._loop_executor.audit.failed(
                    ctx,
                    list(ctx.must_fix_issues),
                    repair_action="repair_call",
                    source_text_hash=source_hash,
                    failure_kind=outcome.error_kind.value,
                    fallback_action=outcome.action,
                )
                break

            # ── Post-repair hook (prescreen, etc.) ──
            try:
                revised_text = await self.on_repair_success(ctx, revised_text)
            except _PrescreenFailed:
                self._on_step(
                    "repair_prescreen_failed",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "reason": "prescreen_failed",
                    },
                )
                ctx.current_text = ctx.pre_round_text
                continue
            except Exception as exc:
                outcome = failure_policy.repair_failed(
                    snapshot=RepairRoundSnapshot(
                        stage=self._stage_name(),
                        chapter_number=self._chapter_number,
                        round_number=round_num + 1,
                        text=ctx.pre_round_text,
                        report=ctx.report,
                    ),
                    exc=exc,
                    action="rollback_to_pre_repair_text",
                )
                ctx.current_text = outcome.current_text
                ctx.report = outcome.report
                repair_exhausted = outcome.repair_exhausted
                self._loop_executor.audit.failed(
                    ctx,
                    list(ctx.must_fix_issues),
                    repair_action="post_repair",
                    source_text_hash=source_hash,
                    failure_kind=outcome.error_kind.value,
                    fallback_action=outcome.action,
                )
                break

            if revised_text is None:
                ctx.current_text = ctx.pre_round_text
                continue

            ctx.current_text = revised_text

            # ── Intermediate dedup ──
            from novel_forge.pipeline.long.stages.dedup_pronoun import (
                run_self_repetition_check,
            )

            ctx.current_text, _mid_dedup = await asyncio.to_thread(
                run_self_repetition_check,
                self._dedup_runner(),
                ctx.current_text,
            )

            # ── Semantic drift detection ──
            drift = self._detect_drift(ctx)
            if drift.has_drift:
                self._on_step(
                    "semantic_drift_detected",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "stage": self._stage_name(),
                        **drift.to_step_payload(),
                    },
                )
                drift_verdict = decide_rollback(
                    drift=DriftContext(
                        has_drift=drift.has_drift,
                        high_severity_count=drift.high_severity_count,
                    ),
                )
                if self._config.rollback_enabled and drift_verdict != RollbackVerdict.KEEP:
                    self._loop_executor.rollback_guard.rollback_to_pre_round(
                        ctx,
                        event_name=f"{self._stage_name()}_repair_rollback",
                        event_payload={
                            "chapter": self._chapter_number,
                            "round": round_num + 1,
                            "reason": drift_verdict.value,
                            "drift_signals": [
                                s.description[:80] for s in drift.signals if s.severity == "high"
                            ],
                        },
                        issues=list(ctx.must_fix_issues),
                        source_text_hash=source_hash,
                        target_text_hash=text_hash(ctx.current_text),
                        repair_action="semantic_drift_rollback",
                        failure_kind="semantic_drift",
                    )
                    break

            # ── Change budget check ──
            dynamic_policy = derive_dynamic_repair_policy(
                list(ctx.must_fix_issues or ctx.issues),
                base_change_budget=self._config.change_budget,
                base_score_threshold=self._config.score_threshold,
                base_stagnation_delta=self._config.stagnation_delta,
                rollback_history_len=len(ctx.rollback_history),
            )
            change = self._text_change_ratio(ctx.pre_round_text, ctx.current_text)
            if (
                dynamic_policy.change_budget > 0
                and change > dynamic_policy.change_budget * 0.5
                and change <= dynamic_policy.change_budget
            ):
                keep_repair = await self._confirm_change_budget_risk(
                    ctx=ctx,
                    change_ratio=change,
                    threshold=dynamic_policy.change_budget,
                )
                if not keep_repair:
                    self._loop_executor.rollback_guard.rollback_to_pre_round(
                        ctx,
                        event_name="change_budget_human_rollback",
                        event_payload={
                            "chapter": self._chapter_number,
                            "round": round_num + 1,
                            "stage": self._stage_name(),
                            "change_ratio": round(change, 4),
                            "threshold": dynamic_policy.change_budget,
                            "action": "rollback_to_pre_repair_text",
                        },
                        issues=list(ctx.must_fix_issues),
                        source_text_hash=source_hash,
                        target_text_hash=text_hash(ctx.current_text),
                        repair_action="change_budget_human_rollback",
                        failure_kind="change_budget",
                    )
                    repair_exhausted = True
                    break
            if change > dynamic_policy.change_budget:
                self._loop_executor.rollback_guard.rollback_to_pre_round(
                    ctx,
                    event_name="change_budget_exceeded",
                    event_payload={
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "stage": self._stage_name(),
                        "change_ratio": round(change, 4),
                        "threshold": dynamic_policy.change_budget,
                        "issue_pressure": dynamic_policy.issue_pressure,
                        "action": "reject_and_rollback",
                    },
                    issues=list(ctx.must_fix_issues),
                    source_text_hash=source_hash,
                    target_text_hash=text_hash(ctx.current_text),
                    repair_action="change_budget_exceeded",
                    failure_kind="change_budget",
                )
                repair_exhausted = True
                break

            ctx.any_applied = True
            target_hash = text_hash(ctx.current_text)
            self._loop_executor.audit.applied(
                ctx,
                list(ctx.must_fix_issues),
                source_text_hash=source_hash,
                target_text_hash=target_hash,
            )

            if not self._config.recheck_enabled:
                self._on_step(
                    f"{self._stage_name()}_repair_recheck_skipped",
                    {"chapter": self._chapter_number, "round": round_num + 1},
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(ctx.must_fix_issues),
                    ctx,
                    status="recheck_skipped",
                    repair_action="finalize_without_recheck",
                    bucket="repair_audit_finalized_issue_ids",
                    source_text_hash=source_hash,
                    target_text_hash=target_hash,
                )
                break

            # ── Re-evaluation ──
            try:
                new_report = await self._with_remaining_timeout(
                    self.evaluate(ctx.current_text),
                    started_at=started_at,
                    operation="recheck",
                    round_number=round_num + 1,
                )
            except Exception as exc:
                outcome = failure_policy.recheck_failed(
                    snapshot=RepairRoundSnapshot(
                        stage=self._stage_name(),
                        chapter_number=self._chapter_number,
                        round_number=round_num + 1,
                        text=ctx.pre_round_text,
                        report=ctx.report,
                    ),
                    exc=exc,
                )
                ctx.current_text = outcome.current_text
                ctx.report = outcome.report
                repair_exhausted = outcome.repair_exhausted
                self._loop_executor.audit.failed(
                    ctx,
                    list(ctx.must_fix_issues),
                    repair_action="recheck",
                    source_text_hash=source_hash,
                    target_text_hash=text_hash(ctx.current_text),
                    failure_kind=outcome.error_kind.value,
                    fallback_action=outcome.action,
                )
                break

            self._on_step(
                f"{self._stage_name()}_repair_recheck",
                {
                    "chapter": self._chapter_number,
                    "round": round_num + 1,
                    "new_score": self.compute_score(new_report),
                    "remaining_issues": len(self.extract_issues(new_report)),
                },
            )
            remaining_after_recheck = list(self.extract_issues(new_report))
            self._on_step(
                "repair_audit_event",
                {
                    "schema_version": 1,
                    "event_type": "rechecked",
                    "dimension": self._stage_name(),
                    "surface": self._audit_surface(),
                    "chapter": self._chapter_number,
                    "round": round_num + 1,
                    "status": "rechecked",
                    "repair_action": "recheck",
                    "source_text_hash": source_hash,
                    "target_text_hash": target_hash,
                    "remaining_issue_ids": self._audit_issue_ids(remaining_after_recheck, ctx),
                    "remaining_issues": len(remaining_after_recheck),
                    "new_score": self.compute_score(new_report),
                },
            )

            # ── Issue ledger diff ──
            post_issues_raw = remaining_after_recheck
            post_issues: list[dict[str, Any]] = []
            for issue in post_issues_raw:
                serialized = self._serialize_issue(issue)
                paragraph_span = serialized.get("paragraph_span") or []
                paragraph_start = serialized.get("paragraph_start") or serialized.get("paragraph_index")
                paragraph_end = serialized.get("paragraph_end") or paragraph_start
                if isinstance(paragraph_span, list) and paragraph_span:
                    paragraph_start = paragraph_span[0]
                    paragraph_end = paragraph_span[-1]
                post_issues.append(
                    {
                        "issue_id": serialized.get("issue_id") or serialized.get("id") or "",
                        "issue_type": (
                            serialized.get("issue_type")
                            or serialized.get("type")
                            or serialized.get("category")
                            or ""
                        ).lower(),
                        "severity": (serialized.get("severity") or "medium").lower(),
                        "summary": serialized.get("summary") or serialized.get("description") or "",
                        "evidence": serialized.get("evidence")
                        or serialized.get("evidence_quote")
                        or serialized.get("quote")
                        or "",
                        "location": serialized.get("location") or serialized.get("position") or "",
                        "paragraph_start": paragraph_start or 0,
                        "paragraph_end": paragraph_end or 0,
                        "location_confidence": serialized.get("location_confidence") or 0.0,
                    }
                )
            repaired_types = set(
                (self._issue_value(iss, "issue_type", "") or "").lower()
                for iss in ctx.must_fix_issues
            )
            repair_no_op = ctx.current_text == ctx.pre_round_text
            ledger = diff_issues(
                ctx.pre_issues,
                post_issues,
                repaired_issue_types=repaired_types,
                repair_was_no_op=repair_no_op,
            )
            self._on_step(
                f"{self._stage_name()}_issue_ledger",
                {
                    "chapter": self._chapter_number,
                    "round": round_num + 1,
                    **ledger.to_step_payload(),
                },
            )
            self._emit_issues_audit_event(
                "verified",
                list(ledger.resolved),
                ctx,
                status="resolved",
                repair_action="ledger_diff",
                bucket="repair_audit_verified_issue_ids",
                source_text_hash=source_hash,
                target_text_hash=target_hash,
                extra={"ledger_status": "resolved"},
            )
            self._emit_issues_audit_event(
                "verified",
                list(ledger.unresolved),
                ctx,
                status="unresolved",
                repair_action="ledger_diff",
                source_text_hash=source_hash,
                target_text_hash=target_hash,
                extra={"ledger_status": "unresolved"},
            )
            self._emit_issues_audit_event(
                "verified",
                list(ledger.new_high_critical),
                ctx,
                status="new_regression",
                repair_action="ledger_diff",
                source_text_hash=source_hash,
                target_text_hash=target_hash,
                failure_kind="regression",
                extra={"ledger_status": "new_high_critical"},
            )

            # ── Rollback decision ──
            rollback_verdict = decide_rollback(
                ledger_has_regression=ledger.has_regression,
                ledger=ledger,
            )
            if self._config.rollback_enabled and rollback_verdict != RollbackVerdict.KEEP:
                ctx.rollback_history.append(
                    {
                        "round": round_num + 1,
                        "reason": rollback_verdict.value,
                        "new_issues": [
                            {
                                "type": i.issue_type,
                                "severity": i.severity,
                                "summary": i.summary[:80],
                            }
                            for i in ledger.new_high_critical
                        ],
                        "resolved_count": len(ledger.resolved_high_critical),
                        "new_count": len(ledger.new_high_critical),
                    }
                )
                self._on_step(
                    f"{self._stage_name()}_repair_rollback",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "reason": rollback_verdict.value,
                        "rollback_history_len": len(ctx.rollback_history),
                    },
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(ctx.must_fix_issues),
                    ctx,
                    status="rolled_back",
                    repair_action="ledger_regression_rollback",
                    source_text_hash=source_hash,
                    target_text_hash=target_hash,
                    failure_kind="regression",
                    fallback_action="rollback_to_pre_repair_text",
                )

                if len(ctx.rollback_history) <= self._config.rollback_retry_limit:
                    ctx.current_text = ctx.pre_round_text
                    if ctx.report is not None:
                        ctx.pre_issues = list(self.extract_issues(ctx.report) or [])
                    continue
                else:
                    ctx.current_text = ctx.pre_round_text
                    break

            await self.on_recheck_success(ctx, new_report, ledger)

            ctx.report = new_report
            ctx.pre_issues = post_issues
            ctx.issues = post_issues_raw
            ctx.previous_score = ctx.score
            ctx.score = self.compute_score(new_report)

            # ── Repair continuation decision ──
            ctx.must_fix_issues = filter_must_fix_issues(ctx.issues, self._config.must_fix_severity)
            repair_verdict = decide_repair_continuation(
                RepairContext(
                    current_round=round_num,
                    max_rounds=self._config.max_rounds,
                    score=ctx.score,
                    score_threshold=dynamic_policy.score_threshold,
                    must_fix_issues=tuple(ctx.must_fix_issues),
                    previous_score=ctx.previous_score,
                    stagnation_delta=dynamic_policy.stagnation_delta,
                )
            )
            verification_evidence = dict(ctx.extra.get("candidate_verification") or {})
            original_issue_ids = list(verification_evidence.get("original_issue_ids") or [])
            gate_passed = repair_verdict == RepairVerdict.COMPLETE
            verification_evidence.update(
                recheck_performed=True,
                gate_passed=gate_passed,
                resolved_issue_ids=(
                    original_issue_ids
                    if gate_passed
                    else self._audit_issue_ids(list(ledger.resolved), ctx)
                ),
                residual_issue_ids=(
                    []
                    if gate_passed
                    else self._audit_issue_ids(list(ledger.unresolved), ctx)
                    or original_issue_ids
                ),
                regression_issue_ids=self._audit_issue_ids(
                    list(ledger.new_high_critical), ctx
                ),
                candidate_text_hash=target_hash,
                score=ctx.score,
                score_threshold=dynamic_policy.score_threshold,
                repair_verdict=repair_verdict.value,
            )
            ctx.extra["candidate_verification"] = verification_evidence

            if repair_verdict == RepairVerdict.COMPLETE:
                self._on_step(
                    f"{self._stage_name()}_repair_early_exit",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "score": ctx.score,
                        "remaining_must_fix": len(ctx.must_fix_issues),
                    },
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(ledger.resolved),
                    ctx,
                    status="finalized",
                    repair_action="repair_complete",
                    bucket="repair_audit_finalized_issue_ids",
                    source_text_hash=source_hash,
                    target_text_hash=target_hash,
                )
                break

            if repair_verdict == RepairVerdict.STAGNATED:
                self._on_step(
                    f"{self._stage_name()}_repair_stagnated",
                    {
                        "chapter": self._chapter_number,
                        "round": round_num + 1,
                        "score": ctx.score,
                        "previous_score": ctx.previous_score,
                        "remaining_must_fix": len(ctx.must_fix_issues),
                    },
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(ctx.must_fix_issues),
                    ctx,
                    status="stagnated",
                    repair_action="repair_stagnated",
                    bucket="repair_audit_finalized_issue_ids",
                    source_text_hash=source_hash,
                    target_text_hash=target_hash,
                    failure_kind="stagnated",
                    fallback_action="needs_human_review",
                )
                repair_exhausted = True
                break

        else:
            if self._config.max_rounds > 0:
                repair_exhausted = True
                self._on_step(
                    f"{self._stage_name()}_repair_exhausted",
                    {
                        "chapter": self._chapter_number,
                        "rounds_used": self._config.max_rounds,
                        "remaining_must_fix": len(ctx.must_fix_issues),
                    },
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(ctx.must_fix_issues),
                    ctx,
                    status="exhausted",
                    repair_action="max_rounds_exhausted",
                    bucket="repair_audit_finalized_issue_ids",
                    source_text_hash=text_hash(ctx.pre_round_text),
                    target_text_hash=text_hash(ctx.current_text),
                    failure_kind="exhausted",
                    fallback_action="needs_human_review",
                )

        # ── Best-effort acceptance ──
        best_effort_accepted = False
        best_effort_reason = ""
        needs_human_review = False

        if repair_exhausted or len(ctx.rollback_history) > 0:
            current_score = ctx.score
            current_critical = sum(
                1
                for i in (self.extract_issues(ctx.report) if ctx.report else [])
                if getattr(i, "severity", "").lower() == "critical"
            )
            best_effort_verdict = decide_best_effort_accept(
                BestEffortContext(
                    original_score=original_score,
                    current_score=current_score,
                    original_critical_count=original_critical_count,
                    current_critical_count=current_critical,
                    alignment_score=self._get_alignment_score(),
                    has_prompt_leaks=self._has_prompt_leaks(),
                ),
                hard_floor=self._config.hard_floor,
            )
            if best_effort_verdict.should_accept:
                best_effort_accepted = True
                best_effort_reason = best_effort_verdict.reason
                needs_human_review = True
                repair_exhausted = False
                self._on_step(
                    f"{self._stage_name()}_best_effort_accepted",
                    {
                        "chapter": self._chapter_number,
                        "original_score": original_score,
                        "current_score": current_score,
                        "reason": best_effort_verdict.reason,
                    },
                )
                self._emit_issues_audit_event(
                    "finalized",
                    list(self.extract_issues(ctx.report) if ctx.report is not None else []),
                    ctx,
                    status="best_effort_accepted",
                    repair_action="best_effort_acceptance",
                    bucket="repair_audit_finalized_issue_ids",
                    source_text_hash=text_hash(ctx.pre_round_text),
                    target_text_hash=text_hash(ctx.current_text),
                    fallback_action="best_effort_accept",
                )

        # ── Post-loop hook ──
        await self.on_loop_exit(ctx)

        # ── Build result ──
        result = RepairLoopResult[ReportT](
            current_text=ctx.current_text,
            report=ctx.report,
            repair_exhausted=repair_exhausted,
            rounds_used=ctx.round_number + 1 if ctx.round_number > 0 or ctx.any_applied else 0,
            best_effort_accepted=best_effort_accepted,
            best_effort_reason=best_effort_reason,
            needs_human_review=needs_human_review,
            rollback_history=list(ctx.rollback_history),
            extra=dict(ctx.extra),
        )
        final_result = self.build_extra_result(ctx, result)
        summary_status = (
            "best_effort_accepted"
            if final_result.best_effort_accepted
            else "needs_human_review"
            if final_result.needs_human_review
            else "exhausted"
            if final_result.repair_exhausted
            else "completed"
        )
        self._emit_loop_audit_summary(
            ctx,
            final_result,
            status=summary_status,
            fallback_action="rollback_to_pre_repair_text" if ctx.rollback_history else "",
        )
        return final_result

    # ── Internal helpers ──

    def _stage_name(self) -> str:
        """Return the stage name for logging/events (e.g., 'continuity', 'causal')."""
        explicit = getattr(self, "stage_name", None)
        if explicit:
            return str(explicit)
        name = self.__class__.__name__
        suffix = "RepairRunner"
        if name.endswith(suffix):
            name = name[: -len(suffix)]
        snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
        return snake.strip("_").lower()

    def _dedup_runner(self) -> Any:
        """Return the outer chapter runner expected by dedup helpers."""
        return getattr(self, "_runner", self)

    def _get_pov_character(self) -> str:
        """Get POV character name for drift detection.

        Default: empty string (no POV tracking).
        Override to provide chapter-specific POV character.
        """
        return ""

    def _get_known_characters(self) -> list[str]:
        """Get list of known characters for drift detection.

        Default: empty list (no character tracking).
        Override to provide chapter-specific character list.
        """
        return []

    def _get_chapter_outline(self) -> Any:
        """Get chapter outline for drift detection.

        Default: None (no outline-based drift checks).
        Override to provide chapter outline for goal/plot drift detection.
        """
        return None

    def _detect_drift(self, ctx: RepairRoundContext[ReportT]) -> Any:
        """Detect semantic drift between pre-round and current text.

        Uses helper methods for POV character, known characters, and chapter outline.
        Subclasses override the helpers instead of this method.
        """
        return detect_drift(
            ctx.pre_round_text,
            ctx.current_text,
            pov_character=self._get_pov_character(),
            known_characters=self._get_known_characters(),
            chapter_outline=self._get_chapter_outline(),
        )

    def _get_alignment_score(self) -> float:
        """Get current alignment score for best-effort context.

        Default: 7.0 (safe default).
        Override to load from actual alignment report.
        """
        return 7.0

    def _has_prompt_leaks(self) -> bool:
        """Check for prompt leaks in chapter repair report.

        Default: False.
        Override to check actual report.
        """
        return False


class _PrescreenFailed(Exception):
    """Internal exception to signal prescreen failure."""
