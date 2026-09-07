"""Source-driven guidance contract audits for long-chapter generation.

The local audit layer should not encode story-specific semantics such as
particular names, phrasing patterns, or Chinese sentence templates.  It only
checks structural contracts that are already represented in artifacts, plus
report self-consistency.  Semantic violations are expected to arrive as
structured findings from guard/continuity/alignment/causal reports.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.pipeline.steps.planning.transition_claims import (
    find_uncovered_required_state_transitions,
)

_BLOCKING_SEVERITIES = {"critical", "high", "blocking"}
_BLOCKING_TERM_PATTERN = (
    r"(?:critical|high|blocking|blocker|阻断|严重|高危|高风险|高优先级|重大)"
)
_NEGATED_BLOCKING_PATTERNS = (
    re.compile(
        rf"(?:无|没有|未发现|未见|不存在|并无|未出现|无需|未构成|不|并不|不是)\s*"
        rf"{_BLOCKING_TERM_PATTERN}"
        rf"(?:\s*(?:/|、|,|，|和|与|或)\s*{_BLOCKING_TERM_PATTERN})*"
        r"\s*(?:级|等级)?(?:回归)?(?:问题|风险|缺陷|阻断|错误|issue|issues)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:no|not|without)\s+{_BLOCKING_TERM_PATTERN}"
        rf"(?:\s*(?:/|,|，|or|and)\s*{_BLOCKING_TERM_PATTERN})*"
        r"(?:\s+(?:issue|issues|problem|problems|blocker|blockers|risk|risks|"
        r"regression|regressions))?",
        re.IGNORECASE,
    ),
)
_EN_BLOCKING_RE = re.compile(r"(?<![a-z])(?:critical|high|blocking|blocker)(?![a-z])")
_ZH_BLOCKING_MARKERS = ("阻断", "严重", "高危", "高风险", "高优先级", "重大")
_REPORT_SUMMARY_FIELDS = (
    "summary",
    "analysis",
    "overall_summary",
    "reasoning_brief",
    "diagnosis",
)
_REPORT_ISSUE_FIELDS = (
    "issues",
    "findings",
    "review_findings",
    "missing_main_points",
    "weak_subplot_points",
    "prompt_leaks",
    "factual_errors",
    "continuity_errors",
    "expression_errors",
    "forbidden_element_findings",
)


@dataclass(frozen=True)
class GuidanceAuditPolicy:
    """Configurable structural policy for guidance-contract audits."""

    required_scene_fields: tuple[str, ...] = (
        "purpose",
        "conflict",
        "required_characters",
        "character_motivations",
        "required_outcome",
        "exit_target_state",
        "location",
        "time_marker",
        "target_words",
    )
    word_budget_tolerance: float = 0.20
    bridge_scene_binding_fields: tuple[str, ...] = (
        "entry_state_refs",
        "purpose",
        "required_outcome",
    )
    blocking_severities: set[str] = field(default_factory=lambda: set(_BLOCKING_SEVERITIES))


def audit_plan_contract(
    *,
    plan: Any,
    packet: Any | None = None,
    bridge: Any | None = None,
    chapter_number: int = 0,
    target_word_count: int = 0,
    policy: GuidanceAuditPolicy | None = None,
) -> list[ReviewFinding]:
    """Validate whether a ChapterPlan is structurally ready for drafting.

    This does not infer semantic coverage from prose.  It asks whether the
    planner produced the fields that downstream stages require in order to
    execute and later audit the chapter.
    """

    del packet  # reserved for future source-driven policy injection
    active_policy = policy or GuidanceAuditPolicy()
    findings: list[ReviewFinding] = []
    scenes = list(extract_field(plan, "scene_intents", []) or [])
    if not scenes:
        findings.append(
            _finding(
                chapter_number=chapter_number,
                issue_type="plan_contract_incomplete",
                severity="critical",
                summary="章节计划没有 scene_intents，不能进入 draft。",
                repair_goal="重新生成包含完整 scene_intents 的章节计划。",
                metadata={"stage": "plan"},
            )
        )
        return findings

    total_target_words = 0
    for index, scene in enumerate(scenes, start=1):
        missing = _missing_fields(scene, active_policy.required_scene_fields)
        if missing:
            findings.append(
                _finding(
                    chapter_number=chapter_number,
                    issue_type="plan_scene_incomplete",
                    severity="high",
                    summary=f"scene_{index:02d} 缺少必填执行字段：{'、'.join(missing)}。",
                    repair_goal="补齐该场景的执行字段，使 draft 能按 scene contract 写作。",
                    metadata={"stage": "plan", "scene_index": index, "missing_fields": missing},
                )
            )
        total_target_words += max(0, _int(extract_field(scene, "target_words", 0)))

    if target_word_count > 0:
        tolerance = max(0.0, float(active_policy.word_budget_tolerance))
        low = int(target_word_count * (1.0 - tolerance))
        high = int(target_word_count * (1.0 + tolerance))
        if total_target_words < low or total_target_words > high:
            findings.append(
                _finding(
                    chapter_number=chapter_number,
                    issue_type="plan_word_budget_mismatch",
                    severity="high",
                    summary=(
                        f"场景 target_words 总和 {total_target_words} 与章节目标 "
                        f"{target_word_count} 不匹配。"
                    ),
                    repair_goal="重新分配每个 scene 的 target_words，使总和接近章节目标字数。",
                    metadata={
                        "stage": "plan",
                        "target_word_count": target_word_count,
                        "scene_word_total": total_target_words,
                        "tolerance": tolerance,
                    },
                )
            )

    first_scene = scenes[0]
    if _bridge_requires_opening_binding(bridge) and not _has_anyfield(
        first_scene,
        active_policy.bridge_scene_binding_fields,
    ):
        findings.append(
            _finding(
                chapter_number=chapter_number,
                issue_type="plan_scene_01_bridge_unbound",
                severity="critical",
                summary="scene_01 未提供可承接 bridge 的结构字段。",
                repair_goal="重写 scene_01，使其在 entry_state_refs/purpose/required_outcome 中明确承担开场承接职责。",
                metadata={
                    "stage": "plan",
                    "scene_index": 1,
                    "binding_fields": list(active_policy.bridge_scene_binding_fields),
                },
            )
        )

    # ── required_state_transitions coverage against scenes ─────────────────
    transitions = _as_list(extract_field(plan, "required_state_transitions", []))
    if transitions:
        uncovered = _find_uncovered_transitions(transitions, scenes)
        if uncovered:
            findings.append(
                _finding(
                    chapter_number=chapter_number,
                    issue_type="plan_transition_uncovered",
                    severity="critical",
                    summary=(
                        f"以下 required_state_transitions 未被任何 scene 认领"
                        f"（{len(uncovered)}/{len(transitions)}）：{'；'.join(uncovered)}"
                    ),
                    repair_goal=(
                        "将未认领的 transition 分配给至少一个 scene 的 "
                        "owned_state_changes / required_outcome / exit_target_state，"
                        "或拆分为额外 scene。"
                    ),
                    metadata={
                        "stage": "plan",
                        "total_transitions": len(transitions),
                        "uncovered_count": len(uncovered),
                        "uncovered": uncovered,
                    },
                )
            )

    return findings


def _find_uncovered_transitions(
    transitions: list[str], scenes: list[Any]
) -> list[str]:
    """Return transitions not substantively represented in scene claim fields."""
    return find_uncovered_required_state_transitions(transitions, scenes, max_chars=80)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def audit_report_consistency(
    report: Any,
    *,
    source_module: str,
    chapter_number: int,
) -> list[ReviewFinding]:
    """Catch reports whose summary claims blockers while issues are empty."""

    if report is None:
        return []
    issues = _report_issue_entries(report)
    if issues:
        return []
    payload = _as_mapping(report)
    summary_text = " ".join(
        str(payload.get(field) or "").strip()
        for field in _REPORT_SUMMARY_FIELDS
        if isinstance(payload.get(field), str)
    )
    if not _mentions_blocking(summary_text):
        return []
    return [
        _finding(
            chapter_number=chapter_number,
            source_module=source_module,
            issue_type="report_issue_summary_mismatch",
            severity="critical",
            summary=f"{source_module} 报告摘要声称存在 high/critical/blocking 问题，但 issues=[]。",
            repair_goal="重新运行该报告或人工确认，禁止把自相矛盾报告视为通过。",
            metadata={"stage": "report_consistency", "source_module": source_module},
        )
    ]


def compile_guidance_repair_tickets(findings: list[ReviewFinding]) -> list[RepairTicket]:
    """Compile deterministic contract findings into repair tickets."""

    tickets: list[RepairTicket] = []
    for finding in findings:
        severity = _text(finding.severity).lower() or "medium"
        tickets.append(
            RepairTicket(
                ticket_id=f"ticket_{finding.finding_id}",
                chapter_number=finding.chapter_number,
                finding_ids=[finding.finding_id],
                source_module=finding.source_module,
                dimension=finding.dimension,
                issue_type=finding.issue_type,
                severity=severity,
                target_summary=finding.summary,
                repair_goal=finding.repair_goal or finding.summary,
                repair_mode="fulltext" if severity in _BLOCKING_SEVERITIES else "window",
                acceptance_criteria=[
                    "修复后对应结构审计不再产生同类 finding",
                    "保持原有 JSON/TEXT 顶层输出格式与必填字段",
                    "不得用空数组、空字符串或占位文本绕过结构契约",
                ],
                must_preserve=list(finding.must_preserve or []),
                forbidden_changes=[
                    "不得删除上游已提供的结构化约束字段",
                    "不得伪造已通过的结构化审查结果",
                ],
                max_change_ratio=0.12 if severity in _BLOCKING_SEVERITIES else 0.08,
                max_attempts=2,
                blocking=bool(finding.blocks_finalize),
                metadata=dict(finding.metadata or {}),
            )
        )
    return tickets


def blocking_messages(findings: list[ReviewFinding]) -> list[str]:
    """Human-readable finalize blockers for critical/high contract findings."""

    return [
        f"[{finding.severity}] {finding.issue_type}: {finding.summary}"
        for finding in findings
        if finding.blocks_finalize
    ]


def _missing_fields(source: Any, fields: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for name in fields:
        value = extract_field(source, name, None)
        if name == "target_words":
            if _int(value) <= 0:
                missing.append(name)
        elif isinstance(value, (list, tuple, set)):
            if not list(value):
                missing.append(name)
        elif not _text(value):
            missing.append(name)
    return missing


def _bridge_requires_opening_binding(bridge: Any | None) -> bool:
    if bridge is None:
        return False
    causal = extract_field(bridge, "causal_link", None)
    return bool(
        _text(extract_field(bridge, "action_handoff", ""))
        or _text(extract_field(bridge, "opening_time", ""))
        or _text(extract_field(bridge, "opening_location", ""))
        or _text(extract_field(causal, "previous_event", ""))
        or _text(extract_field(causal, "causal_mechanism", ""))
    )


def _has_anyfield(source: Any, fields: tuple[str, ...]) -> bool:
    for name in fields:
        value = extract_field(source, name, None)
        if isinstance(value, (list, tuple, set)) and list(value):
            return True
        if _text(value):
            return True
    return False


def _report_issue_entries(report: Any) -> list[Any]:
    entries: list[Any] = []
    for issue_field in _REPORT_ISSUE_FIELDS:
        value = extract_field(report, issue_field, [])
        if isinstance(value, dict):
            entries.extend(value.values())
        elif isinstance(value, (list, tuple, set)):
            entries.extend(list(value))
        elif value:
            entries.append(value)
    return [entry for entry in entries if entry]


def _finding(
    *,
    chapter_number: int,
    issue_type: str,
    severity: str,
    summary: str,
    repair_goal: str,
    source_module: str = "guidance_contract_audit",
    evidence_quote: str = "",
    metadata: dict[str, Any] | None = None,
) -> ReviewFinding:
    severity_key = _text(severity).lower() or "medium"
    signature = _stable_suffix([chapter_number, issue_type, summary, evidence_quote])
    return ReviewFinding(
        finding_id=f"guidance_ch{chapter_number}_{signature}",
        chapter_number=chapter_number,
        source_module=source_module,
        dimension="guidance_contract",
        issue_type=issue_type,
        severity=severity_key,
        confidence=0.92 if severity_key in _BLOCKING_SEVERITIES else 0.78,
        summary=summary,
        evidence_quote=evidence_quote,
        anchor_type="artifact_contract",
        repair_goal=repair_goal,
        must_preserve=[
            "保持章节计划的主线顺序、角色状态和出口目标",
            "保持已确认的 bridge 因果链和动作接力",
        ],
        suggested_mode="fulltext" if severity_key in _BLOCKING_SEVERITIES else "window",
        blocks_finalize=severity_key in _BLOCKING_SEVERITIES,
        signature=signature,
        metadata=metadata or {},
    )


def _mentions_blocking(text: str) -> bool:
    lowered = text.lower()
    for pattern in _NEGATED_BLOCKING_PATTERNS:
        lowered = pattern.sub(" ", lowered)
    return bool(_EN_BLOCKING_RE.search(lowered)) or any(
        term in lowered for term in _ZH_BLOCKING_MARKERS
    )


def _stable_suffix(parts: list[Any]) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
        return data if isinstance(data, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
