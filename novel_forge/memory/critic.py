"""CriticAgent — Independent validation agent for continuity and consistency.

Provides an autonomous critique layer that validates chapter content against:
- Cross-chapter continuity
- Character consistency
- Causal chain integrity
- Thematic coherence
- Alignment to outline/plan
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, List, Literal, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.utils.json import safe_parse_json
from novel_forge.core.utils.type_coerce import stringify_text_value
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.motif import MotifTracker
from novel_forge.memory.style_rule_tracker import StyleRuleTracker
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.schemas import StoryKernel

_log = get_logger("memory.critic")

CriticSeverity = Literal["critical", "high", "medium", "low"]

_NEUTRAL_SEVERITY_LABELS = {
    "",
    "none",
    "pass",
    "ok",
    "no",
    "false",
    "无",
    "通过",
    "正常",
}

_CRITIC_SEVERITY_ALIASES: dict[str, CriticSeverity] = {
    "critical": "critical",
    "fatal": "critical",
    "blocker": "critical",
    "p0": "critical",
    "高危": "critical",
    "致命": "critical",
    "high": "high",
    "major": "high",
    "severe": "high",
    "p1": "high",
    "严重": "high",
    "高": "high",
    "medium": "medium",
    "moderate": "medium",
    "mid": "medium",
    "p2": "medium",
    "中": "medium",
    "中等": "medium",
    "low": "low",
    "minor": "low",
    "info": "low",
    "p3": "low",
    "轻微": "low",
    "低": "low",
}


def _normalize_critic_severity(
    value: Any,
    *,
    default: CriticSeverity = "medium",
    allow_none: bool = False,
) -> CriticSeverity | None:
    """Normalize LLM priority labels into the critic severity vocabulary."""
    text = stringify_text_value(value).strip().lower()
    if text in _NEUTRAL_SEVERITY_LABELS:
        return None if allow_none else default
    return _CRITIC_SEVERITY_ALIASES.get(text, default)


def _coerce_critic_confidence(value: Any, default: float = 0.5) -> float:
    """Coerce confidence into [0, 1]."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(1.0, score))


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iter_plot_thread_items(
    value: Any,
    *,
    limit: int | None = None,
) -> list[tuple[str, Any]]:
    """Return plot-thread items from either legacy dict or StoryKernel list shape."""
    if isinstance(value, dict) and "plot_threads" in value:
        raw_threads = value.get("plot_threads") or []
    elif hasattr(value, "plot_threads"):
        raw_threads = value.plot_threads or []
    else:
        raw_threads = value or []

    items: list[tuple[str, Any]] = []
    if isinstance(raw_threads, dict):
        iterable = list(raw_threads.items())
    elif isinstance(raw_threads, list):
        iterable = []
        for index, thread in enumerate(raw_threads):
            if isinstance(thread, dict):
                thread_id = stringify_text_value(thread.get("thread_id")) or str(index)
            else:
                thread_id = stringify_text_value(getattr(thread, "thread_id", "")) or str(index)
            iterable.append((thread_id, thread))
    else:
        iterable = []

    for thread_id, thread in iterable:
        clean_id = stringify_text_value(thread_id)
        if clean_id:
            items.append((clean_id, thread))
        if limit is not None and len(items) >= limit:
            break
    return items


def _thread_text(thread: Any, field_name: str) -> str:
    if isinstance(thread, dict):
        return stringify_text_value(thread.get(field_name))
    return stringify_text_value(getattr(thread, field_name, ""))


def _thread_int(thread: Any, field_name: str, default: int = 0) -> int:
    raw = (
        thread.get(field_name) if isinstance(thread, dict) else getattr(thread, field_name, default)
    )
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        return default


@dataclass
class CritiqueIssue:
    """An issue identified by the critic agent."""

    issue_type: Literal[
        "continuity_error",
        "character_inconsistency",
        "causal_break",
        "thematic_drift",
        "pacing_issue",
        "unresolved_thread",
        "forgotten_setup",
        "alignment_miss",
        "alignment_weak",
        "style_template_drift",
    ]
    severity: Literal["critical", "high", "medium", "low"]
    summary: str
    evidence: str
    affected_chapters: list[int]
    suggested_fix: str
    confidence: float = 0.0  # 0.0 - 1.0

    def __post_init__(self) -> None:
        self.severity = _normalize_critic_severity(self.severity, default="medium") or "medium"
        self.summary = stringify_text_value(self.summary)
        self.evidence = stringify_text_value(self.evidence)
        self.suggested_fix = stringify_text_value(self.suggested_fix)
        self.affected_chapters = [
            chapter
            for chapter in (_coerce_int(chapter) for chapter in self.affected_chapters)
            if chapter > 0
        ]
        self.confidence = _coerce_critic_confidence(self.confidence)


@dataclass
class AlignmentResult:
    """Alignment check result integrated into CritiqueReport."""

    alignment_score: float = 10.0
    risk_level: str = "low"
    summary: str = ""
    missing_main_points: List[str] = field(default_factory=list)
    supportive_subplot_points: List[str] = field(default_factory=list)
    weak_subplot_points: List[str] = field(default_factory=list)
    repair_actions: List[str] = field(default_factory=list)
    is_degraded: bool = False
    degraded_reason: str = ""


@dataclass
class CritiqueReport:
    """Complete critique report for a chapter."""

    chapter_number: int
    overall_score: float = 10.0
    issues: list[CritiqueIssue] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    alignment_result: AlignmentResult | None = None

    @property
    def has_critical_issues(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    @property
    def requires_revision(self) -> bool:
        return self.overall_score < 6.0 or self.has_critical_issues

    @property
    def alignment_timed_out(self) -> bool:
        """Whether the alignment check timed out during this critique run."""
        execution = self.metadata.get("execution") if isinstance(self.metadata, dict) else None
        if not isinstance(execution, dict):
            return False
        timed_out = execution.get("checks_timed_out") or []
        return "alignment" in timed_out

    def to_alignment_report(self) -> AlignmentReport | None:
        """Convert alignment result to standard AlignmentReport format.

        When the alignment check timed out (``alignment_result is None`` but
        ``alignment_timed_out`` is True), returns ``None`` so the caller can
        construct a properly-marked fallback report.
        """
        if self.alignment_result is None:
            return None

        report = AlignmentReport(
            alignment_score=self.alignment_result.alignment_score,
            risk_level=self.alignment_result.risk_level,
            summary=self.alignment_result.summary,
            missing_main_points=self.alignment_result.missing_main_points,
            supportive_subplot_points=self.alignment_result.supportive_subplot_points,
            weak_subplot_points=self.alignment_result.weak_subplot_points,
            repair_actions=self.alignment_result.repair_actions,
        )
        # If the alignment result survived but the overall critique had a
        # timeout annotation, mark the report as degraded so downstream gates
        # know the score may be unreliable.
        if self.alignment_timed_out:
            report = report.model_copy(
                update={
                    "evaluation_status": "timeout",
                    "is_fallback": True,
                    "fallback_reason": "critic_soft_timeout",
                }
            )
        elif getattr(self.alignment_result, "is_degraded", False):
            report = report.model_copy(
                update={
                    "evaluation_status": "degraded",
                    "is_fallback": True,
                    "fallback_reason": getattr(
                        self.alignment_result, "degraded_reason", "degraded_response"
                    ),
                }
            )
        return report


class CriticAgent:
    """Independent agent for chapter validation.

    The CriticAgent operates as a separate validation layer that:
    1. Reviews chapter content against canon state
    2. Checks cross-chapter continuity
    3. Validates character consistency
    4. Verifies causal chain integrity
    5. Assesses thematic coherence

    Unlike the standard ContinuityEvalStep, the CriticAgent has access
    to episodic memory and can perform semantic searches for deeper analysis.
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        episodic_memory: EpisodicMemory | None = None,
        motif_tracker: MotifTracker | None = None,
        style_rule_tracker: StyleRuleTracker | None = None,
        check_timeout_s: float = 45.0,
        timeout_extend_attempts: int = 1,
        timeout_extend_multiplier: float = 1.5,
        cache_enabled: bool = True,
        cache_max_entries: int = 24,
        motif_repetition_lookback_chapters: int = 5,
        motif_repetition_recent_gap_chapters: int = 2,
        style_rule_lookback_chapters: int = 5,
        style_rule_repetition_gap_chapters: int = 2,
    ) -> None:
        self._router = router
        self._builder = builder
        self._episodic_memory = episodic_memory
        self._motif_tracker = motif_tracker
        self._style_rule_tracker = style_rule_tracker
        self._check_timeout_s = max(0.0, float(check_timeout_s))
        self._timeout_extend_attempts = max(0, int(timeout_extend_attempts))
        self._timeout_extend_multiplier = max(1.0, float(timeout_extend_multiplier))
        self._cache_enabled = bool(cache_enabled)
        self._cache_max_entries = max(1, int(cache_max_entries))
        self._motif_repetition_lookback_chapters = max(0, int(motif_repetition_lookback_chapters))
        self._motif_repetition_recent_gap_chapters = max(
            1, int(motif_repetition_recent_gap_chapters)
        )
        self._style_rule_lookback_chapters = max(0, int(style_rule_lookback_chapters))
        self._style_rule_repetition_gap_chapters = max(1, int(style_rule_repetition_gap_chapters))
        self._result_cache: OrderedDict[str, CritiqueReport] = OrderedDict()
        self._cache_stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "stores": 0,
            "evictions": 0,
        }
        self._cache_lock = asyncio.Lock()

        _log.info(
            "CriticAgent initialized | has_episodic=%s | has_motifs=%s | timeout_s=%.1f | "
            "extend_attempts=%d | extend_multiplier=%.2f | cache=%s | cache_max=%d",
            episodic_memory is not None,
            motif_tracker is not None,
            self._check_timeout_s,
            self._timeout_extend_attempts,
            self._timeout_extend_multiplier,
            self._cache_enabled,
            self._cache_max_entries,
        )

    def _dynamic_max_tokens(
        self,
        task_type: TaskType,
        target_output_chars: int,
        *,
        prompt_overhead: int,
        min_tokens: int,
    ) -> int:
        return calculate_route_aware_max_tokens(
            self._router,
            task_type,
            target_output_chars,
            prompt_overhead=prompt_overhead,
            min_tokens=min_tokens,
        )

    async def critique_chapter(
        self,
        chapter_number: int,
        chapter_text: str,
        canon_state: StoryKernel,
        chapter_outline: ChapterOutline,
        story_outline: StoryOutline | None = None,
        previous_chapter_text: str | None = None,
        chapter_plan: ChapterPlan | None = None,
        narrative_context: Any | None = None,
        genre: str = "",
        check_alignment: bool = False,
        chapter_bridge: ChapterBridge | None = None,
        previous_chapter_ending: str = "",
        audit_context: Any | None = None,
        memory_hints: dict[str, Any] | None = None,
    ) -> CritiqueReport:
        """Perform comprehensive critique of a chapter.

        Args:
            chapter_number: Chapter number
            chapter_text: Full chapter text
            canon_state: Current canon state
            chapter_outline: Chapter outline
            story_outline: Full story outline (optional)
            previous_chapter_text: Previous chapter text (optional)
            chapter_plan: Chapter plan for alignment check (optional)
            narrative_context: Deprecated compatibility field; not forwarded to alignment prompts.
            genre: Deprecated compatibility field; not forwarded to alignment prompts.
            check_alignment: Whether to perform alignment check (default False)
            chapter_bridge: Bridge contract from previous chapter (optional)
            previous_chapter_ending: Raw ending text of previous chapter (optional)

        Returns:
            CritiqueReport with issues and recommendations
        """
        start_time = time.monotonic()

        _log.info(
            "critique_start | chapter=%d | text_length=%d | check_alignment=%s",
            chapter_number,
            len(chapter_text),
            check_alignment,
        )

        cache_key = self._build_critique_cache_key(
            chapter_number=chapter_number,
            chapter_text=chapter_text,
            canon_state=canon_state,
            chapter_outline=chapter_outline,
            previous_chapter_text=previous_chapter_text,
            chapter_plan=chapter_plan,
            genre=genre,
            check_alignment=check_alignment,
            chapter_bridge=chapter_bridge,
            previous_chapter_ending=previous_chapter_ending,
            audit_context=audit_context,
            memory_hints=memory_hints,
        )
        if self._cache_enabled and cache_key:
            cached_report = await self._get_cached_report(cache_key)
            if cached_report is not None:
                cached_copy = copy.deepcopy(cached_report)
                execution_meta = cached_copy.metadata.setdefault("execution", {})
                execution_meta["cache_hit"] = True
                execution_meta["cache_key_prefix"] = cache_key[:12]
                execution_meta["cache_size"] = len(self._result_cache)
                execution_meta["cache_max_entries"] = self._cache_max_entries
                execution_meta["cache_stats"] = dict(self._cache_stats)
                _log.debug(
                    "critique_cache_hit | chapter=%d | cache_key=%s",
                    chapter_number,
                    cache_key[:12],
                )
                return cached_copy

        try:
            issues: list[CritiqueIssue] = []
            strengths: list[str] = []
            warnings: list[str] = []
            runtime_warnings: list[str] = []
            alignment_result: AlignmentResult | None = None
            task_coroutines: dict[str, Any] = {
                "character": self._check_character_consistency(
                    chapter_number,
                    chapter_text,
                    canon_state,
                    audit_context=audit_context,
                    memory_hints=memory_hints,
                ),
                "causal": self._check_causal_chain(
                    chapter_number,
                    chapter_text,
                    chapter_outline,
                    canon_state,
                    chapter_bridge=chapter_bridge,
                    previous_chapter_ending=previous_chapter_ending,
                ),
                "plot_threads": self._check_plot_threads(
                    chapter_number,
                    chapter_text,
                    canon_state,
                ),
                "strengths": self._identify_strengths(
                    chapter_number,
                    chapter_text,
                    chapter_outline,
                ),
            }

            if previous_chapter_text:
                task_coroutines["continuity"] = self._check_chapter_continuity(
                    chapter_number,
                    chapter_text,
                    previous_chapter_text,
                    canon_state,
                    chapter_bridge=chapter_bridge,
                    chapter_plan=chapter_plan,
                    previous_chapter_ending=previous_chapter_ending,
                )
            if self._motif_tracker:
                task_coroutines["thematic"] = self._check_thematic_coherence(
                    chapter_number,
                    chapter_text,
                    canon_state,
                )
            if self._style_rule_tracker:
                task_coroutines["style_rule"] = self._check_style_rule_repetition(
                    chapter_number,
                    chapter_text,
                )
            if check_alignment and chapter_plan:
                task_coroutines["alignment"] = self._check_alignment(
                    chapter_number=chapter_number,
                    chapter_text=chapter_text,
                    chapter_outline=chapter_outline,
                    chapter_plan=chapter_plan,
                )
            task_names = list(task_coroutines.keys())
            tasks = {name: asyncio.create_task(coro) for name, coro in task_coroutines.items()}

            wait_timeout = self._check_timeout_s if self._check_timeout_s > 0 else None
            timeout_windows_s: list[float] = []
            timeout_extensions_used = 0

            pending: set[asyncio.Task[Any]] = set(tasks.values())
            if wait_timeout is not None:
                current_timeout = wait_timeout
                max_wait_rounds = 1 + self._timeout_extend_attempts
                for round_idx in range(max_wait_rounds):
                    if not pending:
                        break
                    _done_now, pending = await asyncio.wait(
                        pending,
                        timeout=current_timeout,
                    )
                    timeout_windows_s.append(float(current_timeout))
                    if pending and round_idx < self._timeout_extend_attempts:
                        timeout_extensions_used += 1
                        current_timeout = max(
                            0.001,
                            current_timeout * self._timeout_extend_multiplier,
                        )
                        _log.info(
                            "critique_timeout_extend | chapter=%d | extension=%d/%d | "
                            "next_timeout_s=%.1f | pending_checks=%d",
                            chapter_number,
                            timeout_extensions_used,
                            self._timeout_extend_attempts,
                            current_timeout,
                            len(pending),
                        )
            else:
                await asyncio.wait(pending)
                pending = set()

            incomplete_checks: list[str] = []
            failed_checks: list[str] = []
            timed_out_checks: list[str] = []
            for task_name, task in tasks.items():
                if task in pending:
                    timed_out_checks.append(task_name)
                    incomplete_checks.append(task_name)
                    task.cancel()
                    continue

                try:
                    result = task.result()
                except Exception as exc:
                    failed_checks.append(task_name)
                    incomplete_checks.append(task_name)
                    _log.warning(
                        "critique_check_failed | chapter=%d | check=%s | error=%s",
                        chapter_number,
                        task_name,
                        exc,
                    )
                    continue

                if task_name == "strengths":
                    strengths = list(result) if isinstance(result, list) else []
                    continue

                if task_name == "alignment":
                    if isinstance(result, AlignmentResult):
                        alignment_result = result
                    continue

                if isinstance(result, list):
                    issues.extend(result)

            if pending:
                # Drain cancelled tasks safely: suppress all exceptions (including
                # late-arriving HTTP responses whose JSON may be malformed) so
                # orphaned model calls never leak errors into the main flow.
                _pending_list = list(pending)
                _task_to_name = {t: n for n, t in tasks.items()}
                _drain_results = await asyncio.gather(
                    *_pending_list,
                    return_exceptions=True,
                )
                for _task_obj, _result in zip(_pending_list, _drain_results, strict=True):
                    if isinstance(_result, BaseException) and not isinstance(
                        _result,
                        asyncio.CancelledError,
                    ):
                        _log.debug(
                            "critique_orphan_task_drained | chapter=%d | check=%s | error=%s",
                            chapter_number,
                            _task_to_name.get(_task_obj, "?"),
                            _result,
                        )

            if timed_out_checks:
                total_wait_s = round(sum(timeout_windows_s), 1) if timeout_windows_s else 0.0
                extension_note = (
                    f"，已自动延长 {timeout_extensions_used} 次（累计等待 {total_wait_s:.1f}s）"
                    if timeout_extensions_used > 0
                    else ""
                )
                runtime_warnings.append(
                    "部分检查项因 API 响应超时已跳过（"
                    + ", ".join(timed_out_checks)
                    + f"）{extension_note}，其余检查结果仍有效。"
                    + "可在设置中调大「评审软超时(秒)」或「超时自动延长次数」以改善。"
                )
                _log.warning(
                    "critique_partial_timeout | chapter=%d | timeout_windows_s=%s | "
                    "extensions_used=%d | checks=%s",
                    chapter_number,
                    timeout_windows_s,
                    timeout_extensions_used,
                    timed_out_checks,
                )

            total_checks = len(tasks)
            incomplete_count = len(incomplete_checks)
            incomplete_ratio = incomplete_count / total_checks if total_checks > 0 else 0.0
            if incomplete_ratio > 0.5:
                _log.warning(
                    "critique_high_incomplete_ratio | chapter=%d | incomplete=%d/%d | "
                    "ratio=%.2f | timed_out=%s | failed=%s",
                    chapter_number,
                    incomplete_count,
                    total_checks,
                    incomplete_ratio,
                    timed_out_checks,
                    failed_checks,
                )
                runtime_warnings.append(
                    f"评审检查项完成率过低（{incomplete_count}/{total_checks}，"
                    f"完成率 {100.0 - incomplete_ratio * 100:.0f}%），"
                    "建议检查模型响应速度或增加超时配置。"
                )

            # Alignment issues are appended after alignment check returns.
            if alignment_result is not None:
                for missing in alignment_result.missing_main_points:
                    issues.append(
                        CritiqueIssue(
                            issue_type="alignment_miss",
                            severity="high",
                            summary=f"缺少主线内容：{missing}",
                            evidence="",
                            affected_chapters=[chapter_number],
                            suggested_fix="",
                            confidence=1.0,
                        )
                    )

                for weak in alignment_result.weak_subplot_points:
                    issues.append(
                        CritiqueIssue(
                            issue_type="alignment_weak",
                            severity="medium",
                            summary=f"支线推进偏弱：{weak}",
                            evidence="",
                            affected_chapters=[chapter_number],
                            suggested_fix="",
                            confidence=0.8,
                        )
                    )

            # 7. Generate warnings for patterns
            warnings = runtime_warnings + self._generate_pattern_warnings(issues)

            # Calculate overall score
            overall_score = self._calculate_overall_score(issues)

            # Factor in alignment score if available
            if alignment_result and alignment_result.alignment_score < overall_score:
                overall_score = min(overall_score, alignment_result.alignment_score)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            severity_counts = {
                "critical": sum(1 for i in issues if i.severity == "critical"),
                "high": sum(1 for i in issues if i.severity == "high"),
                "medium": sum(1 for i in issues if i.severity == "medium"),
                "low": sum(1 for i in issues if i.severity == "low"),
            }

            _log.info(
                "critique_done | chapter=%d | score=%.2f | total_issues=%d | severity=%s | strengths=%d | elapsed_ms=%.2f",
                chapter_number,
                overall_score,
                len(issues),
                severity_counts,
                len(strengths),
                elapsed_ms,
            )

            cache_skip_reasons: list[str] = []
            if timed_out_checks:
                cache_skip_reasons.append("timeout")
            if failed_checks:
                cache_skip_reasons.append("check_failed")

            report = CritiqueReport(
                chapter_number=chapter_number,
                overall_score=round(overall_score, 2),
                issues=issues,
                strengths=strengths,
                warnings=warnings,
                alignment_result=alignment_result,
                metadata={
                    "issue_count_by_severity": severity_counts,
                    "execution": {
                        "mode": "parallel_checks",
                        "character_check_mode": "batched",
                        "checks_executed": task_names,
                        "checks_failed": failed_checks,
                        "checks_timed_out": timed_out_checks,
                        "checks_incomplete": incomplete_checks,
                        "incomplete_ratio": round(incomplete_ratio, 3),
                        "timeout_s": self._check_timeout_s,
                        "timeout_windows_s": timeout_windows_s,
                        "timeout_extensions_allowed": self._timeout_extend_attempts,
                        "timeout_extensions_used": timeout_extensions_used,
                        "timeout_extend_multiplier": self._timeout_extend_multiplier,
                        "cache_hit": False,
                        "cache_key_prefix": cache_key[:12] if cache_key else "",
                        "cache_size": len(self._result_cache),
                        "cache_max_entries": self._cache_max_entries,
                        "cache_stats": dict(self._cache_stats),
                        "cache_store_skipped": bool(cache_skip_reasons),
                        "cache_skip_reasons": cache_skip_reasons,
                        "routing_task_types": [
                            TaskType.CRITIC_CONTINUITY.value,
                            TaskType.CRITIC_CHARACTER.value,
                            TaskType.CRITIC_CAUSAL.value,
                            TaskType.CRITIC_STRENGTHS.value,
                            TaskType.CHECK_ALIGNMENT.value,
                        ],
                    },
                },
            )

            can_cache = (
                self._cache_enabled
                and bool(cache_key)
                and not timed_out_checks
                and not failed_checks
            )
            if can_cache:
                await self._store_cached_report(cache_key, report)
                report.metadata.setdefault("execution", {})["cache_size"] = len(self._result_cache)
                report.metadata.setdefault("execution", {})["cache_stats"] = dict(self._cache_stats)

            if self._episodic_memory:
                try:
                    await self._episodic_memory.index_critique(chapter_number, report)
                except Exception as exc:
                    _log.warning(
                        "critique_indexing_failed | chapter=%d | error=%s",
                        chapter_number,
                        exc,
                    )

            return report

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "critique_failed | chapter=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    async def _check_chapter_continuity(
        self,
        chapter_number: int,
        chapter_text: str,
        previous_chapter_text: str,
        canon_state: StoryKernel,
        chapter_bridge: ChapterBridge | None = None,
        chapter_plan: ChapterPlan | None = None,
        previous_chapter_ending: str = "",
    ) -> list[CritiqueIssue]:
        """Check continuity with previous chapter."""
        issues: list[CritiqueIssue] = []

        # Build character profiles from canon state for the template
        character_profiles = self._build_character_profiles_for_critic(
            canon_state,
            chapter_text + "\n" + previous_chapter_text,
        )

        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CRITIC_CONTINUITY,
                    {
                        "chapter_number": chapter_number,
                        "current_chapter": chapter_text,
                        "previous_chapter": previous_chapter_text,
                        "canon_state_summary": self._summarize_canon_state(canon_state),
                        "chapter_bridge": self._serialize_bridge_for_prompt(chapter_bridge),
                        "chapter_plan": self._serialize_plan_for_prompt(chapter_plan),
                        "character_profiles": character_profiles,
                        "previous_chapter_ending": previous_chapter_ending[-600:]
                        if previous_chapter_ending
                        else "",
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.CRITIC_CONTINUITY,
                        max(1200, len(chapter_text) // 6),
                        prompt_overhead=2600,
                        min_tokens=1024,
                    ),
                    temperature=0.2,
                )
            )

            data = safe_parse_json(response.content)

            for item in data.get("issues", []):
                if not isinstance(item, dict):
                    continue
                severity = self._normalize_issue_severity(item.get("severity")) or "medium"
                summary = (
                    stringify_text_value(item.get("summary"))
                    or stringify_text_value(item.get("description"))
                    or stringify_text_value(item.get("issue"))
                    or "跨章连续性问题"
                )
                suggested_fix = (
                    stringify_text_value(item.get("suggested_fix"))
                    or stringify_text_value(item.get("fix_suggestion"))
                    or stringify_text_value(item.get("fix"))
                )
                issues.append(
                    CritiqueIssue(
                        issue_type="continuity_error",
                        severity=severity,
                        summary=summary,
                        evidence=stringify_text_value(item.get("evidence", "")),
                        affected_chapters=[chapter_number - 1, chapter_number],
                        suggested_fix=suggested_fix,
                        confidence=self._coerce_confidence(item.get("confidence", 0.5)),
                    )
                )

        except Exception as exc:
            _log.warning(
                "critic_continuity_check_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

        return issues

    async def _check_character_consistency(
        self,
        chapter_number: int,
        chapter_text: str,
        canon_state: StoryKernel,
        audit_context: Any | None = None,
        memory_hints: dict[str, Any] | None = None,
    ) -> list[CritiqueIssue]:
        """Check character consistency against canon state.

        Optimized path:
        - Batch multiple character checks into one LLM call (same TaskType for routing compatibility)
        - Fallback to legacy per-character checks if batch call fails
        """
        issues: list[CritiqueIssue] = []
        selected_characters = self._select_relevant_characters(
            canon_state,
            chapter_text,
        )
        if not selected_characters:
            return issues

        history_jobs = [
            self._build_character_history_context(
                name=name,
                chapter_number=chapter_number,
                audit_context=audit_context,
                memory_hints=memory_hints,
            )
            for name, _ in selected_characters
        ]
        history_results = await asyncio.gather(*history_jobs, return_exceptions=True)

        character_batch: list[dict[str, Any]] = []
        assert len(selected_characters) == len(history_results), (
            "selected_characters and history_results must have same length"
        )
        for (name, char_state), history_result in zip(
            selected_characters,
            history_results,
            strict=True,
        ):
            historical_context = ""
            if isinstance(history_result, str):
                historical_context = history_result
            character_batch.append(
                {
                    "character_name": name,
                    "canon_state": self._serialize_character_state(char_state),
                    "historical_context": historical_context,
                }
            )

        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CRITIC_CHARACTER,
                    {
                        "character_batch": character_batch,
                        "chapter_text": chapter_text,
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.CRITIC_CHARACTER,
                        max(1400, len(character_batch) * 600),
                        prompt_overhead=2600,
                        min_tokens=1536,
                    ),
                    temperature=0.2,
                )
            )

            data = safe_parse_json(response.content)
            issues.extend(
                self._parse_character_batch_issues(
                    data=data,
                    chapter_number=chapter_number,
                )
            )
            _log.debug(
                "critique_character_batch_done | chapter=%d | batch_size=%d | issues=%d",
                chapter_number,
                len(character_batch),
                len(issues),
            )
            return issues
        except Exception as exc:
            _log.warning(
                "critique_character_batch_failed | chapter=%d | error=%s | fallback=legacy",
                chapter_number,
                exc,
            )
            return await self._check_character_consistency_legacy(
                chapter_number=chapter_number,
                chapter_text=chapter_text,
                selected_characters=selected_characters,
                audit_context=audit_context,
                memory_hints=memory_hints,
            )

    async def _check_character_consistency_legacy(
        self,
        chapter_number: int,
        chapter_text: str,
        selected_characters: list[tuple[str, Any]],
        audit_context: Any | None = None,
        memory_hints: dict[str, Any] | None = None,
    ) -> list[CritiqueIssue]:
        """Legacy per-character checks used as fallback for reliability."""
        issues: list[CritiqueIssue] = []

        for name, char_state in selected_characters:
            try:
                response = await self._router.route(
                    self._builder.build(
                        TaskType.CRITIC_CHARACTER,
                        {
                            "character_name": name,
                            "canon_state": self._serialize_character_state(char_state),
                            "historical_context": await self._build_character_history_context(
                                name=name,
                                chapter_number=chapter_number,
                                audit_context=audit_context,
                                memory_hints=memory_hints,
                            ),
                            "chapter_text": chapter_text,
                        },
                        max_tokens=self._dynamic_max_tokens(
                            TaskType.CRITIC_CHARACTER,
                            900,
                            prompt_overhead=2200,
                            min_tokens=768,
                        ),
                        temperature=0.2,
                    )
                )
                data = safe_parse_json(response.content)

                if data.get("has_inconsistency"):
                    severity = self._normalize_issue_severity(data.get("severity"))
                    if severity is None:
                        continue
                    issues.append(
                        CritiqueIssue(
                            issue_type="character_inconsistency",
                            severity=severity,
                            summary=stringify_text_value(data.get("summary", "")),
                            evidence=stringify_text_value(data.get("evidence", "")),
                            affected_chapters=[chapter_number],
                            suggested_fix=stringify_text_value(data.get("suggested_fix", "")),
                            confidence=self._coerce_confidence(data.get("confidence", 0.5)),
                        )
                    )
            except Exception:
                continue

        return issues

    def _select_relevant_characters(
        self,
        canon_state: StoryKernel,
        chapter_text: str,
    ) -> list[tuple[str, Any]]:
        """Select chapter-visible characters without locally ranking their importance."""
        all_characters = list(canon_state.get_all_characters().items())
        if not all_characters:
            return []

        chapter_text = str(chapter_text or "")
        mentioned = [
            (name, state) for name, state in all_characters if name and name in chapter_text
        ]
        return mentioned or all_characters

    async def _build_character_history_context(
        self,
        name: str,
        chapter_number: int,
        audit_context: Any | None = None,
        memory_hints: dict[str, Any] | None = None,
    ) -> str:
        """Build short historical context for a character from memory.

        Priority order:
        1. Use audit_context.character_histories if available (pre-computed)
        2. Use memory_hints.relevant_history if available (planning-stage search)
        3. Fallback to episodic memory semantic search
        """
        if audit_context is not None:
            char_histories = getattr(audit_context, "character_histories", None)
            if char_histories and isinstance(char_histories, dict):
                history_list = char_histories.get(name, [])
                if history_list:
                    return "\n".join(
                        str(h.get("event_summary", "") or getattr(h, "event_summary", ""))
                        for h in history_list
                        if h
                    )

        if memory_hints is not None:
            relevant_history = memory_hints.get("relevant_history", [])
            if relevant_history:
                matching = [
                    h for h in relevant_history if name in str(h.get("event_summary", "") or "")
                ]
                if matching:
                    return "\n".join(str(h.get("event_summary", "")) for h in matching)

        if not self._episodic_memory:
            return ""
        try:
            results = await self._episodic_memory.search_by_semantic(
                query=f"{name} 的状态 情绪 位置",
                chapter_range=(max(1, chapter_number - 10), chapter_number - 1),
                top_k=2,
            )
            if not results:
                return ""
            return "\n".join(r.event_summary for r in results)
        except Exception:
            return ""

    def _serialize_character_state(self, char_state: Any) -> dict[str, Any]:
        """Serialize character state to prompt-safe structure."""
        if isinstance(char_state, dict):
            return {
                "alive": char_state.get("alive", True),
                "location": char_state.get("location", ""),
                "emotional_state": char_state.get("emotional_state", ""),
                "inventory": char_state.get("inventory", []),
                "notes": char_state.get("notes", ""),
            }
        return {
            "alive": getattr(char_state, "alive", True),
            "location": getattr(char_state, "location", ""),
            "emotional_state": getattr(char_state, "emotional_state", ""),
            "inventory": getattr(char_state, "inventory", []),
            "notes": getattr(char_state, "notes", ""),
        }

    def _parse_character_batch_issues(
        self,
        data: dict[str, Any],
        chapter_number: int,
    ) -> list[CritiqueIssue]:
        """Parse batched character-check response into CritiqueIssue list."""
        issues: list[CritiqueIssue] = []
        raw_issues = data.get("issues", [])

        if isinstance(raw_issues, list) and raw_issues:
            for item in raw_issues:
                if not isinstance(item, dict):
                    continue
                severity = self._normalize_issue_severity(item.get("severity"))
                if severity is None:
                    continue
                character_name = str(item.get("character_name") or item.get("name") or "").strip()
                summary = str(item.get("summary", "") or "").strip()
                if character_name and summary and character_name not in summary:
                    summary = f"{character_name}：{summary}"
                issues.append(
                    CritiqueIssue(
                        issue_type="character_inconsistency",
                        severity=severity,
                        summary=summary or "角色一致性问题",
                        evidence=stringify_text_value(item.get("evidence", "")),
                        affected_chapters=[chapter_number],
                        suggested_fix=stringify_text_value(item.get("suggested_fix", "")),
                        confidence=self._coerce_confidence(
                            item.get("confidence", data.get("confidence", 0.5))
                        ),
                    )
                )
            return issues

        # Backward-compatible parsing for single-character format
        if data.get("has_inconsistency"):
            severity = self._normalize_issue_severity(data.get("severity"))
            if severity is not None:
                issues.append(
                    CritiqueIssue(
                        issue_type="character_inconsistency",
                        severity=severity,
                        summary=stringify_text_value(data.get("summary")) or "角色一致性问题",
                        evidence=stringify_text_value(data.get("evidence", "")),
                        affected_chapters=[chapter_number],
                        suggested_fix=stringify_text_value(data.get("suggested_fix", "")),
                        confidence=self._coerce_confidence(data.get("confidence", 0.5)),
                    )
                )
        return issues

    async def _check_causal_chain(
        self,
        chapter_number: int,
        chapter_text: str,
        chapter_outline: ChapterOutline,
        canon_state: StoryKernel,
        chapter_bridge: ChapterBridge | None = None,
        previous_chapter_ending: str = "",
    ) -> list[CritiqueIssue]:
        """Check causal chain integrity."""
        issues: list[CritiqueIssue] = []

        prev_exit_state = canon_state.chapter_exit_states.get(chapter_number - 1)
        if not prev_exit_state:
            return issues

        causal_link = None
        if chapter_bridge and chapter_bridge.causal_link:
            causal_link = chapter_bridge.causal_link

        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CRITIC_CAUSAL,
                    {
                        "chapter_number": chapter_number,
                        "chapter_outline_goal": chapter_outline.goal,
                        "causal_link": self._serialize_causal_link_for_prompt(causal_link),
                        "chapter_bridge": self._serialize_bridge_for_prompt(chapter_bridge),
                        "previous_exit_state": {
                            "location": prev_exit_state.location,
                            "active_goals": prev_exit_state.active_goals,
                            "open_questions": prev_exit_state.open_questions,
                        },
                        "previous_chapter_ending": previous_chapter_ending[-800:]
                        if previous_chapter_ending
                        else "",
                        "chapter_text": chapter_text,
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.CRITIC_CAUSAL,
                        max(1000, len(chapter_text) // 8),
                        prompt_overhead=2400,
                        min_tokens=1024,
                    ),
                    temperature=0.2,
                )
            )

            data = safe_parse_json(response.content)

            for item in data.get("causal_breaks", []):
                if not isinstance(item, dict):
                    continue
                severity = self._normalize_issue_severity(item.get("severity")) or "high"
                summary = (
                    stringify_text_value(item.get("summary"))
                    or stringify_text_value(item.get("description"))
                    or stringify_text_value(item.get("issue"))
                    or "因果链断裂"
                )
                issues.append(
                    CritiqueIssue(
                        issue_type="causal_break",
                        severity=severity,
                        summary=summary,
                        evidence=stringify_text_value(item.get("evidence", "")),
                        affected_chapters=[chapter_number - 1, chapter_number],
                        suggested_fix=(
                            stringify_text_value(item.get("fix"))
                            or stringify_text_value(item.get("suggested_fix"))
                            or stringify_text_value(item.get("fix_suggestion"))
                        ),
                        confidence=self._coerce_confidence(item.get("confidence", 0.5)),
                    )
                )

        except Exception as exc:
            _log.warning(
                "critic_causal_chain_check_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

        return issues

    async def _check_plot_threads(
        self,
        chapter_number: int,
        chapter_text: str,
        canon_state: StoryKernel,
    ) -> list[CritiqueIssue]:
        """Check for unresolved or forgotten plot threads."""
        issues: list[CritiqueIssue] = []

        # Check active plot threads. StoryKernel stores these as a list, while
        # older tests/artifacts may still pass a dict keyed by thread id.
        for thread_id, thread in _iter_plot_thread_items(canon_state, limit=5):
            last_touched = _thread_int(thread, "last_touched_chapter")
            chapters_since_touch = chapter_number - last_touched

            if chapters_since_touch > 10:
                title = _thread_text(thread, "title") or thread_id
                # Thread hasn't been touched in a while
                issues.append(
                    CritiqueIssue(
                        issue_type="unresolved_thread",
                        severity="medium" if chapters_since_touch < 15 else "high",
                        summary=f"主线线程「{title}」已{chapters_since_touch}章未推进",
                        evidence=f"上次提及：第{last_touched}章",
                        affected_chapters=[last_touched, chapter_number],
                        suggested_fix=f"考虑在本章或下一章中推进{title}线程",
                        confidence=1.0,
                    )
                )

        # Use episodic memory to find forgotten setups
        if self._episodic_memory:
            # Search for unresolved questions
            results = await self._episodic_memory.search_by_semantic(
                query="未解决的问题 悬念 待回答",
                chapter_range=(1, chapter_number - 1),
                top_k=5,
                min_relevance=0.5,
            )

            for result in results:
                # Check if this question was answered in current chapter
                if chapter_number - result.chapter_number > 5:
                    issues.append(
                        CritiqueIssue(
                            issue_type="forgotten_setup",
                            severity="low",
                            summary=f"第{result.chapter_number}章的悬念可能已被遗忘",
                            evidence=result.event_summary,
                            affected_chapters=[result.chapter_number, chapter_number],
                            suggested_fix=f"检查本章是否回应了第{result.chapter_number}章的悬念",
                            confidence=result.relevance_score,
                        )
                    )

        return issues

    async def _check_thematic_coherence(
        self,
        chapter_number: int,
        chapter_text: str,
        canon_state: StoryKernel,
    ) -> list[CritiqueIssue]:
        """Check thematic coherence using motif tracker."""
        issues: list[CritiqueIssue] = []

        if not self._motif_tracker:
            return issues

        # Check for unintentional repetition
        repetitions = await self._motif_tracker.check_unintentional_repetition(
            chapter_number,
            chapter_text,
            lookback_chapters=self._motif_repetition_lookback_chapters,
            repetition_gap_chapters=self._motif_repetition_recent_gap_chapters,
        )

        for rep in repetitions:
            issues.append(
                CritiqueIssue(
                    issue_type="thematic_drift",
                    severity=_normalize_critic_severity(rep.severity, default="medium") or "medium",
                    summary=f"无意识的意象重复：{rep.motif_name}",
                    evidence=rep.text_snippet,
                    affected_chapters=[chapter_number] + rep.previous_chapters,
                    suggested_fix=rep.suggestion,
                    confidence=rep.similarity_score,
                )
            )

        return issues

    async def _check_style_rule_repetition(
        self,
        chapter_number: int,
        chapter_text: str,
    ) -> list[CritiqueIssue]:
        """Flag writing-technique rules that have calcified into cross-chapter templates.

        Complements ``_check_thematic_coherence``: that checks imagery/motif
        repetition; this checks ``style_profile.modules`` rule repetition (e.g.
        the "穿堂风三层写法" technique firing every chapter). Detection is
        deterministic (rule-cue substring scan), so this is a cheap local check.
        """
        issues: list[CritiqueIssue] = []

        if not self._style_rule_tracker:
            return issues

        repetitions = self._style_rule_tracker.check_rule_repetition(
            chapter_number,
            chapter_text,
            lookback_chapters=self._style_rule_lookback_chapters,
            repetition_gap_chapters=self._style_rule_repetition_gap_chapters,
        )
        for rep in repetitions:
            issues.append(
                CritiqueIssue(
                    issue_type="style_template_drift",
                    severity=_normalize_critic_severity(rep.severity, default="medium") or "medium",
                    summary=f"写作技法模板化：{rep.module_name}·{rep.rule_name}",
                    evidence=rep.text_snippet,
                    affected_chapters=[chapter_number] + rep.previous_chapters,
                    suggested_fix=rep.suggestion,
                    confidence=rep.similarity_score,
                )
            )
        return issues

    async def _check_alignment(
        self,
        chapter_number: int,
        chapter_text: str,
        chapter_outline: ChapterOutline,
        chapter_plan: ChapterPlan | None,
    ) -> AlignmentResult:
        """Check alignment between chapter text and outline/plan.

        This method integrates alignment checking into the critique flow,
        replacing the need for a separate AlignmentStep.
        """
        alignment_result = AlignmentResult()

        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CHECK_ALIGNMENT,
                    {
                        "chapter_outline": chapter_outline,
                        "chapter_plan": chapter_plan,
                        "chapter_text": chapter_text,
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.CHECK_ALIGNMENT,
                        max(2200, len(chapter_text) // 4),
                        prompt_overhead=3200,
                        min_tokens=2048,
                    ),
                    temperature=0.2,
                )
            )

            data = safe_parse_json(response.content)

            missing_main = self._to_string_list(data.get("missing_main_points", []))
            weak_subplots = self._to_string_list(data.get("weak_subplot_points", []))
            supportive_subplots = self._to_string_list(data.get("supportive_subplot_points", []))
            repair_actions = self._to_string_list(data.get("repair_actions", []))

            raw_model_score = data.get("alignment_score")
            model_score = self._coerce_score(raw_model_score, default=8.5)
            model_risk = self._normalize_risk_level(data.get("risk_level"))

            # ── Degraded-response detection ───────────────────────────────
            # When the LLM returns alignment_score as 0 or a near-zero value
            # while also reporting missing_main_points, the response is likely
            # truncated or partially decoded.  A genuine low score with real
            # misses would still have a non-trivial model_score (> 1.0).
            # Marking this prevents the calibration formula from producing a
            # distorted score (e.g. 1.8) that triggers unnecessary repairs.
            is_degraded = (
                raw_model_score is not None and model_score < 1.0 and len(missing_main) > 0
            )

            rule_score = self._calibrate_alignment_score(
                missing_count=len(missing_main),
                weak_count=len(weak_subplots),
                supportive_count=len(supportive_subplots),
            )

            if missing_main:
                # When the LLM's own score significantly exceeds the rule score,
                # the missing_main_points likely contain false positives (the LLM
                # marked content as "missing" when it actually exists in different
                # phrasing).  The prompt instructs the LLM to use the same formula,
                # so a positive deviation reveals the LLM's internal assessment
                # that the "missing" items are partially covered.
                _gap = model_score - rule_score
                if _gap >= 3.0:
                    # Very large discrepancy: LLM strongly disagrees with its own
                    # formula → likely false-positive misses.  Trust LLM heavily.
                    calibrated_score = round(0.7 * model_score + 0.3 * rule_score, 1)
                elif _gap >= 1.5:
                    # Moderate discrepancy: partial coverage likely.
                    calibrated_score = round(0.6 * model_score + 0.4 * rule_score, 1)
                else:
                    # Small or negative discrepancy: trust the formula more.
                    calibrated_score = round(0.3 * model_score + 0.7 * rule_score, 1)
                calibrated_score = max(0.0, min(10.0, calibrated_score))
            else:
                calibrated_score = round((0.4 * model_score) + (0.6 * rule_score), 1)
                calibrated_score = max(0.0, min(10.0, calibrated_score))

            calibrated_risk = self._calibrate_alignment_risk(
                model_risk=model_risk,
                calibrated_score=calibrated_score,
                missing_count=len(missing_main),
                weak_count=len(weak_subplots),
            )

            summary = data.get("summary", "")
            if not summary:
                if len(missing_main) >= 1:
                    summary = "章节推进与大纲存在关键缺口，建议优先修复主线点。"
                elif len(weak_subplots) >= 1:
                    summary = "章节总体可用，但支线推进偏弱，建议补充承接细节。"
                else:
                    summary = "章节与大纲总体一致。"

            alignment_result = AlignmentResult(
                alignment_score=calibrated_score,
                risk_level=calibrated_risk,
                summary=summary,
                missing_main_points=missing_main,
                supportive_subplot_points=supportive_subplots,
                weak_subplot_points=weak_subplots,
                repair_actions=repair_actions,
                is_degraded=is_degraded,
                degraded_reason="model_score_near_zero_with_misses" if is_degraded else "",
            )

        except Exception as exc:  # noqa: BLE001
            _log.warning("_build_alignment_result failed, returning default | error=%s", exc)

        return alignment_result

    @staticmethod
    def _coerce_confidence(value: Any, default: float = 0.5) -> float:
        """Coerce confidence into [0, 1]."""
        return _coerce_critic_confidence(value, default=default)

    @staticmethod
    def _normalize_issue_severity(
        value: Any,
    ) -> Literal["critical", "high", "medium", "low"] | None:
        """Normalize severity values and drop neutral labels."""
        return _normalize_critic_severity(value, default="medium", allow_none=True)

    @staticmethod
    def _coerce_score(value: Any, default: float = 7.5) -> float:
        """Coerce a value to a score between 0 and 10."""
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = default
        return max(0.0, min(10.0, score))

    @staticmethod
    def _normalize_risk_level(value: Any) -> str:
        """Normalize risk level string to low/medium/high."""
        lowered = str(value).lower().strip()
        risk_alias = {
            "low": "low",
            "minor": "low",
            "safe": "low",
            "medium": "medium",
            "mid": "medium",
            "moderate": "medium",
            "high": "high",
            "severe": "high",
            "critical": "high",
        }
        return risk_alias.get(lowered, "medium")

    @staticmethod
    def _calibrate_alignment_score(
        missing_count: int,
        weak_count: int,
        supportive_count: int,
    ) -> float:
        """Calibrate alignment score based on detected issues.

        Uses diminishing penalties for multiple missing main points:
        the first miss is penalised most heavily (2.0), subsequent
        misses at 1.5 each, reflecting that mass "missing" reports
        often indicate coverage-style differences rather than truly
        absent content.
        """
        if missing_count <= 0:
            mainline_deduction = 0.0
        elif missing_count == 1:
            mainline_deduction = 2.0
        else:
            # First miss: 2.0, each subsequent miss: 1.5
            mainline_deduction = 2.0 + 1.5 * (missing_count - 1)
        mainline_deduction = min(8.0, mainline_deduction)
        disruptive_subplot_deduction = min(2.0, 0.4 * max(0, weak_count - 1))
        supportive_bonus = min(0.6, 0.2 * supportive_count)
        calibrated = 10.0 - mainline_deduction - disruptive_subplot_deduction + supportive_bonus
        return round(calibrated, 1)

    @staticmethod
    def _calibrate_alignment_risk(
        model_risk: str,
        calibrated_score: float,
        missing_count: int,
        weak_count: int,
    ) -> str:
        """Calibrate risk level based on score and issue counts."""
        risk_order = {"low": 0, "medium": 1, "high": 2}
        rank = risk_order.get(model_risk, 1)

        if calibrated_score < 8.0:
            rank = max(rank, 1)
        if calibrated_score < 6.5:
            rank = max(rank, 2)
        if missing_count >= 1:
            rank = max(rank, 1)
        if missing_count >= 2:
            rank = max(rank, 2)
        if weak_count >= 3:
            rank = max(rank, 1)
        if weak_count >= 5:
            rank = max(rank, 2)

        return ("low", "medium", "high")[rank]

    @classmethod
    def _to_string_list(cls, value: Any) -> list[str]:
        """Convert various value types to a list of strings."""
        import re

        items: list[str] = []

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for raw_key, raw_val in item.items():
                        key = cls._clean_str(raw_key)
                        val = cls._clean_str(raw_val)
                        if key and val:
                            items.append(f"{key}: {val}")
                        elif key:
                            items.append(key)
                        elif val:
                            items.append(val)
                else:
                    text = cls._clean_str(item)
                    if text:
                        items.append(text)
        elif isinstance(value, dict):
            for raw_key, raw_val in value.items():
                key = cls._clean_str(raw_key)
                val = cls._clean_str(raw_val)
                if key and val:
                    items.append(f"{key}: {val}")
                elif key:
                    items.append(key)
                elif val:
                    items.append(val)
        elif isinstance(value, str):
            fragments = [part.strip() for part in re.split(r"[;\n；]+", value)]
            items.extend(fragment for fragment in fragments if fragment)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _clean_str(value: Any) -> str:
        """Clean a value to string."""
        if value is None:
            return ""
        return str(value).strip()

    async def _identify_strengths(
        self,
        chapter_number: int,
        chapter_text: str,
        chapter_outline: ChapterOutline,
    ) -> list[str]:
        """Identify chapter strengths."""
        strengths: list[str] = []

        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CRITIC_STRENGTHS,
                    {
                        "chapter_number": chapter_number,
                        "chapter_outline": {
                            "goal": chapter_outline.goal,
                            "main_plot_points": chapter_outline.main_plot_points,
                        },
                        "chapter_text": chapter_text,
                    },
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.CRITIC_STRENGTHS,
                        900,
                        prompt_overhead=2000,
                        min_tokens=768,
                    ),
                    temperature=0.3,
                )
            )

            data = safe_parse_json(response.content)
            raw = data.get("strengths", [])
            # 模板返回 list[dict]，需转换为 list[str] 以匹配下游契约
            for item in raw:
                if isinstance(item, dict):
                    cat = item.get("category", "")
                    summary = item.get("summary", "")
                    strengths.append(f"[{cat}] {summary}" if cat else summary)
                elif isinstance(item, str):
                    strengths.append(item)

        except Exception as exc:
            _log.warning(
                "critic_identify_strengths_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

        return strengths

    def _generate_pattern_warnings(self, issues: list[CritiqueIssue]) -> list[str]:
        """Generate warnings for detected patterns."""
        warnings: list[str] = []

        # Check for repeated issue types
        issue_types: dict[str, int] = {}
        for issue in issues:
            issue_types[issue.issue_type] = issue_types.get(issue.issue_type, 0) + 1

        for issue_type, count in issue_types.items():
            if count >= 3:
                warnings.append(f"注意：检测到{count}个{issue_type}问题，建议系统性检查")

        # Check for severity patterns
        critical_count = sum(1 for i in issues if i.severity == "critical")
        high_count = sum(1 for i in issues if i.severity == "high")

        if critical_count > 0:
            warnings.append(f"警告：存在{critical_count}个严重问题，需要立即修复")
        elif high_count >= 3:
            warnings.append(f"警告：存在{high_count}个高优先级问题")

        return warnings

    def _calculate_overall_score(self, issues: list[CritiqueIssue]) -> float:
        """Calculate overall chapter score based on issues.

        Uses per-severity caps and diminishing penalties so that many
        medium/low issues don't brick the score to 0.0.  The raw
        penalty structure remains aggressive for critical/high, but
        the cap prevents an avalanche of minor findings from masking
        the real signal.
        """
        score = 10.0

        # Base penalty per occurrence
        severity_penalties = {
            "critical": 3.0,
            "high": 1.5,
            "medium": 0.5,
            "low": 0.2,
        }
        # Max total deduction per category
        severity_caps = {
            "critical": 6.0,  # max 2 full critical penalties
            "high": 4.5,  # max 3 full high penalties
            "medium": 2.0,  # cap medium deductions
            "low": 0.6,  # cap low deductions
        }

        accumulated: dict[str, float] = {}
        for issue in issues:
            sev = issue.severity
            penalty = severity_penalties.get(sev, 0.5)
            cap = severity_caps.get(sev, 2.0)
            current = accumulated.get(sev, 0.0)
            actual = min(penalty, cap - current)
            if actual > 0:
                accumulated[sev] = current + actual
                score -= actual

        return max(0.0, min(10.0, score))

    def _summarize_canon_state(self, canon_state: StoryKernel) -> str:
        """Generate a brief summary of canon state for context."""
        if canon_state is None:
            return ""

        parts: list[str] = []
        if isinstance(canon_state, dict):
            characters = canon_state.get("characters", {}) or {}
            timeline = canon_state.get("timeline", []) or []
            foreshadowing = canon_state.get("foreshadowing", []) or []
        else:
            characters = getattr(canon_state, "characters", {}) or {}
            timeline = getattr(canon_state, "timeline", []) or []
            foreshadowing = getattr(canon_state, "foreshadowing", []) or []

        # Active characters
        char_count = len(characters)
        parts.append(f"活跃角色：{char_count}人")

        # Recent events
        recent_events = list(timeline)[-5:]
        if recent_events:
            tail_event = recent_events[-1]
            event_text = (
                str(tail_event.get("event", "") or "")
                if isinstance(tail_event, dict)
                else str(getattr(tail_event, "event", "") or "")
            )
            if event_text:
                parts.append(f"最近事件：{event_text}")

        # Active foreshadowing
        active_fs_count = len(
            [
                fs
                for fs in foreshadowing
                if (
                    str(fs.get("status", "") or "").upper() in ("PLANTED", "REINFORCED")
                    if isinstance(fs, dict)
                    else str(getattr(getattr(fs, "status", None), "value", "") or "").upper()
                    in ("PLANTED", "REINFORCED")
                )
            ]
        )
        parts.append(f"活跃伏笔：{active_fs_count}个")

        return "；".join(parts)

    async def _get_cached_report(self, cache_key: str) -> CritiqueReport | None:
        """Get report from LRU cache and refresh recency."""
        async with self._cache_lock:
            cached = self._result_cache.get(cache_key)
            if cached is None:
                self._cache_stats["misses"] = self._cache_stats.get("misses", 0) + 1
                return None
            self._result_cache.move_to_end(cache_key, last=True)
            self._cache_stats["hits"] = self._cache_stats.get("hits", 0) + 1
            return cached

    async def _store_cached_report(self, cache_key: str, report: CritiqueReport) -> None:
        """Store report into LRU cache."""
        async with self._cache_lock:
            self._result_cache[cache_key] = copy.deepcopy(report)
            self._result_cache.move_to_end(cache_key, last=True)
            self._cache_stats["stores"] = self._cache_stats.get("stores", 0) + 1

            while len(self._result_cache) > self._cache_max_entries:
                self._result_cache.popitem(last=False)
                self._cache_stats["evictions"] = self._cache_stats.get("evictions", 0) + 1

    async def get_recent_critiques(
        self,
        chapter_number: int,
        *,
        lookback: int = 6,
        min_severity: str = "medium",
    ) -> list[CritiqueReport]:
        """Return recent critique reports for chapters near *chapter_number*.

        Used by ``stage_memory_builder`` to count recent high-severity critiques
        for dynamic history lookback calculation.

        Args:
            chapter_number: Reference chapter number.
            lookback: Number of chapters to look back (default 6).
            min_severity: Minimum severity threshold ("low", "medium", "high", "critical").

        Returns:
            List of CritiqueReport objects with at least one issue at or above
            the specified severity.
        """
        severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = severity_order.get(min_severity, 1)

        results: list[CritiqueReport] = []
        start_ch = max(1, chapter_number - lookback)

        async with self._cache_lock:
            for _key, report in self._result_cache.items():
                if not isinstance(report, CritiqueReport):
                    continue
                report_ch = getattr(report, "chapter_number", 0) or 0
                if start_ch <= report_ch < chapter_number:
                    max_sev = max(
                        (severity_order.get(i.severity, 0) for i in report.issues),
                        default=0,
                    )
                    if max_sev >= threshold:
                        results.append(report)

        results.sort(key=lambda r: getattr(r, "chapter_number", 0) or 0, reverse=True)
        return results

    def _build_critique_cache_key(
        self,
        *,
        chapter_number: int,
        chapter_text: str,
        canon_state: Any,
        chapter_outline: Any,
        previous_chapter_text: str | None,
        chapter_plan: Any,
        genre: str,
        check_alignment: bool,
        chapter_bridge: Any = None,
        previous_chapter_ending: str = "",
        audit_context: Any = None,
        memory_hints: dict[str, Any] | None = None,
    ) -> str:
        """Build a deterministic, route-aware cache key for critique input."""
        payload = {
            "chapter_number": int(chapter_number),
            "chapter_text_hash": self._hash_text(chapter_text),
            "previous_chapter_hash": self._hash_text(previous_chapter_text or ""),
            "canon_hash": self._stable_hash(self._object_to_primitive(canon_state)),
            "canon_snapshot_hash": self._stable_hash(self._canon_cache_snapshot(canon_state)),
            "outline_hash": self._stable_hash(self._outline_cache_snapshot(chapter_outline)),
            "plan_hash": self._stable_hash(self._object_to_primitive(chapter_plan)),
            "bridge_hash": self._stable_hash(self._object_to_primitive(chapter_bridge)),
            "prev_ending_hash": self._hash_text(previous_chapter_ending or ""),
            "audit_context_hash": self._stable_hash(self._object_to_primitive(audit_context)),
            "memory_hints_hash": self._stable_hash(self._object_to_primitive(memory_hints)),
            "genre": str(genre or ""),
            "check_alignment": bool(check_alignment),
            "route_fingerprint": self._build_route_fingerprint(),
        }
        return self._stable_hash(payload)

    def _build_route_fingerprint(self) -> str:
        """Build routing fingerprint so model-route changes invalidate cache."""
        task_types = [
            TaskType.CRITIC_CONTINUITY,
            TaskType.CRITIC_CHARACTER,
            TaskType.CRITIC_CAUSAL,
            TaskType.CRITIC_STRENGTHS,
            TaskType.CHECK_ALIGNMENT,
        ]
        route_overrides = getattr(self._router, "task_route_overrides", {}) or {}
        normalized_routes: dict[str, dict[str, Any]] = {}
        for task in task_types:
            override = route_overrides.get(task) if hasattr(route_overrides, "get") else None
            if override is None:
                normalized_routes[task.value] = {}
                continue
            normalized_routes[task.value] = {
                "provider": str(getattr(override, "provider", "") or ""),
                "model_id": str(getattr(override, "model_id", "") or ""),
                "thinking": bool(getattr(override, "thinking", False)),
                "multi_turn": bool(getattr(override, "multi_turn", False)),
            }

        tier_to_model_raw = getattr(self._router, "_tier_to_model", {}) or {}
        normalized_tiers: dict[str, str] = {}
        if isinstance(tier_to_model_raw, dict):
            for key, value in tier_to_model_raw.items():
                tier_key = getattr(key, "value", str(key))
                normalized_tiers[str(tier_key)] = str(value or "")

        payload = {
            "default_provider": str(getattr(self._router, "default_provider", "") or ""),
            "routes": normalized_routes,
            "tier_to_model": normalized_tiers,
        }
        return self._stable_hash(payload)

    def _canon_cache_snapshot(self, canon_state: Any) -> dict[str, Any]:
        """Extract a compact canon snapshot used in cache key derivation."""
        if canon_state is None:
            return {}

        try:
            characters = sorted(str(name) for name in getattr(canon_state, "characters", {}).keys())
        except Exception:
            characters = []

        timeline_tail: list[str] = []
        try:
            timeline = list(getattr(canon_state, "timeline", []) or [])
            for event in timeline[-5:]:
                text = str(getattr(event, "event", "") or "").strip()
                if text:
                    timeline_tail.append(text[:120])
        except Exception:
            timeline_tail = []

        thread_positions: list[str] = []
        try:
            for thread_id, thread in _iter_plot_thread_items(canon_state, limit=30):
                last_touched = _thread_int(thread, "last_touched_chapter")
                thread_positions.append(f"{thread_id}:{last_touched}")
            thread_positions.sort()
        except Exception:
            thread_positions = []

        return {
            "characters": characters[:60],
            "timeline_tail": timeline_tail,
            "plot_threads": thread_positions,
            "canon_summary": self._summarize_canon_state(canon_state),
        }

    def _outline_cache_snapshot(self, chapter_outline: Any) -> dict[str, Any]:
        """Extract compact chapter-outline snapshot for cache key."""
        if chapter_outline is None:
            return {}

        if hasattr(chapter_outline, "model_dump"):
            try:
                raw = chapter_outline.model_dump(mode="json")
                if isinstance(raw, dict):
                    return {
                        "goal": raw.get("goal", ""),
                        "main_plot_points": raw.get("main_plot_points", []),
                    }
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "_extract_outline_context model_dump failed, using getattr fallback | error=%s",
                    exc,
                )

        return {
            "goal": str(getattr(chapter_outline, "goal", "") or ""),
            "main_plot_points": self._object_to_primitive(
                getattr(chapter_outline, "main_plot_points", [])
            ),
        }

    def _build_character_profiles_for_critic(
        self,
        canon_state: StoryKernel,
        chapter_context: str,
    ) -> list[dict[str, str]]:
        """Extract character profiles from canon state for critic templates."""
        profiles = []
        chars = getattr(canon_state, "characters", {}) or {}
        for name, state in chars.items():
            if not name:
                continue
            profile: dict[str, str] = {"name": name, "role": ""}
            if hasattr(state, "gender"):
                profile["gender"] = str(getattr(state, "gender", "") or "")
            if hasattr(state, "role"):
                profile["role"] = str(getattr(state, "role", "") or "")
            if hasattr(state, "social_status"):
                profile["social_status"] = str(getattr(state, "social_status", "") or "")
            if name in chapter_context:
                profiles.append(profile)
        return profiles

    @staticmethod
    def _serialize_bridge_for_prompt(bridge: ChapterBridge | None) -> dict[str, Any] | None:
        """Serialize ChapterBridge to prompt-safe dict."""
        if bridge is None:
            return None
        if hasattr(bridge, "model_dump"):
            try:
                return bridge.model_dump(mode="json")
            except Exception:
                pass
        return {
            "opening_time": str(getattr(bridge, "opening_time", "") or ""),
            "opening_location": str(getattr(bridge, "opening_location", "") or ""),
            "opening_pov": str(getattr(bridge, "opening_pov", "") or ""),
            "transition_mode": str(getattr(bridge, "transition_mode", "") or ""),
            "emotional_carryover": str(getattr(bridge, "emotional_carryover", "") or ""),
            "action_handoff": str(getattr(bridge, "action_handoff", "") or ""),
            "bridge_summary": str(getattr(bridge, "bridge_summary", "") or ""),
        }

    @staticmethod
    def _serialize_plan_for_prompt(plan: ChapterPlan | None) -> dict[str, Any] | None:
        """Serialize ChapterPlan to prompt-safe dict."""
        if plan is None:
            return None
        if hasattr(plan, "model_dump"):
            try:
                return plan.model_dump(mode="json")
            except Exception:
                pass
        return {
            "opening_contract": str(getattr(plan, "opening_contract", "") or ""),
            "closing_contract": str(getattr(plan, "closing_contract", "") or ""),
            "scene_intents": [
                {
                    "summary": str(getattr(s, "summary", "") or ""),
                    "required_outcome": str(getattr(s, "required_outcome", "") or ""),
                }
                for s in (getattr(plan, "scene_intents", []) or [])
            ],
        }

    @staticmethod
    def _serialize_causal_link_for_prompt(causal_link: Any) -> dict[str, Any] | None:
        """Serialize CausalLink to prompt-safe dict."""
        if causal_link is None:
            return None
        if hasattr(causal_link, "model_dump"):
            try:
                dumped = causal_link.model_dump(mode="json")
                if isinstance(dumped, dict):
                    return cast(dict[str, Any], dumped)
            except Exception:
                pass
        return {
            "previous_event": str(getattr(causal_link, "previous_event", "") or ""),
            "causal_mechanism": str(getattr(causal_link, "causal_mechanism", "") or ""),
            "unresolved_question": str(getattr(causal_link, "unresolved_question", "") or ""),
            "open_threads": list(getattr(causal_link, "open_threads", []) or []),
        }

    def _object_to_primitive(self, value: Any) -> Any:
        """Convert arbitrary object to JSON-serializable primitive recursively."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [self._object_to_primitive(v) for v in value]
        if isinstance(value, tuple):
            return [self._object_to_primitive(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._object_to_primitive(v) for k, v in value.items()}
        if is_dataclass(value) and not isinstance(value, type):
            try:
                return self._object_to_primitive(asdict(value))
            except Exception:
                return repr(value)
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump(mode="json")
                return self._object_to_primitive(dumped)
            except Exception:
                return repr(value)
        return repr(value)

    @staticmethod
    def _hash_text(text: str) -> str:
        """Hash text for cache key derivation."""
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_hash(value: Any) -> str:
        """Stable hash for nested structures."""
        try:
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            payload = repr(value)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
