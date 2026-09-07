"""Early-direction gate for bridge and plan artifacts.

The long-chapter pipeline already has strong downstream repair loops.  This
module catches upstream drift before draft generation turns it into expensive
prose repair: compression/bridge/plan should preserve the chapter's narrative
coordinates, or the chapter should be replanned immediately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from novel_forge.core.review.repair_harness import (
    RepairAuthority,
    TextRepairHarness,
    locate_text_fragments,
    materialize_text_artifact,
    repair_report_payload,
)
from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
)
from novel_forge.core.utils.repair_target_resolver import RepairResolverContext
from novel_forge.core.utils.string import carry_forward_status, carry_forward_text
from novel_forge.pipeline.long.services.time_validation import extract_explicit_duration_claims

CompassSeverity = Literal["critical", "high", "medium", "low"]
CompassStage = Literal["bridge", "plan", "bridge_plan"]

_BLOCKING_SEVERITIES: set[str] = {"critical", "high"}
_PUNCTUATION = set(" \t\r\n，。！？；：、,.!?;:()（）[]【】《》“”\"'—-")
_WEAK_CHARS = set("的是了和与及在把被对从到以而或也都很更再就还又")


@dataclass(frozen=True)
class UpstreamCompassFinding:
    """A single bridge/plan direction finding."""

    issue_type: str
    severity: CompassSeverity
    stage: CompassStage
    message: str
    evidence: str = ""
    repair_hint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    diagnostic: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpstreamCompassReport:
    """Aggregated early-direction report."""

    chapter: int
    passed: bool
    blocking: bool
    findings: list[UpstreamCompassFinding]

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary without requiring pydantic."""
        payload = asdict(self)
        payload["stage"] = "upstream_compass"
        payload["finding_count"] = len(self.findings)
        payload["blocking_findings"] = [
            asdict(finding)
            for finding in self.findings
            if finding.severity in _BLOCKING_SEVERITIES
        ]
        return payload


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _iter_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_iter_text(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(_iter_text(item))
        return parts
    if hasattr(value, "model_dump"):
        try:
            return _iter_text(value.model_dump(mode="json"))
        except TypeError:
            return _iter_text(value.model_dump())
    if hasattr(value, "__dict__"):
        return _iter_text(vars(value))
    return [str(value)]


def _text_blob(*values: Any) -> str:
    return "\n".join(part for value in values for part in _iter_text(value) if part)


def _duration_locator_evidence(
    *,
    artifact: str,
    payload: Any,
    evidence: dict[str, dict[int, set[str]]],
    chapter_number: int | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    phrases = sorted(
        {
            phrase
            for by_day in evidence.values()
            for phrase_set in by_day.values()
            for phrase in phrase_set
        }
    )
    located = locate_text_fragments(
        artifact=artifact,
        payload=payload,
        fragments=phrases,
        role="repair",
        chapter_number=chapter_number,
    )
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for subject, by_day in evidence.items():
        result[subject] = {}
        for day, phrase_set in by_day.items():
            best_by_anchor: dict[tuple[str, int], AuditLocator] = {}
            for phrase in sorted(phrase_set, key=len, reverse=True):
                for locator in located.get(phrase, []):
                    anchor = (locator.json_pointer, locator.char_start)
                    previous = best_by_anchor.get(anchor)
                    if previous is None or locator.char_end > previous.char_end:
                        best_by_anchor[anchor] = locator
            locators = [
                repair_report_payload(locator)
                for locator in sorted(
                    best_by_anchor.values(),
                    key=lambda item: (item.json_pointer, item.char_start, item.char_end),
                )
            ]
            result[subject][str(day)] = locators
    return result


def find_outline_plan_duration_conflicts(
    *,
    outline: Any,
    plan: Any,
) -> list[dict[str, Any]]:
    """Find mechanically provable total-duration conflicts in approved evidence."""

    outline_evidence = extract_explicit_duration_claims(outline)
    plan_evidence = extract_explicit_duration_claims(plan)
    outline_locator_evidence = _duration_locator_evidence(
        artifact="chapter_outline",
        payload=outline,
        evidence=outline_evidence,
    )
    plan_locator_evidence = _duration_locator_evidence(
        artifact="chapter_plan",
        payload=plan,
        evidence=plan_evidence,
    )
    outline_claims = {subject: set(by_day) for subject, by_day in outline_evidence.items()}
    plan_claims = {subject: set(by_day) for subject, by_day in plan_evidence.items()}
    conflicts: list[dict[str, Any]] = []
    for subject in sorted(set(outline_claims) | set(plan_claims)):
        authoritative = outline_claims.get(subject, set())
        downstream = plan_claims.get(subject, set())
        if len(authoritative) > 1:
            conflict_scope = (
                "outline_and_plan_internal" if len(downstream) > 1 else "outline_internal"
            )
            recovery_target = "manual"
        elif len(downstream) > 1:
            conflict_scope = "plan_internal"
            recovery_target = "plan"
        elif len(authoritative) == 1 and len(downstream) == 1 and authoritative != downstream:
            conflict_scope = "outline_plan"
            recovery_target = "plan"
        else:
            continue
        conflicts.append(
            {
                "subject": subject,
                "outline_days": sorted(authoritative),
                "plan_days": sorted(downstream),
                "conflict_scope": conflict_scope,
                "recovery_target": recovery_target,
                "outline_evidence": {
                    str(day): sorted(phrases)
                    for day, phrases in outline_evidence.get(subject, {}).items()
                },
                "plan_evidence": {
                    str(day): sorted(phrases)
                    for day, phrases in plan_evidence.get(subject, {}).items()
                },
                "outline_locators": outline_locator_evidence.get(subject, {}),
                "plan_locators": plan_locator_evidence.get(subject, {}),
            }
        )
    return conflicts


def _duration_conflict_diagnostic(
    *,
    conflict: dict[str, Any],
    outline: Any,
    plan: Any,
    chapter_number: int,
) -> dict[str, Any]:
    """Compile one duration conflict into the shared repair-harness contract."""

    source_internal = str(conflict.get("recovery_target")) == "manual"
    repair_artifact = "chapter_outline" if source_internal else "chapter_plan"
    locator_key = "outline_locators" if source_internal else "plan_locators"
    locator_groups = conflict.get(locator_key) or {}
    reference_groups = conflict.get("outline_locators") or {}
    authoritative_days = {int(day) for day in conflict.get("outline_days") or []}
    if not source_internal and len(authoritative_days) == 1:
        conflicting_plan_days = {
            int(day) for day in conflict.get("plan_days") or []
        } - authoritative_days
        locator_groups = {
            str(day): locators
            for day, locators in locator_groups.items()
            if int(day) in conflicting_plan_days
        }
        reference_groups = {
            str(day): locators
            for day, locators in reference_groups.items()
            if int(day) in authoritative_days
        }
    repair_locators = _audit_locators_from_groups(locator_groups, role="repair")
    reference_locators = (
        []
        if source_internal
        else _audit_locators_from_groups(reference_groups, role="reference")
    )
    evidence = [
        AuditEvidence(
            quote=locator.quote,
            source=locator.artifact,
            locator=locator,
            confidence=locator.confidence,
        )
        for locator in [*repair_locators, *reference_locators]
    ]
    subject = str(conflict.get("subject") or "期限")
    issue_id = f"upstream_duration_{chapter_number}_{subject}"
    authority: RepairAuthority = "proposal_required" if source_internal else "automatic_candidate"
    issue = AuditIssueV2(
        issue_id=issue_id,
        dimension="alignment",
        issue_type=(
            "outline_internal_duration_conflict"
            if source_internal
            else "plan_outline_duration_conflict"
        ),
        severity="critical",
        blocking=True,
        summary=f"{subject}期限证据互相冲突",
        description=(
            "已审批的大纲源证据内部存在不同总期限，只能生成版本化修订候选并重新审批。"
            if source_internal
            else "章节 Plan 偏离大纲中的总期限，允许在未发布 Plan 候选中修复并重跑同一审查。"
        ),
        evidence=evidence,
        repair_targets=repair_locators,
        reference_targets=reference_locators,
        repair_intent=AuditRepairIntent(
            operation="replace",
            target_policy="exact_text_span",
            rationale="只修改冲突期限所在的最小文本片段，不让正文迁就错误源证据。",
            preserve=["人物目标", "因果顺序", "非冲突时间标记", "已归档正文"],
            allowed_strategies=[
                "versioned_source_proposal" if source_internal else "bounded_plan_repair"
            ],
        ),
        postconditions=[
            AuditPostcondition(
                validator_id="upstream_compass.duration_consistency",
                description="同一审查器不再报告该期限冲突。",
                evidence_hint=subject,
            )
        ],
        metadata={
            "repair_authority": authority,
            "repair_artifact": repair_artifact,
            "recovery_target": conflict.get("recovery_target"),
            "conflict_scope": conflict.get("conflict_scope"),
        },
    )
    harness = TextRepairHarness(
        RepairResolverContext(
            artifacts={
                "chapter_outline": materialize_text_artifact(outline),
                "chapter_plan": materialize_text_artifact(plan),
            }
        )
    )
    result = harness.diagnose(issue, authority=authority).model_dump()
    result["issue"] = repair_report_payload(issue)
    return result


def _duration_conflict_finding(
    *,
    conflict: dict[str, Any],
    outline: Any,
    plan: Any,
    chapter_number: int,
) -> UpstreamCompassFinding:
    source_internal = str(conflict.get("recovery_target")) == "manual"
    diagnostic = _duration_conflict_diagnostic(
        conflict=conflict,
        outline=outline,
        plan=plan,
        chapter_number=chapter_number,
    )
    return UpstreamCompassFinding(
        issue_type=(
            "outline_internal_duration_conflict"
            if source_internal
            else "plan_outline_duration_conflict"
        ),
        severity="critical",
        stage="bridge_plan" if source_internal else "plan",
        message=(
            f"审查上游的「{conflict['subject']}」期限冲突："
            f"大纲={conflict['outline_days']}日，Plan={conflict['plan_days']}日。"
        ),
        evidence=str(conflict),
        repair_hint=(
            "上游大纲自身冲突；先修订并审批源证据，不得自动改正文或无限重做 Plan。"
            if source_internal
            else "回到 Plan 修正期限后重新生成/审查，不得让正文迁就冲突证据。"
        ),
        metadata=conflict,
        diagnostic=diagnostic,
    )


def audit_outline_source_consistency(
    *,
    outline: Any,
    chapter_number: int,
    blocking_enabled: bool = True,
) -> UpstreamCompassReport:
    """Audit approved outline totals before any model-backed chapter work."""

    findings = [
        _duration_conflict_finding(
            conflict=conflict,
            outline=outline,
            plan={},
            chapter_number=chapter_number,
        )
        for conflict in find_outline_plan_duration_conflicts(outline=outline, plan={})
        if str(conflict.get("recovery_target")) == "manual"
    ]
    blocking = blocking_enabled and any(
        finding.severity in _BLOCKING_SEVERITIES for finding in findings
    )
    return UpstreamCompassReport(
        chapter=chapter_number,
        passed=not blocking,
        blocking=blocking,
        findings=findings,
    )


def _audit_locators_from_groups(
    groups: Any,
    *,
    role: Literal["repair", "reference"],
) -> list[AuditLocator]:
    result: list[AuditLocator] = []
    if not isinstance(groups, dict):
        return result
    for day in sorted(groups, key=str):
        raw_locators = groups.get(day)
        if not isinstance(raw_locators, list):
            continue
        for raw in raw_locators:
            if not isinstance(raw, dict):
                continue
            payload = dict(raw)
            payload["role"] = role
            result.append(AuditLocator.model_validate(payload))
    return result


def _signal_chars(text: str) -> set[str]:
    return {
        ch
        for ch in _clean_text(text)
        if ch not in _PUNCTUATION and ch not in _WEAK_CHARS and not ch.isdigit()
    }


def _overlap_score(needle: str, haystack: str) -> float:
    needle = _clean_text(needle)
    haystack = _clean_text(haystack)
    if not needle:
        return 1.0
    if needle in haystack:
        return 1.0
    chars = _signal_chars(needle)
    if not chars:
        return 0.0
    hay_chars = _signal_chars(haystack)
    return len(chars & hay_chars) / max(1, len(chars))


def _has_semantic_trace(needle: str, haystack: str, *, threshold: float = 0.36) -> bool:
    return _overlap_score(needle, haystack) >= threshold


def _bridge_transition_text(bridge: Any) -> str:
    return _text_blob(
        getattr(bridge, "transition_mode", ""),
        getattr(bridge, "action_handoff", ""),
        getattr(bridge, "bridge_summary", ""),
        getattr(bridge, "opening_time", ""),
        getattr(bridge, "causal_link", None),
    )


def _first_scene(plan: Any) -> Any | None:
    scenes = getattr(plan, "scene_intents", []) or []
    if not isinstance(scenes, list) or not scenes:
        return None
    return scenes[0]


def _field_text(source: Any, name: str) -> str:
    if isinstance(source, dict):
        return _clean_text(source.get(name, ""))
    return _clean_text(getattr(source, name, ""))


def build_location_transition_semantic_check(
    *,
    packet: Any,
    bridge: Any,
    plan: Any,
    chapter_number: int,
) -> dict[str, Any] | None:
    """Assemble an LLM semantic-check package for bridge location continuity."""

    if chapter_number <= 1:
        return None
    previous_exit = getattr(packet, "previous_exit_state", None)
    if previous_exit is None:
        return None
    previous_location = _clean_text(getattr(previous_exit, "location", ""))
    opening_location = _clean_text(getattr(bridge, "opening_location", ""))
    if not previous_location or not opening_location or previous_location == opening_location:
        return None

    first_scene = _first_scene(plan)
    previous_pov = _clean_text(getattr(previous_exit, "pov", ""))
    opening_pov = _clean_text(getattr(bridge, "opening_pov", ""))
    transition_text = _bridge_transition_text(bridge)
    issue_description = (
        "上一章出口地点与本章开场地点不一致。请判断 Bridge/Plan 是否已经在语义上解释"
        "移动路径、时间跳转、视角切换，或说明二者其实是同一地点/建筑/场域的子地点。"
        "只要证据包中已有充分解释，就判定原问题已解决；不要因为地点字符串不完全相同而判定失败。"
    )
    issue_evidence = (
        f"previous_location={previous_location}; opening_location={opening_location}; "
        f"previous_pov={previous_pov}; opening_pov={opening_pov}"
    )
    repaired_text = "\n".join(
        part
        for part in (
            "【上一章出口】",
            f"- time_marker: {_clean_text(getattr(previous_exit, 'time_marker', ''))}",
            f"- location: {previous_location}",
            f"- pov: {previous_pov}",
            "【Bridge】",
            f"- opening_time: {_clean_text(getattr(bridge, 'opening_time', ''))}",
            f"- opening_location: {opening_location}",
            f"- opening_pov: {opening_pov}",
            f"- transition_mode: {_clean_text(getattr(bridge, 'transition_mode', ''))}",
            f"- action_handoff: {_clean_text(getattr(bridge, 'action_handoff', ''))}",
            f"- bridge_summary: {_clean_text(getattr(bridge, 'bridge_summary', ''))}",
            f"- causal_link: {transition_text}",
            "【Plan 开场】",
            f"- opening_contract: {_clean_text(getattr(plan, 'opening_contract', ''))}",
            f"- first_scene.location: {_field_text(first_scene, 'location') if first_scene is not None else ''}",
            f"- first_scene.time_marker: {_field_text(first_scene, 'time_marker') if first_scene is not None else ''}",
            f"- first_scene.summary: {_field_text(first_scene, 'summary') if first_scene is not None else ''}",
            f"- first_scene.entry_state: {_field_text(first_scene, 'entry_state') if first_scene is not None else ''}",
            f"- first_scene.required_outcome: {_field_text(first_scene, 'required_outcome') if first_scene is not None else ''}",
        )
        if part
    )
    return {
        "issue_description": issue_description,
        "issue_evidence": issue_evidence,
        "repaired_text": repaired_text,
        "repair_action": "Bridge/Plan 开场承接语义判定",
        "metadata": {
            "chapter_number": chapter_number,
            "previous_location": previous_location,
            "opening_location": opening_location,
            "previous_pov": previous_pov,
            "opening_pov": opening_pov,
        },
    }


def _location_transition_unresolved(
    judgment: dict[str, Any] | None,
) -> tuple[bool, float, str]:
    if not isinstance(judgment, dict):
        return False, 0.0, ""
    resolved = bool(judgment.get("issue_resolved"))
    try:
        confidence = float(judgment.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reasoning = _clean_text(judgment.get("reasoning", ""))
    return not resolved, confidence, reasoning


def _scene_count(plan: Any) -> int:
    scenes = getattr(plan, "scene_intents", []) or []
    return len(scenes) if isinstance(scenes, list) else 0


def _scene_word_total(plan: Any) -> int:
    total = 0
    for scene in list(getattr(plan, "scene_intents", []) or []):
        try:
            total += int(getattr(scene, "target_words", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _plan_has_state_work(plan: Any) -> bool:
    if list(getattr(plan, "required_state_transitions", []) or []):
        return True
    for scene in list(getattr(plan, "scene_intents", []) or []):
        if _clean_text(getattr(scene, "required_outcome", "")):
            return True
        if _clean_text(getattr(scene, "exit_target_state", "")):
            return True
        if list(getattr(scene, "owned_state_changes", []) or []):
            return True
    return False


def audit_upstream_compass(
    *,
    packet: Any,
    bridge: Any,
    plan: Any,
    chapter_number: int,
    target_word_count: int = 0,
    min_plan_scenes: int = 1,
    word_budget_tolerance: float = 0.25,
    blocking_enabled: bool = True,
    location_transition_judgment: dict[str, Any] | None = None,
) -> UpstreamCompassReport:
    """Audit bridge/plan artifacts before draft generation."""
    findings: list[UpstreamCompassFinding] = []
    bridge_blob = _text_blob(bridge)
    plan_blob = _text_blob(plan)
    combined_blob = f"{bridge_blob}\n{plan_blob}"
    previous_exit = getattr(packet, "previous_exit_state", None)
    outline = getattr(packet, "chapter_outline", None)

    if int(getattr(bridge, "to_chapter", 0) or 0) != chapter_number:
        findings.append(
            UpstreamCompassFinding(
                issue_type="bridge_target_chapter_mismatch",
                severity="critical",
                stage="bridge",
                message=f"Bridge 指向章节与当前章节不一致：to_chapter={getattr(bridge, 'to_chapter', None)}。",
                repair_hint="重新生成 Bridge，确保 to_chapter 与当前章节编号一致。",
            )
        )

    if chapter_number > 1 and not _clean_text(getattr(bridge, "action_handoff", "")):
        findings.append(
            UpstreamCompassFinding(
                issue_type="bridge_missing_action_handoff",
                severity="high",
                stage="bridge",
                message="Bridge 缺少上一章到本章开场的动作接力。",
                repair_hint="补齐上一章结尾动作如何自然抵达本章开场。",
            )
        )

    if chapter_number > 1 and not _clean_text(getattr(bridge, "bridge_summary", "")):
        findings.append(
            UpstreamCompassFinding(
                issue_type="bridge_missing_summary",
                severity="high",
                stage="bridge",
                message="Bridge 缺少可供 Plan/Draft 继承的桥接摘要。",
                repair_hint="用一句话明确时间、地点、POV、动作和情绪承接。",
            )
        )

    if chapter_number > 1 and previous_exit is not None:
        previous_location = _clean_text(getattr(previous_exit, "location", ""))
        opening_location = _clean_text(getattr(bridge, "opening_location", ""))
        transition_unresolved, transition_confidence, transition_reasoning = (
            _location_transition_unresolved(location_transition_judgment)
        )
        if (
            previous_location
            and opening_location
            and previous_location != opening_location
            and transition_unresolved
        ):
            severity: CompassSeverity = "high" if transition_confidence >= 0.55 else "medium"
            findings.append(
                UpstreamCompassFinding(
                    issue_type="bridge_unexplained_location_jump",
                    severity=severity,
                    stage="bridge",
                    message=f"地点从“{previous_location}”跳到“{opening_location}”，但 Bridge 未解释移动过程。",
                    evidence=transition_reasoning,
                    repair_hint="在 action_handoff 或 bridge_summary 中补出行动路径、时间跳转或视角切换理由。",
                    metadata={
                        "previous_location": previous_location,
                        "opening_location": opening_location,
                        "llm_confidence": transition_confidence,
                        "llm_reasoning": transition_reasoning,
                        "transition_text": _bridge_transition_text(bridge)[:240],
                    },
                )
            )

        for question in list(getattr(previous_exit, "open_questions", []) or [])[:6]:
            if not _has_semantic_trace(str(question), bridge_blob):
                findings.append(
                    UpstreamCompassFinding(
                        issue_type="bridge_dropped_open_question",
                        severity="high",
                        stage="bridge",
                        message=f"上一章未决问题未进入 Bridge：{question}",
                        repair_hint="把未决问题放入 pending_questions / causal_link / bridge_summary。",
                        evidence=str(question),
                    )
                )

    for item in list(getattr(packet, "must_carry_forward", []) or [])[:8]:
        if carry_forward_status(item) != "open":
            continue
        text = carry_forward_text(item)
        if text and not _has_semantic_trace(text, combined_blob, threshold=0.55):
            findings.append(
                UpstreamCompassFinding(
                    issue_type="carry_forward_dropped",
                    severity="critical",
                    stage="bridge_plan",
                    message=f"必须承接项未进入 Bridge/Plan：{text}",
                    repair_hint="重新规划时显式继承该承接项，并在 opening_contract 或首场 required_outcome 落地。",
                    evidence=text,
                )
            )

    scene_count = _scene_count(plan)
    if scene_count < max(1, min_plan_scenes):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_missing_scene_intents",
                severity="critical",
                stage="plan",
                message=f"Plan 场景意图数量不足：{scene_count}。",
                repair_hint="重新生成 Plan，至少给出一个可执行场景意图。",
            )
        )

    if not _clean_text(getattr(plan, "opening_contract", "")):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_missing_opening_contract",
                severity="high",
                stage="plan",
                message="Plan 缺少 opening_contract，Draft 无法稳定继承 Bridge。",
                repair_hint="补齐开场必须满足的时间、地点、POV、动作接力和情绪承接。",
            )
        )

    if not _clean_text(getattr(plan, "closing_contract", "")):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_missing_closing_contract",
                severity="high",
                stage="plan",
                message="Plan 缺少 closing_contract，章节出口状态不明确。",
                repair_hint="补齐本章末尾必须交付的状态变化、悬念或行动钩子。",
            )
        )

    goal = _clean_text(getattr(outline, "goal", ""))
    if goal and not _has_semantic_trace(goal, plan_blob, threshold=0.30):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_goal_drift",
                severity="high",
                stage="plan",
                message=f"Plan 未明显覆盖章节目标：{goal}",
                repair_hint="重新生成 Plan，并把章节目标拆成场景 purpose / required_outcome。",
                evidence=goal,
            )
        )

    for conflict in find_outline_plan_duration_conflicts(outline=outline, plan=plan):
        findings.append(
            _duration_conflict_finding(
                conflict=conflict,
                outline=outline,
                plan=plan,
                chapter_number=chapter_number,
            )
        )

    if not _plan_has_state_work(plan):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_missing_state_work",
                severity="high",
                stage="plan",
                message="Plan 没有明确状态迁移或场景出口结果。",
                repair_hint="为每个关键场景补齐 required_outcome / exit_target_state / owned_state_changes。",
            )
        )

    total_words = _scene_word_total(plan)
    if target_word_count > 0 and total_words > 0:
        delta_ratio = abs(total_words - target_word_count) / max(1, target_word_count)
        if delta_ratio > word_budget_tolerance:
            findings.append(
                UpstreamCompassFinding(
                    issue_type="plan_word_budget_drift",
                    severity="high",
                    stage="plan",
                    message=(
                        f"Plan 场景字数合计 {total_words} 与目标 {target_word_count} "
                        f"偏差 {delta_ratio:.0%}。"
                    ),
                    repair_hint="重新分配 scene.target_words，使总量接近章节目标。",
                    metadata={"target_word_count": target_word_count, "scene_word_total": total_words},
                )
            )

    opening_contract = _clean_text(getattr(plan, "opening_contract", ""))
    bridge_action = _clean_text(getattr(bridge, "action_handoff", ""))
    if bridge_action and opening_contract and not _has_semantic_trace(
        bridge_action,
        opening_contract,
        threshold=0.24,
    ):
        findings.append(
            UpstreamCompassFinding(
                issue_type="plan_opening_contract_ignores_bridge",
                severity="medium",
                stage="plan",
                message="Plan 的 opening_contract 与 Bridge 动作接力重合度偏低。",
                repair_hint="在 opening_contract 中显式承接 Bridge 的动作、地点和情绪。",
                evidence=bridge_action,
                metadata={"overlap_score": round(_overlap_score(bridge_action, opening_contract), 3)},
            )
        )

    blocking = blocking_enabled and any(
        finding.severity in _BLOCKING_SEVERITIES for finding in findings
    )
    return UpstreamCompassReport(
        chapter=chapter_number,
        passed=not blocking,
        blocking=blocking,
        findings=findings,
    )


def blocking_messages(report: UpstreamCompassReport) -> list[str]:
    """Return user-facing block messages for high-severity compass findings."""
    messages: list[str] = []
    for finding in report.findings:
        if finding.severity not in _BLOCKING_SEVERITIES:
            continue
        targets = finding.diagnostic.get("targets") if finding.diagnostic else []
        target_labels: list[str] = []
        if isinstance(targets, list):
            for target in targets[:4]:
                if not isinstance(target, dict) or not target.get("path"):
                    continue
                raw_window = target.get("window")
                window: dict[str, Any] = raw_window if isinstance(raw_window, dict) else {}
                span = ""
                if window.get("char_end"):
                    span = f"[{window.get('char_start', 0)}:{window['char_end']}]"
                path = str(target.get("path") or "")
                target_labels.append(f"{target.get('surface', '')}{path}{span}")
        suffix = f" 精确靶点：{', '.join(target_labels)}。" if target_labels else ""
        messages.append(f"{finding.message}{suffix}")
    return messages
