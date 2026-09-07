"""Progress callbacks, formatting utilities, and post-repair helpers."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from novel_forge.common.severity import normalize_severity, severity_at_least
from novel_forge.common.utils import normalize_gender_value
from novel_forge.core.exceptions import StorageError
from novel_forge.core.schemas.review import BeforeRepairCheckpoint
from novel_forge.obs.logger import get_logger
from novel_forge.workspace.execution_result import StepCallback

_log = get_logger("workspace.execution_helpers")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,}")
_PARA_RE = re.compile(r"(?:第\s*|[Pp]\s*|\[P\s*)(\d+)\s*(?:段|\])")


def _audit_severity_rank(value: Any, *, default: str = "info") -> int:
    severity = normalize_severity(value, default=default)
    if severity == "critical":
        return 2
    if severity_at_least(severity, "warning"):
        return 1
    return 0


def _report_severity_rank(value: Any, *, default: str = "info") -> int:
    severity = normalize_severity(value, default=default)
    if severity == "critical":
        return 3
    if severity in {"high", "major"}:
        return 2
    if severity in {"warning", "medium"}:
        return 1
    return 0


def _load_chapter_source_slice_if_available(
    storage: Any,
    layout: Any,
    *,
    project_id: str,
    chapter_number: int,
) -> Any | None:
    """Load the chapter source slice for auxiliary workspace tools when present."""

    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            load_chapter_source_slice,
        )

        return load_chapter_source_slice(
            storage,
            layout,
            project_id=project_id,
            chapter_number=chapter_number,
        )
    except Exception as exc:
        _log.debug(
            "chapter_source_slice_unavailable | project=%s | chapter=%s | error=%s",
            project_id,
            chapter_number,
            exc,
        )
        return None


def _safe_para_index(value: Any, default: int = 0) -> int:
    """Safely extract paragraph index from a value that may be int, str, or list."""
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_TIME_PATTERN = re.compile(
    r"[\u5143\u5e74\u5c81\u6708\u65e5\u5e74\u5b63\u6625\u590f\u79cb\u51ac\u814a\u6b63\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u5bcc\u4e11\u5bc5\u536f\u8fb0\u5df3\u5348\u672a\u7533\u9149\u620c\u4ea5\u5b50]{2,}"
)
_ENTITY_PATTERNS = {
    "time": _TIME_PATTERN,
    "location": re.compile(
        r"[\u4e00-\u9fff]{2,4}(?:\u57ce|\u9547|\u6751\u5b50|\u5e9c|\u5dde|\u53bf|\u5bab|\u6bbf|\u9601|\u53f0|\u697c|\u9662|\u5bfa|\u89c2|\u5e99|\u5c71|\u6cb3|\u6e56|\u6d77|\u8def|\u8857|\u5df7)"
    ),
    "person": re.compile(
        r"(?:[\u4e00-\u9fff]{2,3}(?:\u516c|\u5b50|\u90ce|\u541b|\u5a18|\u59d1|\u592b|\u59bb|\u738b|\u4faf|\u5c06|\u5e08|\u9053|\u50e7|\u5c3c))"
    ),
    "item": re.compile(
        r"[\u300c\u201c\u2018\u300e]([\u4e00-\u9fff]{2,6})[\u300d\u201d\u2019\u300f]"
    ),
}
_PERSON_ACTION_PATTERN = re.compile(
    r"(?:^|[\s，。！？；、：「“『])([\u4e00-\u9fff]{2,3})"
    r"(?=(?:说|道|问|答|想|看|走|站|坐|笑|叹|喊|低声|转身|点头|摇头|沉默|"
    r"握住|来到|离开|推开|拿起|放下|回头|抬头|皱眉))"
)
_LANE_CATEGORY_MAP: dict[str, str] = {
    "naming": "continuity",
    "timeline": "causal",
    "worldbuilding": "continuity",
    "character_state": "causal",
    "narrative_drift": "continuity",
}
_LOCALLY_SAFE_ISSUE_TYPES: frozenset[str] = frozenset(
    {
        "opening_gap",
        "location_jump",
        "pov_jump",
        "time_marker_invalid",
        "text_repetition",
        "sensory_anchor_repetition",
        "address_form_mismatch",
        "opening_causal_gap",
    }
)
_STATE_INHERIT_ISSUE_TYPES: frozenset[str] = frozenset(
    {
        "carry_forward_missing",
        "knowledge_contradiction",
        "causal_contradiction",
        "question_ignored",
    }
)


def _append_result_warning(result: Any, message: str) -> Any:
    """Append a warning message to result.warnings.

    Handles both mutable dataclasses (list warnings) and frozen dataclasses
    (tuple warnings). For frozen dataclasses, uses dataclasses.replace()
    to create a new instance.

    Returns the result (either the original or a new instance for frozen dataclasses).
    """
    warnings = getattr(result, "warnings", None)
    if warnings is None:
        return result
    if isinstance(warnings, list):
        warnings.append(message)
        return result
    # Frozen dataclass with tuple warnings — create new instance
    new_warnings = (*warnings, message)
    return dataclasses.replace(result, warnings=new_warnings)


def _emit_noncritical_warning(
    *,
    result: Any,
    on_step_progress: StepCallback,
    warning_step: str,
    warning_payload: dict[str, Any],
    log_event: str,
    error: Exception,
) -> Any:
    """Emit a non-critical warning and append it to result.warnings.

    Returns the result (possibly a new instance if result is a frozen dataclass).
    """
    _log.warning("%s | payload=%s | error=%s", log_event, warning_payload, error, exc_info=True)
    message = str(warning_payload.get("message") or str(error))
    result = _append_result_warning(result, message)
    if on_step_progress:
        on_step_progress(warning_step, warning_payload)
    return result


def _build_step_event_payload(
    *,
    project_id: str,
    chapter_number: int,
    source: str,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "source": source,
        "status": status,
    }
    payload.update(extra)
    return payload


def _emit_step_progress_event(
    on_step_progress: StepCallback,
    step: str,
    payload: dict[str, Any],
) -> None:
    if on_step_progress:
        on_step_progress(step, payload)


def _split_paragraphs_for_audit(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    chunks = [item.strip() for item in re.split(r"\n\s*\n+", raw) if item.strip()]
    if len(chunks) <= 1:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return lines if len(lines) > 1 else chunks
    return chunks


def _build_numbered_paragraph_text(text: str) -> tuple[str, int, list[str]]:
    paragraphs = _split_paragraphs_for_audit(text)
    if not paragraphs:
        return "", 0, []
    numbered = [f"[P{idx}] {para}" for idx, para in enumerate(paragraphs, start=1)]
    return "\n\n".join(numbered), len(paragraphs), paragraphs


def _chapter_repair_checkpoint_path(layout: Any, chapter_number: int) -> Path:
    states_dir = Path(layout.states_dir)
    return states_dir / f"chapter_{chapter_number:04d}_before_repair_checkpoint.json"


def save_repair_checkpoint(
    storage: Any,
    layout: Any,
    chapter_number: int,
    checkpoint: BeforeRepairCheckpoint,
) -> None:
    """Persist a per-chapter before-repair checkpoint for rollback."""
    path = _chapter_repair_checkpoint_path(layout, chapter_number)
    storage.save_json(path, checkpoint.model_dump(mode="json"))


def load_repair_checkpoint(
    storage: Any,
    layout: Any,
    chapter_number: int,
) -> BeforeRepairCheckpoint | None:
    """Load a per-chapter before-repair checkpoint if present and valid."""
    path = _chapter_repair_checkpoint_path(layout, chapter_number)
    try:
        if not storage.exists(path):
            return None
        payload = storage.load_json(path)
        if not isinstance(payload, dict):
            return None
        return BeforeRepairCheckpoint.model_validate(payload)
    except Exception as exc:
        _log.warning(
            "repair checkpoint load failed | chapter=%s | path=%s | error=%s",
            chapter_number,
            path,
            exc,
        )
        return None


def _normalize_repair_lane(value: Any) -> str:
    lane = str(value or "").strip().lower()
    if lane in {"continuity", "repair_continuity"}:
        return "continuity"
    if lane in {"causal", "repair_causal"}:
        return "causal"
    return ""


def _parse_para_index(location: str) -> int:
    text = str(location or "").strip()
    if not text:
        return 0
    match = _PARA_RE.search(text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _tokenize_match_text(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(str(text or ""))}


def _book_issue_to_lane(issue: dict[str, Any]) -> str:
    fix_mode = str(issue.get("fix_mode", "") or "").strip().lower()
    if fix_mode == "repair_causal":
        return "causal"
    if fix_mode in {"repair_continuity", "manual_patch"}:
        return "continuity"
    category = str(issue.get("category", "") or "").strip().lower()
    return _LANE_CATEGORY_MAP.get(category, "continuity")


def _book_issue_match_blob(issue: dict[str, Any]) -> str:
    parts = [
        str(issue.get("description", "") or ""),
        str(issue.get("suggestion", "") or ""),
        str(issue.get("evidence", "") or ""),
        str(issue.get("location", "") or ""),
        str(issue.get("issue_type", "") or ""),
    ]
    return " ".join(part for part in parts if part.strip())


def _report_issue_match_blob(issue: Any) -> str:
    parts = [
        str(getattr(issue, "summary", "") or ""),
        str(getattr(issue, "evidence", "") or ""),
        str(getattr(issue, "location", "") or ""),
        str(getattr(issue, "issue_type", "") or ""),
        " ".join(
            str(item) for item in (getattr(issue, "fix_actions", []) or []) if str(item).strip()
        ),
    ]
    return " ".join(part for part in parts if part.strip())


def _score_issue_match(book_issue: dict[str, Any], report_issue: Any) -> float:
    score = 0.0
    book_para = _safe_para_index(book_issue.get("paragraph_index", 0))
    if book_para <= 0:
        book_para = _parse_para_index(str(book_issue.get("location", "") or ""))
    report_para = _parse_para_index(str(getattr(report_issue, "location", "") or ""))
    if book_para > 0 and report_para > 0:
        delta = abs(book_para - report_para)
        if delta == 0:
            score += 3.0
        elif delta == 1:
            score += 2.0
        elif delta <= 3:
            score += 1.0
    book_blob = _book_issue_match_blob(book_issue)
    report_blob = _report_issue_match_blob(report_issue)
    if book_blob and report_blob:
        if str(book_issue.get("evidence", "") or "").strip():
            evidence = str(book_issue.get("evidence", "")).strip()
            if evidence and (evidence in report_blob or report_blob[:120] in evidence):
                score += 2.5
        a = _tokenize_match_text(book_blob)
        b = _tokenize_match_text(report_blob)
        if a and b:
            overlap = len(a & b) / max(1, min(len(a), len(b)))
            if overlap >= 0.5:
                score += 3.0
            elif overlap >= 0.3:
                score += 2.0
            elif overlap >= 0.15:
                score += 1.0
    return score


def _select_issue_indices_for_lane(
    *,
    book_issues: list[dict[str, Any]],
    report_issues: list[Any],
    lane: str,
) -> list[int]:
    if not report_issues:
        return []
    selected: set[int] = set()
    lane_issues = [issue for issue in book_issues if _book_issue_to_lane(issue) == lane]
    for issue in lane_issues:
        best_idx = -1
        best_score = 0.0
        for idx, rep_issue in enumerate(report_issues):
            if idx in selected:
                continue
            score = _score_issue_match(issue, rep_issue)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx >= 0 and best_score >= 2.0:
            selected.add(best_idx)
    return sorted(selected)


def _select_issue_indices_by_paragraph(
    *,
    book_issues: list[dict[str, Any]],
    report_issues: list[Any],
    lane: str,
    threshold: int = 2,
) -> list[int]:
    """Match book issues to report issues by paragraph position proximity.

    Uses paragraph index from book issues and parses paragraph index from
    report issue locations. Matches when the absolute difference is <= threshold.

    Two-pass strategy: tight match (threshold) first, then wider fallback (threshold*2)
    for unmatched book issues to increase repair coverage.

    Args:
        book_issues: Issues from book consistency audit (dict with paragraph_index).
        report_issues: Issues from chapter repair report (object with location attr).
        lane: Repair lane (continuity or causal).
        threshold: Maximum paragraph position difference for a match (default 2).

    Returns:
        List of matched report issue indices.
    """
    if not report_issues:
        return []

    selected: set[int] = set()
    lane_issues = [issue for issue in book_issues if _book_issue_to_lane(issue) == lane]

    # Build pre-computed report paragraph indices for efficiency
    report_paras: list[int] = []
    for rep_issue in report_issues:
        rp = _parse_para_index(str(getattr(rep_issue, "location", "") or ""))
        report_paras.append(rp)

    # Pass 1: tight match with original threshold
    for issue in lane_issues:
        book_para = _safe_para_index(issue.get("paragraph_index", 0))
        if book_para <= 0:
            book_para = _parse_para_index(str(issue.get("location", "") or ""))
        if book_para <= 0:
            continue

        for idx, rep_para in enumerate(report_paras):
            if idx in selected:
                continue
            if rep_para <= 0:
                continue
            if abs(book_para - rep_para) <= threshold:
                selected.add(idx)
                break

    # Pass 2: wider fallback for unmatched book issues (2x threshold, capped at 6)
    wider_threshold = min(threshold * 2, 6)
    if wider_threshold > threshold:
        for issue in lane_issues:
            book_para = _safe_para_index(issue.get("paragraph_index", 0))
            if book_para <= 0:
                book_para = _parse_para_index(str(issue.get("location", "") or ""))
            if book_para <= 0:
                continue

            for idx, rep_para in enumerate(report_paras):
                if idx in selected:
                    continue
                if rep_para <= 0:
                    continue
                if abs(book_para - rep_para) <= wider_threshold:
                    selected.add(idx)
                    break

    return sorted(selected)


def _extract_linked_issue_refs(
    *,
    issue: dict[str, Any],
    chapter_number: int,
    lane: str,
    max_issue_count: int,
) -> list[int]:
    refs = issue.get("linked_issue_refs", [])
    if not isinstance(refs, list) or max_issue_count <= 0:
        return []
    selected: list[int] = []
    lane_norm = _normalize_repair_lane(lane)
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        ref_lane = _normalize_repair_lane(ref.get("lane"))
        if ref_lane != lane_norm:
            continue
        try:
            ref_chapter = int(ref.get("chapter_number", 0) or 0)
            ref_index = int(ref.get("index", -1) or -1)
        except (TypeError, ValueError):
            continue
        if ref_chapter != chapter_number:
            continue
        if ref_index < 0 or ref_index >= max_issue_count:
            continue
        selected.append(ref_index)
    return list(dict.fromkeys(selected))


def _collect_linked_issue_indices_for_lane(
    *,
    chapter_issues: list[dict[str, Any]],
    chapter_number: int,
    lane: str,
    max_issue_count: int,
) -> tuple[list[int], int]:
    selected: set[int] = set()
    linked_issue_count = 0
    for issue in chapter_issues:
        refs = _extract_linked_issue_refs(
            issue=issue,
            chapter_number=chapter_number,
            lane=lane,
            max_issue_count=max_issue_count,
        )
        if not refs:
            continue
        linked_issue_count += 1
        for idx in refs:
            selected.add(idx)
    return sorted(selected), linked_issue_count


def _group_book_issues_by_chapter(
    issues: list[dict[str, Any]],
    *,
    min_severity: str,
    max_chapters: int,
) -> list[tuple[int, list[dict[str, Any]]]]:
    threshold = _audit_severity_rank(min_severity, default="warning")
    grouped: dict[int, list[dict[str, Any]]] = {}
    chapter_priority: dict[int, tuple[int, float]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("auto_repair_eligible") is False:
            continue
        severity = normalize_severity(issue.get("severity"), default="info")
        if _audit_severity_rank(severity) < threshold:
            continue
        chapters = issue.get("chapters_involved", [])
        chapter_ids = [
            int(ch) for ch in chapters if isinstance(ch, (int, str)) and str(ch).strip().isdigit()
        ]
        primary = int(issue.get("primary_chapter", 0) or 0)
        chapter = primary if primary > 0 else (chapter_ids[0] if chapter_ids else 0)
        if chapter <= 0:
            _log.warning(
                "book_consistency: discarding issue %r — no valid chapter "
                "(primary_chapter=%r, chapters_involved=%r)",
                str(issue.get("issue_id", ""))[:40],
                primary,
                chapters,
            )
            continue
        grouped.setdefault(chapter, []).append(issue)
        sev_rank = _audit_severity_rank(severity)
        conf = float(issue.get("confidence", 0.0) or 0.0)
        previous = chapter_priority.get(chapter, (-1, -1.0))
        chapter_priority[chapter] = max(previous, (sev_rank, conf))
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            -chapter_priority.get(item[0], (0, 0.0))[0],
            -chapter_priority.get(item[0], (0, 0.0))[1],
            item[0],
        ),
    )
    if max_chapters > 0:
        ranked = ranked[:max_chapters]
    return ranked


def _extract_key_entities(
    text: str,
    *,
    known_entities: dict[str, list[str] | set[str] | tuple[str, ...]] | None = None,
    include_unquoted_person_candidates: bool = False,
) -> dict[str, list[str]]:
    entity_sets: dict[str, set[str]] = {
        "time": set(),
        "location": set(),
        "person": set(),
        "item": set(),
    }
    for entity_type, pattern in _ENTITY_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            entity_sets[entity_type].update(str(match) for match in matches if str(match).strip())
    if include_unquoted_person_candidates:
        entity_sets["person"].update(
            match.strip() for match in _PERSON_ACTION_PATTERN.findall(text) if match.strip()
        )
    if known_entities:
        for entity_type, names in known_entities.items():
            if entity_type not in entity_sets or not isinstance(names, (list, set, tuple)):
                continue
            for raw_name in names:
                name = str(raw_name or "").strip()
                if len(name) >= 2 and name in text:
                    entity_sets[entity_type].add(name)
    return {entity_type: sorted(values) for entity_type, values in entity_sets.items()}


def _post_repair_evidence_check(
    new_text: str,
    chapter_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    if not chapter_issues or not new_text:
        return {
            "issues_checked": len(chapter_issues),
            "issues_closed": 0,
            "issues_remaining": 0,
            "remaining_details": [],
        }
    closed = 0
    remaining_details: list[dict[str, str]] = []
    for issue in chapter_issues:
        evidence = str(issue.get("evidence", "") or "").strip()
        if not evidence or len(evidence) < 2:
            closed += 1
            continue
        norm_evidence = re.sub(r"\s+", "", evidence)
        norm_text = re.sub(r"\s+", "", new_text)
        if norm_evidence in norm_text:
            remaining_details.append(
                {
                    "issue_id": str(issue.get("issue_id", "") or ""),
                    "severity": str(issue.get("severity", "") or ""),
                    "evidence_snippet": evidence[:60],
                }
            )
        else:
            original_entities = _extract_key_entities(evidence)
            new_entities = _extract_key_entities(new_text)
            entity_removed = False
            for entity_type in ("time", "location", "person", "item"):
                original = set(original_entities.get(entity_type, []))
                if original:
                    current = set(new_entities.get(entity_type, []))
                    if not original.intersection(current):
                        entity_removed = True
                        break
            if entity_removed:
                closed += 1
            else:
                remaining_details.append(
                    {
                        "issue_id": str(issue.get("issue_id", "") or ""),
                        "severity": str(issue.get("severity", "") or ""),
                        "evidence_snippet": evidence[:60],
                    }
                )
    return {
        "issues_checked": len(chapter_issues),
        "issues_closed": closed,
        "issues_remaining": len(remaining_details),
        "remaining_details": remaining_details,
    }


def _scan_cross_chapter_entity_impact(
    *,
    storage: Any,
    layout: Any,
    repaired_chapters: list[dict[str, Any]],
    all_chapter_numbers: list[int],
) -> list[dict[str, Any]]:
    if not repaired_chapters:
        return []
    repaired_chapter_nums = set()
    entity_names: set[str] = set()
    for detail in repaired_chapters:
        if not isinstance(detail, dict):
            continue
        ch_num = int(detail.get("chapter_number", 0) or 0)
        if ch_num <= 0 or detail.get("status") != "applied":
            continue
        repaired_chapter_nums.add(ch_num)
        _ENTITY_RE = re.compile(
            "[\u300c\u201c\u2018\u300e]([\u4e00-\u9fff]{2,6})[\u300d\u201d\u2019\u300f]"
        )
        for issue in detail.get("_source_issues", []):
            if not isinstance(issue, dict):
                continue
            evidence = str(issue.get("evidence", "") or "")
            for match in _ENTITY_RE.finditer(evidence):
                entity_names.add(match.group(1))
            desc = str(issue.get("description", "") or "")
            for match in _ENTITY_RE.finditer(desc):
                entity_names.add(match.group(1))
    if not entity_names or not repaired_chapter_nums:
        return []
    warnings: list[dict[str, Any]] = []
    other_chapters = [ch for ch in all_chapter_numbers if ch not in repaired_chapter_nums]
    for ch_num in other_chapters:
        chapter_path = layout.chapter_path(ch_num)
        if not chapter_path.exists():
            continue
        try:
            text = chapter_path.read_text(encoding="utf-8")
        except OSError:
            continue
        found_entities = [name for name in entity_names if name in text]
        if found_entities:
            warnings.append(
                {
                    "chapter_number": ch_num,
                    "shared_entities": found_entities,
                    "note": f"第 {ch_num} 章包含已修复章节涉及的实体（{'、'.join(found_entities[:5])}），建议关注是否一致。",
                }
            )
    return warnings


def _touch_downstream_context(
    layout: Any,
    repaired_chapter: int,
    *,
    touch_from: int | None = None,
) -> None:
    import os

    if touch_from is None:
        return
    chapters_dir = layout.chapters_dir
    downstream: list[int] = []
    if chapters_dir.exists():
        for path in chapters_dir.glob("chapter_*.md"):
            from novel_forge.persistence.project_staleness import (
                _parse_chapter_number,
            )

            number = _parse_chapter_number(path)
            if number is not None and number >= touch_from:
                downstream.append(number)
    for ch in sorted(downstream):
        for path in (
            layout.chapter_state_packet_path(ch),
            layout.chapter_bridge_path(ch),
            layout.chapter_plan_path(ch),
            layout.chapter_path(ch),
        ):
            if path.exists():
                try:
                    os.utime(path, None)
                except OSError:
                    pass


def _downstream_touch_from(
    repaired_chapter: int,
    repaired_issue_types: list[str] | None,
    *,
    always_safe: bool = False,
) -> int | None:
    if always_safe or not repaired_issue_types:
        return repaired_chapter + 1
    has_state_inherit = any(t in _STATE_INHERIT_ISSUE_TYPES for t in repaired_issue_types)
    if has_state_inherit:
        return None
    all_locally_safe = all(t in _LOCALLY_SAFE_ISSUE_TYPES for t in repaired_issue_types)
    if all_locally_safe:
        return repaired_chapter + 1
    return repaired_chapter + 2


async def _refresh_exit_state_after_repair(
    runtime: Any,
    layout: Any,
    chapter_number: int,
    revised_text: str,
) -> None:
    try:
        from novel_forge.obs.tracer import PipelineTrace
        from novel_forge.pipeline.steps.extract_step import (
            ExtractCanonDeltaStep,
            ExtractInput,
        )

        known_characters: list[str] = []
        authoritative_character_genders: dict[str, str] = {}
        char_bible_path = (
            layout.character_bible_path if hasattr(layout, "character_bible_path") else None
        )
        if char_bible_path is not None and char_bible_path.exists():
            try:
                raw_bible = runtime.storage.load_json(char_bible_path) or {}
                chars = raw_bible.get("characters") or raw_bible.get("主要人物") or {}
                if isinstance(chars, dict):
                    known_characters = [str(k).strip() for k in chars if str(k).strip()]
                    authoritative_character_genders = {
                        str(name).strip(): normalize_gender_value(info.get("gender", ""))
                        for name, info in chars.items()
                        if str(name).strip()
                        and isinstance(info, dict)
                        and normalize_gender_value(info.get("gender", ""))
                    }
                elif isinstance(chars, list):
                    known_characters = []
                    for item in chars:
                        if isinstance(item, dict):
                            name = str(item.get("name", "")).strip()
                            if name:
                                known_characters.append(name)
                                gender = normalize_gender_value(item.get("gender", ""))
                                if gender:
                                    authoritative_character_genders[name] = gender
                        else:
                            name = str(item).strip()
                            if name:
                                known_characters.append(name)
            except (OSError, StorageError):
                _log.debug("character bible load failed (degraded)", exc_info=True)
        step = ExtractCanonDeltaStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=PipelineTrace(),
        )
        layout_root = getattr(layout, "root", None)
        inferred_project_id = str(
            getattr(layout_root, "name", "") or Path(str(layout_root or "")).name
        )
        chapter_source_slice = _load_chapter_source_slice_if_available(
            runtime.storage,
            layout,
            project_id=inferred_project_id,
            chapter_number=chapter_number,
        )
        outcome = await step.run(
            ExtractInput(
                chapter_number=chapter_number,
                chapter_text=revised_text,
                known_characters=known_characters,
                chapter_outline_summary="",
                authoritative_character_genders=authoritative_character_genders or None,
                chapter_source_slice=chapter_source_slice,
            )
        )
        if outcome.chapter_exit_state is not None:
            chapter_exit_state = outcome.chapter_exit_state.model_copy(
                update={"chapter_number": chapter_number}
            )
            runtime.storage.save_json(
                layout.chapter_exit_state_path(chapter_number),
                chapter_exit_state.model_dump(mode="json"),
            )
            _log.info(
                "修复后 exit_state 已更新 | chapter=%d | location=%s | pov=%s",
                chapter_number,
                chapter_exit_state.location,
                chapter_exit_state.pov,
            )
    except Exception as exc:
        _log.warning(
            "修复后 exit_state 更新失败（不影响修复结果）| chapter=%d | error=%s",
            chapter_number,
            exc,
        )


async def _reindex_memory_after_repair(
    runtime: Any,
    project_id: str,
    chapter_number: int,
    revised_text: str,
    *,
    source: str = "post_repair_reindex",
    on_step_progress: StepCallback = None,
) -> None:
    try:
        get_memory_context = getattr(runtime, "get_memory_context", None)
        if not callable(get_memory_context):
            return
        storage = getattr(runtime, "storage", None)
        memory_ctx: Any = await get_memory_context(project_id=project_id, storage=storage)
        if memory_ctx is None:
            return
        finalize: Any = getattr(memory_ctx, "finalize_chapter_memory", None)
        if not callable(finalize):
            return
        stats: Any = await finalize(
            chapter_number=chapter_number,
            text=revised_text,
            creative_report_text="",
        )
        if on_step_progress:
            memory_status = memory_ctx.get_memory_status_for_ui()
            memory_status["save_success"] = stats.get("saved", False)
            memory_status["chapter"] = chapter_number
            memory_status["source"] = source
            on_step_progress("memory_updated", memory_status)
        _log.info(
            "文本修订后记忆重索引完成 | project=%s | chapter=%d | source=%s",
            project_id,
            chapter_number,
            source,
        )
    except Exception as exc:
        _log.warning(
            "文本修订后记忆重索引失败（不影响主流程结果）| chapter=%d | source=%s | error=%s",
            chapter_number,
            source,
            exc,
        )


async def _run_revised_text_postprocess(
    runtime: Any,
    *,
    project_id: str,
    layout: Any,
    chapter_number: int,
    source: str,
    original_text: str,
    revised_text: str,
    repaired_issue_types: list[str] | None = None,
    downstream_always_safe: bool = False,
    on_step_progress: StepCallback = None,
) -> bool:
    text_changed = revised_text != original_text
    _emit_step_progress_event(
        on_step_progress,
        "chapter_postprocess",
        _build_step_event_payload(
            project_id=project_id,
            chapter_number=chapter_number,
            source=source,
            status="started",
            text_changed=text_changed,
        ),
    )
    if not text_changed:
        _emit_step_progress_event(
            on_step_progress,
            "chapter_postprocess",
            _build_step_event_payload(
                project_id=project_id,
                chapter_number=chapter_number,
                source=source,
                status="skipped",
                reason="text_unchanged",
            ),
        )
        return False
    await _refresh_exit_state_after_repair(runtime, layout, chapter_number, revised_text)
    _emit_step_progress_event(
        on_step_progress,
        "chapter_postprocess",
        _build_step_event_payload(
            project_id=project_id,
            chapter_number=chapter_number,
            source=source,
            status="exit_state_refreshed",
        ),
    )
    _touch_downstream_context(
        layout,
        chapter_number,
        touch_from=_downstream_touch_from(
            chapter_number, repaired_issue_types, always_safe=downstream_always_safe
        ),
    )
    _emit_step_progress_event(
        on_step_progress,
        "chapter_postprocess",
        _build_step_event_payload(
            project_id=project_id,
            chapter_number=chapter_number,
            source=source,
            status="downstream_context_touched",
        ),
    )
    await _reindex_memory_after_repair(
        runtime,
        project_id,
        chapter_number,
        revised_text,
        source=f"{source}_reindex",
        on_step_progress=on_step_progress,
    )
    _emit_step_progress_event(
        on_step_progress,
        "chapter_postprocess",
        _build_step_event_payload(
            project_id=project_id,
            chapter_number=chapter_number,
            source=source,
            status="memory_reindexed",
        ),
    )
    _emit_step_progress_event(
        on_step_progress,
        "chapter_postprocess",
        _build_step_event_payload(
            project_id=project_id, chapter_number=chapter_number, source=source, status="completed"
        ),
    )
    return True
