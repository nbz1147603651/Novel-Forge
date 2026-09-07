# ruff: noqa: F403,F405,I001
"""Shared helper library for the long-form quality stage."""

from __future__ import annotations

from datetime import datetime, timezone

from novel_forge.pipeline.long.stages.quality_checks_common import *

def _artifact_dump(value: Any) -> Any:
    """Return a JSON-safe compact value for stage-artifact diagnostics."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _artifact_dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_artifact_dump(item) for item in value]
    return str(value)


def _artifact_report_summary(report: Any) -> dict[str, Any]:
    """Summarize a report without duplicating the full prompt/report payload."""

    if report is None:
        return {}
    payload = _artifact_dump(report)
    if not isinstance(payload, dict):
        return {"value": payload}
    summary: dict[str, Any] = {}
    for key in (
        "source_text_hash",
        "alignment_score",
        "continuity_score",
        "causal_score",
        "overall_score",
        "quality_score",
        "summary",
        "verdict",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    for issue_key in ("issues", "findings", "suggestions", "repair_suggestions"):
        values = payload.get(issue_key)
        if isinstance(values, list):
            summary[f"{issue_key}_count"] = len(values)
    return summary


def _remember_opening_guard_pending_issues(
    runner: Any,
    issues: list[Any],
    *,
    reason: str,
) -> None:
    """Carry unresolved opening-guard issues into the normal continuity loop."""
    if not issues:
        return
    pending = []
    for issue in issues:
        if hasattr(issue, "model_copy"):
            pending.append(
                issue.model_copy(
                    update={
                        "source": "local",
                        "blocking": True,
                        "diagnostic_note": reason,
                    }
                )
            )
        else:
            item: dict[str, Any] = (
                dict(issue) if isinstance(issue, dict) else {"summary": str(issue)}
            )
            item.update({"source": "local", "blocking": True, "diagnostic_note": reason})
            pending.append(item)
    setattr(runner, _OPENING_GUARD_PENDING_ATTR, pending)


def merge_opening_guard_pending_issues(
    runner: Any,
    continuity_report: Any,
    *,
    chapter_number: int,
    on_step: Callable[[str, Any], None],
) -> Any:
    """Merge unresolved opening-guard findings into the continuity report once."""
    pending = list(getattr(runner, _OPENING_GUARD_PENDING_ATTR, []) or [])
    if hasattr(runner, _OPENING_GUARD_PENDING_ATTR):
        try:
            delattr(runner, _OPENING_GUARD_PENDING_ATTR)
        except Exception:
            setattr(runner, _OPENING_GUARD_PENDING_ATTR, [])
    if not pending or continuity_report is None:
        return continuity_report

    existing = list(getattr(continuity_report, "issues", []) or [])

    def _signature(issue: Any) -> tuple[str, str]:
        return (
            str(getattr(issue, "issue_type", "") or "").strip().lower(),
            str(getattr(issue, "summary", "") or "").strip(),
        )

    existing_signatures = {_signature(issue) for issue in existing}
    new_issues = [issue for issue in pending if _signature(issue) not in existing_signatures]
    if not new_issues:
        return continuity_report

    merged_issues = [*existing, *new_issues]
    current_score = float(getattr(continuity_report, "continuity_score", 10.0) or 10.0)
    high_count = sum(
        1 for issue in new_issues if severity_at_least(getattr(issue, "severity", "medium"), "high")
    )
    adjusted_score = min(current_score, 6.0 if high_count else current_score)
    summary = str(getattr(continuity_report, "summary", "") or "").strip()
    guard_summary = f"开场门禁遗留 {len(new_issues)} 个高置信承接问题，已并入连贯性修复。"
    update = {
        "issues": merged_issues,
        "continuity_score": adjusted_score,
        "summary": f"{summary}；{guard_summary}" if summary else guard_summary,
    }
    on_step(
        "opening_guard_issues_merged",
        {
            "chapter": chapter_number,
            "merged_count": len(new_issues),
            "continuity_score_before": current_score,
            "continuity_score_after": adjusted_score,
            "issue_types": [getattr(issue, "issue_type", "") for issue in new_issues],
        },
    )
    if hasattr(continuity_report, "model_copy"):
        return continuity_report.model_copy(update=update)
    try:
        copied_report = copy.copy(continuity_report)
        for key, value in update.items():
            setattr(copied_report, key, value)
        return copied_report
    except Exception as exc:
        raise TypeError(
            "continuity_report must support model_copy or shallow copy for merge"
        ) from exc


def _critic_report_path(layout: Any, chapter_number: int) -> Any | None:
    path_factory = getattr(layout, "critic_report_path", None)
    if callable(path_factory):
        return path_factory(chapter_number)
    reports_dir = getattr(layout, "reports_dir", None)
    if reports_dir is not None:
        return reports_dir / f"chapter_{chapter_number:03d}_critic.json"
    return None


def _serialize_critic_report(
    report: CritiqueReport,
    current_text: str,
    *,
    context_hash: str = "",
    evidence_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if is_dataclass(report) and not isinstance(report, type):
        payload = asdict(report)
    elif isinstance(report, dict):
        payload = dict(report)
    else:
        model_dump = getattr(report, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            payload = dict(dumped) if isinstance(dumped, dict) else {}
        else:
            payload = dict(getattr(report, "__dict__", {}))

    payload["report_type"] = "critic_agent_full_report"
    current_hash = source_text_hash(current_text)
    if context_hash:
        from novel_forge.pipeline.long.stages.report_freshness import stamp_report_freshness

        stamp_report_freshness(
            payload,
            current_hash=current_hash,
            context_hash=context_hash,
            evidence_hashes=evidence_hashes,
        )
    else:
        payload["source_text_hash"] = current_hash
    return payload


def _persist_critic_report(
    runner: Any,
    layout: Any,
    chapter_number: int,
    report: CritiqueReport | None,
    current_text: str,
    *,
    context_hash: str = "",
    evidence_hashes: dict[str, str] | None = None,
) -> None:
    if report is None:
        return
    path = _critic_report_path(layout, chapter_number)
    if path is None:
        return
    try:
        runner._storage.save_json(
            path,
            _serialize_critic_report(
                report,
                current_text,
                context_hash=context_hash,
                evidence_hashes=evidence_hashes,
            ),
        )
        runner._on_step(
            "critic_report_persisted",
            {"chapter": chapter_number, "path": str(path)},
        )
    except Exception as exc:
        _logger.warning(
            "Failed to persist CriticAgent report | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


_ALIGNMENT_CACHE_MAX_SIMILARITY = 0.95
_ALIGNMENT_CACHE_MIN_SCORE = 8.0
_GUARD_CHECK_INITIAL_OUTPUT_CHARS = 900
_GUARD_ACTIONABLE_STATUSES = {"non_compliant", "partial", "weak"}
_GUARD_CHECKABLE_STATUSES = {"compliant", *_GUARD_ACTIONABLE_STATUSES}
_GUARD_WARNING_PREFIX_INCOMPLETE = "AI护栏约束检查未完成:"
_GUARD_PROHIBITION_MARKERS = frozenset(
    {
        "不得",
        "不能",
        "不要",
        "禁止",
        "避免",
        "不应",
        "不可",
        "不允许",
        "不再",
        "不做",
        "must not",
        "never",
        "forbid",
        "forbidden",
        "avoid",
    }
)
_GUARD_SOFT_APPEARANCE_MARKERS = ("正面出场", "正面登场", "正面出现")
_EXPLICIT_FORBIDDEN_APPEARANCE_RE = re.compile(
    r"(?:禁止|不得|不能|不要|不可|不允许)"
    r"(?:让|令)?"
    r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9·]{2,12}?)"
    r"(?:在本章|于本章)?"
    r"(?:出场|登场|出现)"
)


def _normalize_guard_status(status: Any) -> str:
    value = str(status or "unknown").strip().lower()
    aliases = {
        "ok": "compliant",
        "pass": "compliant",
        "fulfilled": "compliant",
        "met": "compliant",
        "partial": "partial",
        "weak": "weak",
        "soft_fail": "weak",
        "non-compliant": "non_compliant",
        "non_compliant": "non_compliant",
        "not_compliant": "non_compliant",
        "violated": "non_compliant",
        "missing": "non_compliant",
        "check_failed": "check_error",
        "parse_error": "check_error",
    }
    return aliases.get(value, value or "unknown")


def _is_guard_check_error(result: dict[str, Any]) -> bool:
    return (
        bool(result.get("check_error"))
        or _normalize_guard_status(result.get("status")) == "check_error"
    )


def _guard_result_is_actionable(result: dict[str, Any]) -> bool:
    if _is_guard_check_error(result):
        return False
    if result.get("repairable") is False:
        return False
    return _normalize_guard_status(result.get("status")) in _GUARD_ACTIONABLE_STATUSES


def guard_report_has_actionable_low_compliance(
    report: dict[str, Any] | None,
    *,
    threshold: float = 0.6,
) -> bool:
    """Return True only when checked guard results show repairable violations."""
    if not isinstance(report, dict):
        return False
    if (
        str(report.get("intent_guard_mode") or "").strip().lower() == "block"
        and bool(report.get("intent_conflicts"))
    ):
        return True
    try:
        rate_raw = report.get("overall_compliance_rate")
        if rate_raw is None:
            return False
        rate = float(rate_raw)
        if "checked_count" in report or "actionable_violation_count" in report:
            checked_count = int(report.get("checked_count", 0) or 0)
            actionable_count = int(report.get("actionable_violation_count", 0) or 0)
        else:
            results = [r for r in report.get("compliance_results", []) if isinstance(r, dict)]
            checked_count = sum(
                1
                for result in results
                if _normalize_guard_status(result.get("status")) in _GUARD_CHECKABLE_STATUSES
            )
            actionable_count = sum(1 for result in results if _guard_result_is_actionable(result))
    except (TypeError, ValueError):
        return False
    return checked_count > 0 and actionable_count > 0 and rate < threshold


def guard_report_incomplete_warning(report: dict[str, Any] | None) -> str | None:
    """Build a non-repair warning for guard checks that could not be verified."""
    if not isinstance(report, dict):
        return None
    try:
        check_failed_count = int(report.get("check_failed_count", 0) or 0)
        unverified_count = int(report.get("unverified_count", 0) or 0)
    except (TypeError, ValueError):
        return None
    if check_failed_count <= 0 and unverified_count <= 0:
        return None
    fragments: list[str] = []
    if check_failed_count > 0:
        fragments.append(f"{check_failed_count} 条检查失败")
    if unverified_count > 0:
        fragments.append(f"{unverified_count} 条无法确认")
    return f"{_GUARD_WARNING_PREFIX_INCOMPLETE} {'，'.join(fragments)}；不会自动触发文本修复。"


def _guard_issue_type_for_status(status: str) -> str:
    if status == "non_compliant":
        return "guard_constraint_missing"
    if status in {"partial", "weak"}:
        return "guard_constraint_partial"
    if status == "unknown":
        return "guard_constraint_unverified"
    if status == "check_error":
        return "guard_constraint_check_failed"
    return "guard_constraint_note"


def _guard_finding_severity(status: str, confidence: float) -> str:
    if status == "non_compliant":
        return "critical" if confidence >= 0.85 else "high"
    if status in {"partial", "weak"}:
        return "high" if confidence >= 0.8 else "medium"
    if status == "unknown":
        return "medium" if confidence >= 0.6 else "low"
    if status == "check_error":
        return "low"
    return "low"


def _append_guard_note(notes: str, addition: str) -> str:
    notes = str(notes or "").strip()
    addition = str(addition or "").strip()
    if not addition or addition in notes:
        return notes
    return f"{notes}；{addition}" if notes else addition


def _guard_constraint_is_prohibition(constraint: str) -> bool:
    value = str(constraint or "").strip().lower()
    return any(marker in value for marker in _GUARD_PROHIBITION_MARKERS)


def _extract_chapter_contract_forbidden_changes(packet: Any) -> list[str]:
    if packet is None:
        return []
    if isinstance(packet, dict):
        contract = packet.get("chapter_contract", {})
    else:
        contract = getattr(packet, "chapter_contract", {})
    model_dump = getattr(contract, "model_dump", None)
    if callable(model_dump):
        contract = model_dump(mode="json")
    if not isinstance(contract, dict):
        return []
    forbidden = contract.get("forbidden_changes", []) or []
    return [
        str(item).strip()
        for item in _coerce_guard_constraint_items(forbidden)
        if str(item or "").strip()
    ]


def _coerce_guard_constraint_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def guard_constraints_for_packet(packet: Any) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(packet, dict):
        raw_constraints = packet.get("guard_constraints", []) or []
    else:
        raw_constraints = getattr(packet, "guard_constraints", []) or []
    for item in [
        *_coerce_guard_constraint_items(raw_constraints),
        *_extract_chapter_contract_forbidden_changes(packet),
    ]:
        text = str(item or "").strip()
        if text and text not in seen:
            values.append(text)
            seen.add(text)
    return values


def guard_constraints_available(packet: Any) -> bool:
    return bool(guard_constraints_for_packet(packet))


def _guard_exact_evidence_for_subject(current_text: str, subject: str) -> str:
    for paragraph in re.split(r"\n\s*\n", current_text):
        evidence = paragraph.strip()
        if subject in evidence:
            if len(evidence) <= 700:
                return evidence
            break
    index = current_text.find(subject)
    if index < 0:
        return subject
    start = max(0, index - 120)
    end = min(len(current_text), index + len(subject) + 240)
    return current_text[start:end].strip()


def _evaluate_local_guard_constraint(
    *,
    constraint: str,
    current_text: str,
) -> dict[str, Any] | None:
    constraint_text = str(constraint or "").strip()
    if not constraint_text:
        return None
    if any(marker in constraint_text for marker in _GUARD_SOFT_APPEARANCE_MARKERS):
        return None
    match = _EXPLICIT_FORBIDDEN_APPEARANCE_RE.search(constraint_text)
    if not match:
        return None
    subject = str(match.group("subject") or "").strip()
    if not subject:
        return None
    if subject in current_text:
        return {
            "constraint": constraint_text,
            "status": "non_compliant",
            "confidence": 0.98,
            "evidence": _guard_exact_evidence_for_subject(current_text, subject),
            "notes": "本地硬检查：禁出角色在正文出现",
            "check_error": False,
            "repairable": True,
        }
    return {
        "constraint": constraint_text,
        "status": "compliant",
        "confidence": 0.95,
        "evidence": "",
        "notes": "本地硬检查：禁出角色未出现",
        "check_error": False,
        "repairable": False,
    }


def _collect_guard_known_subjects(*sources: Any) -> list[str]:
    """Collect likely character names from packet/bundle without project constants."""

    names: set[str] = set()

    def _add_name(value: Any) -> None:
        name = str(value or "").strip()
        if 1 < len(name) <= 12:
            names.add(name)

    def _from_characters(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    _add_name(key)
                if isinstance(item, dict):
                    _add_name(item.get("name") or item.get("角色") or item.get("姓名"))
                else:
                    _add_name(getattr(item, "name", ""))
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _add_name(item.get("name") or item.get("角色") or item.get("姓名"))
                else:
                    _add_name(getattr(item, "name", ""))

    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            _from_characters(source.get("characters"))
            _from_characters(source.get("character_bible"))
            canon_context = source.get("canon_context", {})
            if isinstance(canon_context, dict):
                _from_characters(canon_context.get("characters", {}))
            else:
                _from_characters(getattr(canon_context, "characters", None))
            continue
        _from_characters(getattr(source, "characters", None))
        _from_characters(getattr(source, "character_bible", None))
        canon_context = getattr(source, "canon_context", None)
        if isinstance(canon_context, dict):
            _from_characters(canon_context.get("characters", {}))
        else:
            _from_characters(getattr(canon_context, "characters", None))

    return sorted(names, key=len, reverse=True)


def _guard_constraint_subjects(constraint: str, known_subjects: list[str]) -> list[str]:
    constraint_text = str(constraint or "")
    return [name for name in known_subjects if name and name in constraint_text]


def _compact_guard_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _guard_evidence_fuzzy_supported(evidence: str, current_text: str) -> bool:
    """Return True when evidence is a near-exact quote with tiny wording drift."""
    needle = _compact_guard_text(evidence)
    haystack = _compact_guard_text(current_text)
    if len(needle) < 12 or not haystack:
        return False
    if needle in haystack:
        return True
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    match = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    coverage = match.size / max(1, len(needle))
    if coverage < 0.86:
        return False
    start = max(0, match.b - 4)
    end = min(len(haystack), match.b + max(len(needle), match.size) + 4)
    window = haystack[start:end]
    ratio = difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio()
    return ratio >= 0.88


def _normalize_guard_result_for_actionability(
    result: dict[str, Any],
    *,
    current_text: str = "",
) -> dict[str, Any]:
    """Normalize one guard result and mark unsafe auto-repair cases unverified."""

    normalized = dict(result)
    status = _normalize_guard_status(normalized.get("status"))
    constraint = str(normalized.get("constraint") or "").strip()
    evidence = str(normalized.get("evidence") or "").strip()
    notes = str(normalized.get("notes") or "").strip()
    subjects = [
        str(subject).strip()
        for subject in normalized.get("constraint_subjects", [])
        if str(subject or "").strip()
    ]

    evidence_exact = bool(evidence and current_text and evidence in current_text)
    evidence_fuzzy = (
        bool(evidence and current_text)
        and not evidence_exact
        and _guard_evidence_fuzzy_supported(evidence, current_text)
    )
    normalized["status"] = status
    normalized["evidence"] = evidence
    normalized["notes"] = notes
    normalized["constraint_subjects"] = subjects
    normalized["evidence_exact"] = evidence_exact
    normalized["evidence_match_mode"] = (
        "exact" if evidence_exact else "fuzzy" if evidence_fuzzy else "none"
    )

    if _is_guard_check_error(normalized):
        normalized["repairable"] = False
        return normalized

    if status == "compliant":
        normalized["repairable"] = False
        return normalized

    if evidence and not evidence_exact and not evidence_fuzzy:
        normalized["status"] = "unknown"
        normalized["repairable"] = False
        normalized["notes"] = _append_guard_note(
            notes,
            "证据不是正文精确子串或高相似片段，已转人工复核",
        )
        return normalized

    if evidence and subjects and not any(subject in evidence for subject in subjects):
        normalized["status"] = "unknown"
        normalized["repairable"] = False
        normalized["notes"] = _append_guard_note(
            notes,
            "证据未命中护栏约束的显式主体，已转人工复核",
        )
        return normalized

    if status == "non_compliant" and not evidence and _guard_constraint_is_prohibition(constraint):
        normalized["status"] = "unknown"
        normalized["repairable"] = False
        normalized["notes"] = _append_guard_note(
            notes,
            "禁令类护栏缺少正文违规片段，已转人工复核",
        )
        return normalized

    if status not in _GUARD_ACTIONABLE_STATUSES:
        normalized["repairable"] = False
    elif normalized.get("repairable") is not False:
        normalized["repairable"] = True
    return normalized


def build_guard_compliance_findings(
    report: dict[str, Any],
    *,
    chapter_number: int,
    current_text: str = "",
) -> list[ReviewFinding]:
    """Normalize guard-constraint compliance results into unified findings."""
    source_hash = source_text_hash(current_text) if current_text else ""
    findings: list[ReviewFinding] = []
    results = list(report.get("compliance_results") or [])
    for result in results:
        result = _normalize_guard_result_for_actionability(result, current_text=current_text)
        if _is_guard_check_error(result):
            continue
        status = _normalize_guard_status(result.get("status"))
        if status == "compliant":
            continue
        constraint = str(result.get("constraint") or "").strip()
        confidence = _coerce_confidence(result.get("confidence"))
        evidence = str(result.get("evidence") or "").strip()
        notes = str(result.get("notes") or "").strip()
        issue_type = _guard_issue_type_for_status(status)
        severity = _guard_finding_severity(status, confidence)
        summary_prefix = {
            "non_compliant": "未兑现",
            "partial": "仅部分兑现",
            "weak": "兑现较弱",
            "unknown": "无法确认是否兑现",
            "check_error": "检查失败",
        }.get(status, "需要复核")
        repairable = status in _GUARD_ACTIONABLE_STATUSES and result.get("repairable") is not False
        suggested_mode = (
            "replace"
            if evidence and result.get("evidence_exact")
            else ("insert" if status == "non_compliant" else "window")
        )
        summary = (
            f"AI护栏约束{summary_prefix}: {constraint}"
            if constraint
            else f"AI护栏约束{summary_prefix}"
        )
        issue_id = stable_issue_id(
            "guard",
            chapter_number=chapter_number,
            issue_type="guard_constraint",
            summary=constraint or summary,
            repair_surface="chapter_text",
        )
        finding = ReviewFinding(
            finding_id=f"guard_constraint_{issue_id}",
            chapter_number=chapter_number,
            source_module="guard_constraint_compliance",
            dimension="guard",
            issue_type=issue_type,
            severity=severity,
            confidence=confidence,
            summary=summary,
            evidence_quote=evidence,
            paragraph_start=0,
            paragraph_end=0,
            anchor_type="evidence_match" if evidence else "inferred_scope",
            repair_goal=(
                f"在不改变本章既有主线结果的前提下，明确回应并落实该约束：{constraint}"
                if constraint
                else "在不改变本章既有主线结果的前提下，补足 AI 护栏要求的关键约束"
            ),
            must_preserve=[
                "保持当前章节已通过的主线推进和关键因果关系",
                "不得引入新的高风险设定漂移或人物动机偏航",
            ],
            suggested_mode=suggested_mode,
            blocks_finalize=repairable and status == "non_compliant" and confidence >= 0.6,
            source_text_hash=source_hash,
            metadata={
                "constraint": constraint,
                "status": status,
                "notes": notes,
                "repairable": repairable,
                "evidence_exact": bool(result.get("evidence_exact")),
                "evidence_match_mode": str(result.get("evidence_match_mode") or "none"),
                "constraint_subjects": list(result.get("constraint_subjects") or []),
                "original_issue": {
                    "issue_id": issue_id,
                    "issue_type": issue_type,
                    "severity": severity,
                    "summary": summary,
                    "evidence": evidence,
                    "location": "全文",
                    "repair_surface": "chapter_text",
                },
            },
        )
        findings.append(finding)
    return findings


def compile_guard_repair_tickets(
    findings: list[ReviewFinding],
    *,
    max_tickets: int = 3,
) -> list[RepairTicket]:
    """Compile bounded repair tickets from guard-compliance findings."""

    def _severity_score(finding: ReviewFinding) -> int:
        return int(SEVERITY_RANK.get(str(finding.severity or "").lower(), 0))

    actionable_findings = [
        finding
        for finding in findings
        if _normalize_guard_status(finding.metadata.get("status")) in _GUARD_ACTIONABLE_STATUSES
        and finding.metadata.get("repairable") is not False
        and finding_auto_repair_eligible(finding)
        and finding.issue_type
        not in {"guard_constraint_unverified", "guard_constraint_check_failed"}
    ]
    ranked = sorted(
        actionable_findings,
        key=lambda finding: (
            -_severity_score(finding),
            -float(finding.confidence or 0.0),
            finding.finding_id,
        ),
    )
    tickets: list[RepairTicket] = []
    for finding in ranked[:max_tickets]:
        constraint = str(finding.metadata.get("constraint") or "").strip()
        acceptance_criteria = []
        if constraint:
            acceptance_criteria.append(f"修复后需明确回应约束：{constraint}")
        if finding.evidence_quote:
            acceptance_criteria.append("优先在现有证据位置就地补强，而不是新增无关段落")
        acceptance_criteria.append("不得引入新的高风险情节偏航、角色漂移或时间线回退")
        tickets.append(
            RepairTicket(
                ticket_id=f"ticket_{finding.finding_id}",
                chapter_number=finding.chapter_number,
                finding_ids=[finding.finding_id],
                source_module=finding.source_module,
                dimension=finding.dimension,
                issue_type=finding.issue_type,
                severity=finding.severity,
                target_summary=finding.summary,
                repair_goal=finding.repair_goal,
                repair_mode=finding.suggested_mode,
                acceptance_criteria=acceptance_criteria,
                must_preserve=list(finding.must_preserve or []),
                forbidden_changes=[
                    "不得改写已通过质量门的主要情节结果",
                    "不得新增与约束无关的大段补叙或世界观扩写",
                ],
                max_change_ratio=0.06 if finding.suggested_mode == "replace" else 0.08,
                max_attempts=2,
                target_paragraph_start=finding.paragraph_start,
                target_paragraph_end=finding.paragraph_end,
                blocking=bool(finding.blocks_finalize),
                metadata={
                    **dict(finding.metadata or {}),
                    "constraint": constraint,
                    "status": finding.metadata.get("status", ""),
                    "notes": finding.metadata.get("notes", ""),
                    "evidence_quote": finding.evidence_quote,
                    "anchor_type": finding.anchor_type,
                    "evidence_exact": bool(finding.metadata.get("evidence_exact")),
                    "constraint_subjects": list(finding.metadata.get("constraint_subjects") or []),
                    "repairable": True,
                    "original_issue": dict(finding.metadata.get("original_issue") or {}),
                },
            )
        )
    return tickets


def attach_guard_repair_metadata(
    report: dict[str, Any],
    *,
    chapter_number: int,
    current_text: str = "",
    max_tickets: int = 3,
) -> tuple[list[ReviewFinding], list[RepairTicket]]:
    """Build findings and repair tickets for a guard compliance report."""
    findings = build_guard_compliance_findings(
        report,
        chapter_number=chapter_number,
        current_text=current_text,
    )
    findings, readiness = prepare_findings_for_repair(
        findings,
        current_text=current_text,
        completed_chapters=[chapter_number],
    )
    tickets = compile_guard_repair_tickets(findings, max_tickets=max_tickets)
    report["review_findings"] = [finding.model_dump(mode="json") for finding in findings]
    report["repair_tickets"] = [ticket.model_dump(mode="json") for ticket in tickets]
    report["repair_readiness"] = readiness
    return findings, tickets


def _build_motif_context_for_step(
    memory_ctx: Any,
    chapter_number: int,
) -> dict[str, Any]:
    """Build motif_context dict from memory_ctx for ContinuityEvalInput.

    Pure helper - no async I/O, lazy loading. Returns format compatible
    with ContinuityEvalInput.motif_context.

    Args:
        memory_ctx: MemoryContext object with motif_tracker attribute
        chapter_number: Current chapter number

    Returns:
        Dict with keys: active_motifs, forbidden_repetition,
        suggested_callbacks, repeated_phrases (empty dict if no tracker)
    """
    motif_tracker = getattr(memory_ctx, "motif_tracker", None)
    if motif_tracker is None:
        return {}
    try:
        settings = getattr(memory_ctx, "settings", None) or getattr(memory_ctx, "_settings", None)
        motif_context = motif_tracker.get_motifs_for_prompt(
            current_chapter=chapter_number,
            max_motifs=10,
            include_recent_usage=True,
            related_lookback_chapters=getattr(
                settings,
                "memory_motif_related_lookback_chapters",
                2,
            ),
            chapter_text="",
        )
        return dict(motif_context) if isinstance(motif_context, dict) else {}
    except Exception:
        return {}


def _build_dynamic_continuity_inputs(
    runner: Any,
    bundle: Any,
    packet: Any,
    chapter_number: int,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Build dynamic continuity filter inputs for eval and fallback rechecks.

    Returns:
        Tuple of (motif_context, bible_anchor_terms, registry_word_sets).
        registry_word_sets contains keys: kinship_terms, rhetorical_hints,
        emotion_keywords, bible_derived, genre.
    """
    empty_registry: dict[str, Any] = {
        "kinship_terms": None,
        "rhetorical_hints": None,
        "emotion_keywords": None,
        "bible_derived": None,
        "genre": None,
    }
    if not getattr(getattr(runner, "_settings", None), "dynamic_continuity_filter", False):
        return {}, [], empty_registry

    memory_ctx = getattr(runner, "memory_context", None)
    motif_context = (
        _build_motif_context_for_step(memory_ctx, chapter_number) if memory_ctx is not None else {}
    )
    bible_anchor_terms = _extract_anchor_terms_from_bible(
        getattr(bundle, "story_bible", None),
        getattr(packet, "canon_context", None),
    )

    registry_word_sets: dict[str, Any] = dict(empty_registry)
    project_path = getattr(bundle.layout, "root", None)
    if project_path is not None:
        try:
            from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry

            _registry = ForbiddenElementRegistry(project_path)
            if _registry.has_project_seeds:
                bible_derived_provider = (
                    getattr(memory_ctx, "bible_derived_provider", None) if memory_ctx else None
                )
                bible_derived = None
                if bible_derived_provider is not None:
                    try:
                        bible_derived = bible_derived_provider.derive_from_packet(packet)
                    except Exception:
                        pass
                genre = ""
                story_bible = getattr(bundle, "story_bible", None)
                if story_bible is not None:
                    genre = getattr(story_bible, "genre", "") or ""
                merged = _registry.merge_sources(
                    bible_derived=bible_derived, genre=genre or "universal_minimal"
                )
                registry_word_sets = {
                    "kinship_terms": frozenset(merged.get("kinship_and_address_terms", [])),
                    "rhetorical_hints": frozenset(merged.get("rhetorical_imagery_hints", [])),
                    "emotion_keywords": frozenset(merged.get("abstract_emotion_keywords", [])),
                    "bible_derived": bible_derived,
                    "genre": genre or "universal_minimal",
                }
        except Exception:
            pass

    return motif_context, bible_anchor_terms, registry_word_sets


def _compute_text_similarity(text1: str, text2: str) -> float:
    """Compute similarity ratio between two texts."""
    if not text1 or not text2:
        return 0.0
    s1 = "".join(text1.split())
    s2 = "".join(text2.split())
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    min_len = min(len(s1), len(s2))
    if max_len == 0:
        return 0.0
    length_ratio = min_len / max_len
    if length_ratio < _ALIGNMENT_CACHE_MAX_SIMILARITY:
        return length_ratio
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def _should_skip_alignment_recheck(
    previous_text: str,
    current_text: str,
    previous_alignment_report: Any | None,
) -> tuple[bool, str]:
    """Determine if alignment recheck can be skipped based on change magnitude."""
    if previous_alignment_report is None:
        return False, "no previous report"

    previous_score = getattr(previous_alignment_report, "alignment_score", 0.0)
    if previous_score < _ALIGNMENT_CACHE_MIN_SCORE:
        return False, f"previous score {previous_score:.1f} below threshold"

    similarity = _compute_text_similarity(previous_text, current_text)
    if similarity >= _ALIGNMENT_CACHE_MAX_SIMILARITY:
        return (
            True,
            f"text unchanged (similarity={similarity:.2f} >= {_ALIGNMENT_CACHE_MAX_SIMILARITY})",
        )

    return False, "text changed significantly"


def _coerce_confidence(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_OPENING_GUARD_SCOPE_TOKENS = frozenset({"opening", "开头", "开场"})
_OPENING_GUARD_STRUCTURAL_TYPES = frozenset(
    {
        "bridge_contract_not_followed",
        "custody_break",
        "location_jump",
        "opening_gap",
        "pov_jump",
    }
)


def _opening_guard_sort_key(item: tuple[Any, dict[str, Any]]) -> tuple[int, int, float, int]:
    issue, raw = item
    issue_type = str(getattr(issue, "issue_type", "") or "").lower()
    severity = str(getattr(issue, "severity", "medium") or "medium").lower()
    confidence = _coerce_confidence(raw.get("confidence"), default=0.0)
    structural_boost = 1 if issue_type in _OPENING_GUARD_STRUCTURAL_TYPES else 0
    return (
        structural_boost,
        int(SEVERITY_RANK.get(severity, 1)),
        confidence,
        len(str(getattr(issue, "summary", "") or "")),
    )


def _extract_active_characters(chapter_text: str, packet: Any) -> list[str]:
    """Extract active character names from chapter text and canon context."""
    characters: set[str] = set()

    canon_context = getattr(packet, "canon_context", None)
    if canon_context is None:
        chars_dict = {}
    elif isinstance(canon_context, dict):
        chars_dict = canon_context.get("characters", {})
    elif hasattr(canon_context, "characters"):
        chars_dict = canon_context.characters
    else:
        chars_dict = {}

    characters.update(chars_dict.keys())

    dialogue_patterns = [
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*说",
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*道",
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*问",
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*答",
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*想",
        r'[""' ']([^""' '\n]{2,30})[""' "]\\s*喊道",
    ]
    for pattern in dialogue_patterns:
        matches = re.findall(pattern, chapter_text)
        for match in matches:
            if len(match) >= 2 and not any(c in match for c in "，。、！？"):
                characters.add(match.strip())

    return list(characters)


def _push_audit_result_to_ui(
    runner: Any,
    chapter_number: int,
    critique_report: CritiqueReport | None,
    alignment_report: Any | None,
) -> None:
    """Push audit results via runner event for DesktopJobManager to relay to UIStore."""
    try:
        project_id = getattr(runner, "_project_id", None)
        if not project_id:
            _logger.debug("No project_id available, skipping UI push")
            return

        audit_result: dict[str, Any] = {
            "chapter_number": chapter_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if critique_report is not None:
            audit_result["critique"] = {
                "overall_score": critique_report.overall_score,
                "has_critical_issues": critique_report.has_critical_issues,
                "requires_revision": critique_report.requires_revision,
                "warnings": critique_report.warnings,
                "strengths": critique_report.strengths,
                "metadata": critique_report.metadata or {},
                "issues": [
                    {
                        "severity": issue.severity,
                        "category": str(issue.issue_type),
                        "summary": issue.summary,
                        "evidence": issue.evidence,
                        "suggested_fix": issue.suggested_fix,
                        "affected_chapters": issue.affected_chapters,
                    }
                    for issue in critique_report.issues
                ],
            }
            audit_result["continuity_score"] = critique_report.overall_score
            audit_result["issue_count"] = len(critique_report.issues)
            audit_result["critical_issues"] = sum(
                1 for i in critique_report.issues if i.severity == "critical"
            )
            audit_result["high_issues"] = sum(
                1 for i in critique_report.issues if i.severity == "high"
            )

        if alignment_report is not None:
            alignment_dict = (
                alignment_report.model_dump(mode="json")
                if hasattr(alignment_report, "model_dump")
                else alignment_report
            )
            audit_result["alignment"] = alignment_dict
            if "alignment_score" in alignment_dict:
                audit_result["alignment_score"] = alignment_dict["alignment_score"]

        if runner.has_audit_coordinator() and runner.audit_coordinator is not None:
            try:
                memory_ctx = runner.memory_context
                if memory_ctx is not None:
                    memory_status = memory_ctx.get_status_summary()
                    audit_result["memory_context"] = {
                        "available": True,
                        "indexed_chapters": memory_status.get("indexed_chapters", 0),
                        "summary_available": memory_status.get("summary_available", False),
                        "motifs_count": len(memory_status.get("motifs", [])),
                        "relationships_count": memory_status.get("relationships_count", 0),
                    }
                    if runner.audit_coordinator is not None:
                        cached_result = runner.audit_coordinator.get_cached_result(
                            "audit_context", chapter_number
                        )
                        if cached_result is not None:
                            audit_result["memory_context"]["motif_warnings"] = getattr(
                                cached_result, "motif_warnings", []
                            )
                            audit_result["memory_context"]["relevant_history"] = getattr(
                                cached_result, "relevant_history", []
                            )
            except Exception as exc:
                _logger.debug("Failed to get memory context for UI: %s", exc)

        runner._on_step(
            "audit_result_update",
            {"chapter_number": chapter_number, "audit_result": audit_result},
        )
        _logger.debug(
            "Emitted audit_result_update event | project=%s | chapter=%d",
            project_id,
            chapter_number,
        )

    except Exception as exc:
        _logger.warning("Failed to push audit result to UI: %s", exc)


# ── Public functions ─────────────────────────────────────────────────────────




# ``quality_checks_runner`` imports this implementation facade with ``*``.
# A literal list makes the intentionally re-exported private helpers visible to
# mypy while keeping the runtime surface unchanged.
__all__ = [
    "AlignmentInput",
    "AlignmentStep",
    "Any",
    "Callable",
    "ChapterRepairInput",
    "ChapterRepairStep",
    "ContinuityEvalInput",
    "ContinuityEvalStep",
    "CriticAgent",
    "CritiqueReport",
    "EditorialCheckInput",
    "EditorialCheckStep",
    "KNOWN_PROMPT_MARKERS",
    "ModelGatewayError",
    "ReadingPowerRepairLoopResult",
    "RepairTicket",
    "ReviewFinding",
    "SEVERITY_RANK",
    "StoryKernel",
    "TaskType",
    "_ALIGNMENT_CACHE_MAX_SIMILARITY",
    "_ALIGNMENT_CACHE_MIN_SCORE",
    "_EXPLICIT_FORBIDDEN_APPEARANCE_RE",
    "_GUARD_ACTIONABLE_STATUSES",
    "_GUARD_CHECKABLE_STATUSES",
    "_GUARD_CHECK_INITIAL_OUTPUT_CHARS",
    "_GUARD_PROHIBITION_MARKERS",
    "_GUARD_SOFT_APPEARANCE_MARKERS",
    "_GUARD_WARNING_PREFIX_INCOMPLETE",
    "_OPENING_GUARD_PENDING_ATTR",
    "_OPENING_GUARD_SCOPE_TOKENS",
    "_OPENING_GUARD_STRUCTURAL_TYPES",
    "_append_guard_note",
    "_artifact_dump",
    "_artifact_report_summary",
    "_build_dynamic_continuity_inputs",
    "_build_motif_context_for_step",
    "_build_reading_power_excerpt",
    "_build_reading_power_input",
    "_coerce_confidence",
    "_coerce_guard_constraint_items",
    "_collect_guard_known_subjects",
    "_compact_guard_text",
    "_compute_text_similarity",
    "_critic_report_path",
    "_evaluate_local_guard_constraint",
    "_extract_active_characters",
    "_extract_anchor_terms_from_bible",
    "_extract_chapter_contract_forbidden_changes",
    "_extract_reading_power_expectations",
    "_guard_constraint_is_prohibition",
    "_guard_constraint_subjects",
    "_guard_evidence_fuzzy_supported",
    "_guard_exact_evidence_for_subject",
    "_guard_finding_severity",
    "_guard_issue_type_for_status",
    "_guard_result_is_actionable",
    "_infer_chapter_type",
    "_is_guard_check_error",
    "_logger",
    "_normalize_guard_result_for_actionability",
    "_normalize_guard_status",
    "_opening_guard_sort_key",
    "_persist_critic_report",
    "_push_audit_result_to_ui",
    "_reading_power_repair_issues_from_report",
    "_remember_opening_guard_pending_issues",
    "_serialize_critic_report",
    "_should_skip_alignment_recheck",
    "_source_text_hash",
    "_text_change_ratio",
    "annotations",
    "asdict",
    "asyncio",
    "attach_guard_repair_metadata",
    "build_guard_compliance_findings",
    "calculate_route_aware_max_tokens",
    "compile_guard_repair_tickets",
    "copy",
    "count_chapter_words",
    "datetime",
    "detect_drift",
    "diff_issues",
    "difflib",
    "evaluate_and_record_reading_power",
    "finding_auto_repair_eligible",
    "format_address_rules_for_prompt",
    "get_logger",
    "guard_constraints_available",
    "guard_constraints_for_packet",
    "guard_report_has_actionable_low_compliance",
    "guard_report_incomplete_warning",
    "is_dataclass",
    "llm_h",
    "load_story_kernel_composer",
    "merge_opening_guard_pending_issues",
    "prepare_findings_for_repair",
    "re",
    "render_world_context_rules",
    "route_output_limit",
    "severity_at_least",
    "source_text_hash",
    "stable_issue_id",
    "timezone",
]
