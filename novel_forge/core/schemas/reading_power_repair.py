"""Reading power repair schemas and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from novel_forge.core.utils.audit_issue import severity_rank, stable_issue_id

if TYPE_CHECKING:
    from novel_forge.core.schemas.reading_power import ReadingPowerReport


@dataclass(frozen=True)
class ReadingPowerRepairInput:
    """Input for reading power repair."""

    chapter_text: str
    """Current chapter text to be repaired."""
    issues: list[Any]
    """List of issue summaries or structured issue objects describing what needs to be fixed."""
    expected_hook: str | dict[str, Any] | None
    """Expected hook type/description for this chapter ending."""
    expected_payoffs: list[Any]
    """Expected micro-payoff items that should be delivered in this chapter."""
    previous_hook_description: str
    """Description of the previous chapter's hook to ensure continuity."""
    chapter_number: int = 1
    """Chapter number, used for prompt context and logging."""
    reading_power_hint: dict[str, Any] | None = None
    """Optional cross-chapter reading power hint."""
    memory_guidance: dict[str, Any] | None = None
    """Optional historical repair guidance from episodic critique memory."""
    repair_attempt_guidance: dict[str, Any] | None = None
    """Round-specific diagnosis and new-direction guidance for non-repetitive retries."""
    style_profile: dict[str, Any] | None = None
    """Style profile for maintaining consistent writing style."""
    editorial_contract: Any | None = None
    """Project-level editorial contract card source for repair prompts."""
    forbidden_elements: list[str] = field(default_factory=list)
    """Hard-forbidden elements that must NOT appear in the repaired text."""
    forbidden_elements_soft: list[str] = field(default_factory=list)
    """Soft-forbidden elements that should be avoided when possible."""
    intentional_callbacks: list[str] = field(default_factory=list)
    """Intentional motif callbacks that are allowed even if similar to forbidden elements."""
    word_count_min: int = 0
    """Minimum allowed word count for the chapter."""
    word_count_max: int = 0
    """Maximum allowed word count for the chapter."""
    iteration: int = 1
    """Current repair iteration (1-based)."""
    character_profiles: list[str] = field(default_factory=list)
    """Whitelist of known characters that are allowed to appear in the repaired text."""
    kernel_context: dict[str, Any] | None = field(default=None)
    """StoryKernel field slices from ContextComposer. Fields are merged into
    the repair context without overriding explicit fields."""
    chapter_state_packet: Any | None = None
    """Full chapter packet for contract, bridge, and narrative-context cards."""
    chapter_outline: Any | None = None
    """Chapter outline used to scope POV, involved characters, and knowledge cards."""
    chapter_plan: Any | None = None
    """Chapter plan payload used to keep reading-power repair inside intended scene goals."""
    chapter_bridge: Any | None = None
    """Bridge payload used when repair needs opening or suspense continuity anchors."""
    chapter_source_slice: Any | None = None
    """ChapterSourceSlice projection for source artifact boundaries."""


@dataclass(frozen=True)
class ReadingPowerRepairLoopResult:
    """Result returned by the reading power repair loop."""

    current_text: str
    """The repaired or unchanged chapter text."""
    reading_power_report: dict[str, Any] | None
    """Reading power evaluation report after repair."""
    pre_repair_report: dict[str, Any] | None
    """Reading power evaluation report before repair."""
    repair_rounds_used: int
    """Number of repair rounds actually used."""
    applied: bool
    """Whether any repairs were actually applied."""
    warnings: list[str] = field(default_factory=list)
    """Any warnings generated during repair."""


@dataclass
class ReadingPowerIssue:
    """A single reading power issue detected within a chapter."""

    issue_type: str
    severity: str
    summary: str
    evidence: str
    fix_suggestion: str = ""
    location: str = ""
    issue_id: str = ""
    repair_surface: str = "chapter_text"
    status: str = "open"
    blocking: bool = False
    postconditions: list[dict[str, Any]] = field(default_factory=list)


def _reading_power_postconditions(issue_type: str) -> list[dict[str, Any]]:
    """Build lightweight acceptance checks for reading-power repairs."""
    normalized = str(issue_type or "").strip().lower()
    if normalized in {"hook_missing", "hook_too_weak", "outline_mismatch"}:
        return [
            {
                "validator_id": "reading_power_hook_validator",
                "description": "章尾存在具体、可感、可追踪的下一章阅读驱动力，且钩子强度不为 weak/none。",
                "evidence_hint": "chapter ending + hook_description",
                "required": True,
            }
        ]
    if normalized == "prev_hook_unfulfilled":
        return [
            {
                "validator_id": "reading_power_prev_hook_validator",
                "description": "上一章钩子在本章前中段得到明确回应、兑现、反转或阶段性推进。",
                "evidence_hint": "previous hook + opening/middle paragraphs",
                "required": True,
            }
        ]
    if normalized == "payoff_missing":
        return [
            {
                "validator_id": "reading_power_payoff_validator",
                "description": "本章至少补出一个信息、关系、能力、资源或线索类微兑现。",
                "evidence_hint": "micro_payoffs",
                "required": True,
            }
        ]
    if normalized in {"information_pacing_stagnant", "information_pacing_slow"}:
        return [
            {
                "validator_id": "reading_power_information_pacing_validator",
                "description": "修复后本章至少出现一个清晰的新信息、线索推进或局势变化，信息释放不再停滞。",
                "evidence_hint": "information_pacing + micro_payoffs",
                "required": True,
            }
        ]
    if normalized in {"main_plot_stalled", "main_plot_surface"}:
        return [
            {
                "validator_id": "reading_power_main_plot_validator",
                "description": "修复后主线目标、阻力、结果或角色认知至少有一处可见推进。",
                "evidence_hint": "main_plot_depth + main_plot_advancement_notes",
                "required": True,
            }
        ]
    if normalized == "tension_depressed":
        return [
            {
                "validator_id": "reading_power_tension_validator",
                "description": "修复后章节张力与当前阶段预期更接近，章尾或关键段落有明确压力/期待。",
                "evidence_hint": "tension_match + chapter ending",
                "required": True,
            }
        ]
    if normalized == "character_drive_weak":
        return [
            {
                "validator_id": "reading_power_character_drive_validator",
                "description": "修复后核心角色至少做出一次有动机的选择、拒绝、试探或承担后果的行动。",
                "evidence_hint": "character_drive + character_drive_notes",
                "required": True,
            }
        ]
    if normalized == "revelation_over_budget":
        return [
            {
                "validator_id": "reading_power_revelation_budget_validator",
                "description": "修复后超预算重大揭示被后移、降级为伏笔，或改成读者可消化的阶段性线索。",
                "evidence_hint": "revelation_count + revelation_over_budget",
                "required": True,
            }
        ]
    return [
        {
            "validator_id": "reading_power_semantic_validator",
            "description": "修复后该追读力问题的 summary/evidence 所述缺陷不再成立。",
            "evidence_hint": "target paragraphs",
            "required": True,
        }
    ]


def _reading_power_issue(
    report: "ReadingPowerReport",
    *,
    issue_type: str,
    severity: str,
    summary: str,
    evidence: str,
    fix_suggestion: str,
    location: str,
) -> ReadingPowerIssue:
    chapter_number = int(getattr(report, "chapter", 0) or 0)
    return ReadingPowerIssue(
        issue_type=issue_type,
        severity=severity,
        summary=summary,
        evidence=evidence,
        fix_suggestion=fix_suggestion,
        location=location,
        issue_id=stable_issue_id(
            "reading_power",
            chapter_number=chapter_number,
            issue_type=issue_type,
            summary=summary,
            evidence=evidence,
            location=location,
            repair_surface="chapter_text",
        ),
        repair_surface="chapter_text",
        status="open",
        blocking=severity_rank(severity) >= 3,
        postconditions=_reading_power_postconditions(issue_type),
    )


def _build_reading_power_issues(
    report: "ReadingPowerReport | None",
    min_payoffs: int = 1,
    chapter_number: int = 0,
) -> list[ReadingPowerIssue]:
    """Convert a ReadingPowerReport deficiencies into a list of ReadingPowerIssue.

    Args:
        report: ReadingPowerReport instance or None/fallback report.
        min_payoffs: Minimum expected micro-payoffs count.

    Returns:
        List of ReadingPowerIssue objects representing detected deficiencies.
    """
    if report is None:
        return []

    # Check for fallback/is_fallback report - treat as no issues
    is_fallback = getattr(report, "is_fallback", False)
    if is_fallback:
        return []

    issues: list[ReadingPowerIssue] = []

    hook_type = str(getattr(report, "hook_type", "none") or "none").lower()
    hook_strength = str(getattr(report, "hook_strength", "weak") or "weak").lower()
    prev_hook_fulfilled = bool(getattr(report, "prev_hook_fulfilled", True))
    micro_payoffs = list(getattr(report, "micro_payoffs", []) or [])
    outline_hook_match = getattr(report, "outline_hook_match", None)
    information_pacing = str(getattr(report, "information_pacing", "balanced") or "").lower()
    main_plot_depth = str(getattr(report, "main_plot_depth", "moderate") or "").lower()
    tension_match = str(getattr(report, "tension_match", "matched") or "").lower()
    character_drive = str(getattr(report, "character_drive", "moderate") or "").lower()
    revelation_over_budget = bool(getattr(report, "revelation_over_budget", False))
    consecutive_main_plot_stall = int(getattr(report, "consecutive_main_plot_stall", 0) or 0)

    # hook_type == "none" → issue_type="hook_missing", severity="high"
    if hook_type == "none":
        issues.append(
            _reading_power_issue(
                report,
                issue_type="hook_missing",
                severity="high",
                summary="章尾缺少明确钩子",
                evidence="hook_type is 'none'",
                fix_suggestion="添加一个具体可感的危机、悬念、情绪冲击或选择压力作为章尾钩子",
                location="章尾",
            )
        )

    # hook_strength == "weak" → issue_type="hook_too_weak", severity="medium"
    if hook_strength == "weak":
        issues.append(
            _reading_power_issue(
                report,
                issue_type="hook_too_weak",
                severity="medium",
                summary="章尾钩子力度偏弱",
                evidence="hook_strength is 'weak'",
                fix_suggestion="让章尾出现角色必须应对的动作后果、证据发现或选择代价",
                location="章尾",
            )
        )

    # prev_hook_fulfilled == False → issue_type="prev_hook_unfulfilled", severity="high"
    report_chapter = int(chapter_number or getattr(report, "chapter", 0) or 0)
    if report_chapter > 1 and not prev_hook_fulfilled:
        issues.append(
            _reading_power_issue(
                report,
                issue_type="prev_hook_unfulfilled",
                severity="high",
                summary="上一章钩子没有被回应",
                evidence="prev_hook_fulfilled is False",
                fix_suggestion="在正文前中段补出承接、答案、反转或阶段性兑现",
                location="前中段",
            )
        )

    # len(micro_payoffs) < min_payoffs → issue_type="payoff_missing", severity="medium"
    if len(micro_payoffs) < min_payoffs:
        issues.append(
            _reading_power_issue(
                report,
                issue_type="payoff_missing",
                severity="medium",
                summary=f"章内微兑现不足（{len(micro_payoffs)}/{min_payoffs}）",
                evidence=f"micro_payoffs count {len(micro_payoffs)} < min_payoffs {min_payoffs}",
                fix_suggestion="补充信息、关系、能力或线索类兑现",
                location="章中",
            )
        )

    if information_pacing in {"stagnant", "slow"}:
        issues.append(
            _reading_power_issue(
                report,
                issue_type=(
                    "information_pacing_stagnant"
                    if information_pacing == "stagnant"
                    else "information_pacing_slow"
                ),
                severity="medium",
                summary=(
                    "信息释放停滞"
                    if information_pacing == "stagnant"
                    else "信息释放偏慢"
                ),
                evidence=f"information_pacing is '{information_pacing}'",
                fix_suggestion="补出一处新线索、关系确认、局势变化或阶段性答案",
                location="章中",
            )
        )

    if main_plot_depth in {"stalled", "surface"}:
        issues.append(
            _reading_power_issue(
                report,
                issue_type="main_plot_stalled"
                if main_plot_depth == "stalled"
                else "main_plot_surface",
                severity="high" if main_plot_depth == "stalled" else "medium",
                summary=(
                    "主线推进停滞"
                    if main_plot_depth == "stalled"
                    else "主线推进停留在表层"
                ),
                evidence=(
                    f"main_plot_depth is '{main_plot_depth}', "
                    f"consecutive_main_plot_stall={consecutive_main_plot_stall}"
                ),
                fix_suggestion="让本章目标、阻力、结果或角色认知产生可见推进",
                location="章中",
            )
        )

    if tension_match == "depressed":
        issues.append(
            _reading_power_issue(
                report,
                issue_type="tension_depressed",
                severity="medium",
                summary="章节张力低于阶段预期",
                evidence="tension_match is 'depressed'",
                fix_suggestion="强化关键段落压力、代价、期待或章尾承接压力",
                location="章尾",
            )
        )

    if character_drive == "weak":
        issues.append(
            _reading_power_issue(
                report,
                issue_type="character_drive_weak",
                severity="medium",
                summary="角色驱动力偏弱",
                evidence="character_drive is 'weak'",
                fix_suggestion="让核心角色做出带动机的选择、试探、拒绝或承担后果的行动",
                location="章中",
            )
        )

    if revelation_over_budget:
        issues.append(
            _reading_power_issue(
                report,
                issue_type="revelation_over_budget",
                severity="medium",
                summary="重大揭示超出单章预算",
                evidence="revelation_over_budget is True",
                fix_suggestion="把部分重大揭示后移或降级为阶段性线索",
                location="章中",
            )
        )

    # outline_hook_match == "different" → issue_type="outline_mismatch", severity="medium"
    if isinstance(outline_hook_match, dict):
        match_type = str(outline_hook_match.get("match_type", "") or "").lower()
        if match_type == "different":
            reason = str(outline_hook_match.get("reason", "") or "").strip()
            issues.append(
                _reading_power_issue(
                    report,
                    issue_type="outline_mismatch",
                    severity="medium",
                    summary="大纲钩子未达成",
                    evidence=f"outline_hook_match.match_type is 'different', reason: {reason}",
                    fix_suggestion=f"按大纲预期重设章尾钩子（{reason}）" if reason else "按大纲预期重设章尾钩子",
                    location="章尾",
                )
            )

    return issues
