"""Shared helpers for whole-book consistency audit execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import StorageError
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_ops.execution_book_common import AuditError
from novel_forge.workspace.helpers.execution_helpers import _build_numbered_paragraph_text


def _classify_audit_exception(exc: Exception, checkpoint_exists: bool = False) -> AuditError:
    """Classify an exception and create an AuditError with appropriate recovery info."""
    error_message = str(exc)
    error_type = "unknown"
    recoverable = False
    suggested_action = "请查看错误信息后重试"
    checkpoint_available = checkpoint_exists

    exc_type_name = type(exc).__name__.lower()

    if isinstance(exc, ValueError):
        if "至少需要" in error_message:
            error_type = "validation"
            recoverable = False
            suggested_action = "至少需要2个已完成章节"
            checkpoint_available = False
        elif "未找到上次审计报告" in error_message:
            error_type = "validation"
            recoverable = False
            suggested_action = "请先运行一次完整审计"
            checkpoint_available = False
    elif isinstance(exc, StorageError):
        error_type = "storage"
        recoverable = False
        suggested_action = "检查项目目录权限"
        checkpoint_available = False
    elif (
        "context" in exc_type_name
        or "contextlength" in exc_type_name
        or "context" in error_message.lower()
    ):
        error_type = "timeout"
        recoverable = True
        suggested_action = "减少审计章节数或增加上下文预算"
        checkpoint_available = False
    elif "rate" in exc_type_name or "timeout" in exc_type_name or "TimeoutError" in exc_type_name:
        error_type = "timeout"
        recoverable = True
        suggested_action = "检查模型配置后重试"
        checkpoint_available = checkpoint_exists
    elif "model" in exc_type_name or "gateway" in exc_type_name or "api" in exc_type_name:
        error_type = "model"
        recoverable = True
        suggested_action = "检查模型配置后重试"
        checkpoint_available = checkpoint_exists

    return AuditError(
        message=error_message,
        recoverable=recoverable,
        suggested_action=suggested_action,
        checkpoint_available=checkpoint_available,
        error_type=error_type,
    )


def _audit_issue_identity(issue: dict[str, Any]) -> str:
    issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
    if issue_id:
        return f"id:{issue_id}"
    category = str(issue.get("category", "") or "").strip().lower()
    severity = str(issue.get("severity", "") or "").strip().lower()
    chapter = str(issue.get("primary_chapter") or issue.get("chapter_number") or "").strip()
    paragraph = str(issue.get("paragraph_index") or "").strip()
    description = " ".join(str(issue.get("description", "") or "").split())[:160]
    evidence = " ".join(str(issue.get("evidence", "") or "").split())[:120]
    return f"fp:{category}:{severity}:{chapter}:{paragraph}:{description}:{evidence}"


def _audit_issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": str(issue.get("issue_id") or issue.get("id") or "").strip(),
        "category": str(issue.get("category", "") or "").strip(),
        "severity": str(issue.get("severity", "") or "").strip(),
        "primary_chapter": issue.get("primary_chapter") or issue.get("chapter_number") or 0,
        "paragraph_index": issue.get("paragraph_index", 0),
        "description": str(issue.get("description", "") or "").strip()[:240],
    }


def _audit_severity_counts(issues: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues.values():
        severity = str(issue.get("severity", "warning") or "warning").strip().lower()
        if severity not in counts:
            severity = "warning"
        counts[severity] += 1
    return counts


def _audit_score(payload: dict[str, Any]) -> float | None:
    score = payload.get("consistency_score")
    if isinstance(score, (int, float)):
        return float(score)
    return None


def _compare_audit_reports(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    max_items: int = 50,
) -> dict[str, Any]:
    """Compare two audit reports and return issue-level delta metadata."""

    previous_raw_issues = previous.get("issues", [])
    current_raw_issues = current.get("issues", [])
    if not isinstance(previous_raw_issues, list):
        previous_raw_issues = []
    if not isinstance(current_raw_issues, list):
        current_raw_issues = []

    previous_issues = {
        _audit_issue_identity(issue): issue
        for issue in previous_raw_issues
        if isinstance(issue, dict)
    }
    current_issues = {
        _audit_issue_identity(issue): issue
        for issue in current_raw_issues
        if isinstance(issue, dict)
    }

    previous_keys = set(previous_issues)
    current_keys = set(current_issues)
    new_keys = sorted(current_keys - previous_keys)
    resolved_keys = sorted(previous_keys - current_keys)
    persisting_keys = sorted(current_keys & previous_keys)

    previous_counts = _audit_severity_counts(previous_issues)
    current_counts = _audit_severity_counts(current_issues)
    severity_delta = {
        key: current_counts.get(key, 0) - previous_counts.get(key, 0)
        for key in ("critical", "warning", "info")
    }

    previous_score = _audit_score(previous)
    current_score = _audit_score(current)
    score_delta = (
        round(current_score - previous_score, 4)
        if previous_score is not None and current_score is not None
        else None
    )

    return {
        "basis": "issue_id_or_fingerprint",
        "previous_issue_count": len(previous_issues),
        "current_issue_count": len(current_issues),
        "issue_count_delta": len(current_issues) - len(previous_issues),
        "new_issue_count": len(new_keys),
        "resolved_issue_count": len(resolved_keys),
        "persisting_issue_count": len(persisting_keys),
        "consistency_score_delta": score_delta,
        "severity_delta": severity_delta,
        "new_issues": [_audit_issue_summary(current_issues[key]) for key in new_keys[:max_items]],
        "resolved_issues": [
            _audit_issue_summary(previous_issues[key]) for key in resolved_keys[:max_items]
        ],
    }



def _stable_json_digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    if not path.exists():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _book_audit_resume_signature(
    *,
    layout: ProjectLayout,
    project_id: str,
    completed_chapters: list[int],
    chapter_summaries: list[dict[str, Any]],
    chapter_issue_pool: list[dict[str, Any]],
    analysis_mode: str,
    prompt_hint: str,
    location_strictness: str,
    max_tokens: int,
    temperature: float,
    audit_max_chapters_per_batch: int,
    audit_max_issues_per_chunk: int,
    audit_issue_pool_max_items: int,
    two_phase_enabled: bool,
    two_phase_threshold: float,
    two_phase_max_target_chapters: int,
    parallel_chunks: bool,
    parallel_dimensions: bool,
    memory_enhancement_context: str,
    world_rules: list[str],
    world_setting: str,
    story_theme: str,
    conflict_hint: str,
    world_hint: str,
    character_profiles_compact: list[dict[str, Any]],
    arc_summary: str,
) -> str:
    """Signature for reusing entry-level audit checkpoints safely."""
    chapter_sources: list[dict[str, Any]] = []
    for chapter_number in completed_chapters:
        chapter_path = layout.chapter_path(chapter_number)
        review_draft_path = layout.chapter_review_draft_path(chapter_number)
        source_path = chapter_path if chapter_path.exists() else review_draft_path
        chapter_sources.append(
            {
                "chapter_number": chapter_number,
                "path": source_path.name,
                "sha256": _file_digest(source_path),
            }
        )

    payload = {
        "schema_version": 1,
        "project_id": project_id,
        "completed_chapters": completed_chapters,
        "chapter_summaries": chapter_summaries,
        "chapter_issue_pool": chapter_issue_pool,
        "analysis_mode": analysis_mode,
        "prompt_hint": prompt_hint,
        "location_strictness": location_strictness,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "audit_max_chapters_per_batch": audit_max_chapters_per_batch,
        "audit_max_issues_per_chunk": audit_max_issues_per_chunk,
        "audit_issue_pool_max_items": audit_issue_pool_max_items,
        "two_phase_enabled": two_phase_enabled,
        "two_phase_threshold": two_phase_threshold,
        "two_phase_max_target_chapters": two_phase_max_target_chapters,
        "parallel_chunks": parallel_chunks,
        "parallel_dimensions": parallel_dimensions,
        "memory_enhancement_context": memory_enhancement_context,
        "world_rules": world_rules,
        "world_setting": world_setting,
        "story_theme": story_theme,
        "conflict_hint": conflict_hint,
        "world_hint": world_hint,
        "character_profiles_compact": character_profiles_compact,
        "arc_summary": arc_summary,
        "chapter_sources": chapter_sources,
        "canon_sha256": _file_digest(layout.root / "canon" / "canon_current.json"),
        "outline_sha256": _file_digest(layout.outline_path),
        "characters_sha256": _file_digest(layout.characters_path),
        "story_bible_sha256": _file_digest(layout.root / "story_bible.json"),
        "spec_sha256": _file_digest(layout.root / "spec.json"),
    }
    return _stable_json_digest(payload)



def _extract_flagged_chapters_from_result(result: Any) -> set[int]:
    """Extract unique chapter numbers that have issues from an audit result.

    Looks at each issue's ``primary_chapter`` and ``chapters_involved`` fields
    to collect all chapter numbers flagged with consistency problems.
    """
    flagged: set[int] = set()
    for issue in getattr(result, "issues", []) or []:
        if hasattr(issue, "primary_chapter"):
            ch = getattr(issue, "primary_chapter", 0)
            if ch > 0:
                flagged.add(ch)
        if hasattr(issue, "chapters_involved"):
            for ch in getattr(issue, "chapters_involved", []) or []:
                if ch > 0:
                    flagged.add(ch)
        # Also handle dict-style issues (from raw parsed results)
        if isinstance(issue, dict):
            primary = issue.get("primary_chapter", 0)
            if primary and int(primary) > 0:
                flagged.add(int(primary))
            for ch in issue.get("chapters_involved", []) or []:
                if ch and int(ch) > 0:
                    flagged.add(int(ch))
    return flagged


def _audit_severity_weight(value: Any) -> int:
    severity = str(value or "info").strip().lower()
    if severity in {"critical", "high"}:
        return 30
    if severity in {"warning", "major", "medium"}:
        return 15
    return 5


def _issue_as_dict(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return issue
    if hasattr(issue, "model_dump"):
        dumped = issue.model_dump()
        return dumped if isinstance(dumped, dict) else {}

    _raw_pi = getattr(issue, "paragraph_index", 0) or 0
    if isinstance(_raw_pi, list):
        _raw_pi = _raw_pi[0] if _raw_pi else 0
    try:
        _pi = int(_raw_pi)
    except (TypeError, ValueError):
        _pi = 0

    return {
        "issue_id": str(getattr(issue, "issue_id", "") or ""),
        "category": str(getattr(issue, "category", "") or ""),
        "severity": str(getattr(issue, "severity", "") or ""),
        "primary_chapter": int(getattr(issue, "primary_chapter", 0) or 0),
        "paragraph_index": _pi,
        "description": str(getattr(issue, "description", "") or ""),
    }


def _derive_book_audit_artifact_summary(issues: list[Any]) -> str:
    issue_dicts = [_issue_as_dict(issue) for issue in issues]
    issue_dicts = [issue for issue in issue_dicts if issue]
    if not issue_dicts:
        return "未发现明确的全书一致性问题。"

    counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    categories: dict[str, int] = {}
    for issue in issue_dicts:
        severity = str(issue.get("severity", "info") or "info").strip().lower()
        if severity in {"critical", "high"}:
            normalized = "critical"
        elif severity in {"warning", "major", "medium"}:
            normalized = "warning"
        else:
            normalized = "info"
        counts[normalized] = counts.get(normalized, 0) + 1
        category = str(issue.get("category", "unknown") or "unknown").strip()
        categories[category] = categories.get(category, 0) + 1

    parts = [f"共发现 {len(issue_dicts)} 个一致性问题"]
    if counts.get("critical"):
        parts.append(f"{counts['critical']} 个严重")
    if counts.get("warning"):
        parts.append(f"{counts['warning']} 个警告")
    if counts.get("info"):
        parts.append(f"{counts['info']} 个提示")
    if categories:
        top_categories = sorted(categories.items(), key=lambda item: (-item[1], item[0]))[:3]
        parts.append(
            "主要类型：" + "、".join(f"{category} {count}项" for category, count in top_categories)
        )

    return "；".join(parts)


def _normalize_audit_report_payload(report_payload: dict[str, Any]) -> dict[str, Any]:
    issues = report_payload.get("issues", [])
    field_sources = report_payload.get("field_sources")
    if not isinstance(field_sources, dict):
        field_sources = {}
    report_payload["field_sources"] = field_sources
    if isinstance(issues, list):
        report_payload["issue_summary"] = _derive_book_audit_artifact_summary(issues)
        if str(field_sources.get("summary", "") or "") != "llm":
            report_payload["summary"] = report_payload["issue_summary"]
            field_sources["summary"] = "derived"
        if not str(field_sources.get("consistency_score", "") or ""):
            field_sources["consistency_score"] = (
                "llm" if report_payload.get("consistency_score") not in {None, 0} else "derived"
            )
    report_payload["acceptance"] = _build_book_acceptance_summary(report_payload)
    return report_payload


def _book_issue_counts(issues: list[Any]) -> dict[str, int]:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues:
        data = _issue_as_dict(issue)
        severity = str(data.get("severity", "info") or "info").strip().lower()
        if severity in {"critical", "high"}:
            counts["critical"] += 1
        elif severity in {"warning", "major", "medium"}:
            counts["warning"] += 1
        else:
            counts["info"] += 1
    return counts


def _build_book_acceptance_summary(report_payload: dict[str, Any]) -> dict[str, Any]:
    issues = report_payload.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    counts = _book_issue_counts(issues)
    score_raw = report_payload.get("consistency_score", 0.0)
    try:
        score = float(score_raw or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    auto_repair = report_payload.get("auto_repair")
    if not isinstance(auto_repair, dict):
        auto_repair = {}
    post_repair = auto_repair.get("post_repair_summary")
    if not isinstance(post_repair, dict):
        post_repair = {}
    remaining_after_repair = int(post_repair.get("issues_remaining", 0) or 0)
    manual_review = list(auto_repair.get("needs_manual_review", []) or [])
    blocked = int(auto_repair.get("blocked_chapters", 0) or 0)
    failed = int(auto_repair.get("failed_chapters", 0) or 0)
    post_audit = auto_repair.get("post_repair_targeted_audit")
    post_audit_issues = 0
    if isinstance(post_audit, dict):
        post_audit_issues = int(post_audit.get("issue_count", 0) or 0)

    high_risk = counts["critical"] + counts["warning"]
    if counts["critical"] or failed or blocked:
        status = "needs_attention"
        recommendation = "先处理严重问题、失败或被回滚章节，再进入导出。"
    elif remaining_after_repair or post_audit_issues or manual_review:
        status = "review_recommended"
        recommendation = "建议人工复核剩余问题与二次审计命中章节，再决定是否导出。"
    elif high_risk == 0 and score >= 8.5:
        status = "ready_to_export"
        recommendation = "全书一致性状态良好，可以进入导出/发布前检查。"
    else:
        status = "pass_with_notes"
        recommendation = "可进入导出，但建议保留审计报告中的提示项供最终校读参考。"

    return {
        "status": status,
        "recommendation": recommendation,
        "consistency_score": round(score, 2),
        "issue_count": len(issues),
        "critical_issues": counts["critical"],
        "warning_issues": counts["warning"],
        "info_issues": counts["info"],
        "remaining_after_repair": remaining_after_repair,
        "post_repair_targeted_issue_count": post_audit_issues,
        "manual_review_chapters": manual_review,
        "suggest_export": status in {"ready_to_export", "pass_with_notes"},
    }


def _rank_book_audit_target_chapters(
    *,
    flagged_chapters: set[int],
    summary_result: Any,
    chapter_issue_pool: list[dict[str, Any]],
    completed_chapters: list[int],
    max_targets: int,
) -> list[int]:
    """Rank chapters for targeted full-text audit.

    The first phase only has summaries and existing issue-panel signals, so this
    favors high-severity, high-confidence chapters while preserving book order
    as a deterministic tie-breaker.
    """
    completed_set = set(completed_chapters)
    order = {chapter: idx for idx, chapter in enumerate(completed_chapters)}
    scores: dict[int, float] = {
        chapter: 1.0 for chapter in flagged_chapters if chapter in completed_set
    }

    for issue in getattr(summary_result, "issues", []) or []:
        issue_data = _issue_as_dict(issue)
        severity_score = _audit_severity_weight(issue_data.get("severity"))
        confidence = float(issue_data.get("confidence", 0.0) or 0.0)
        involved = set()
        primary = int(issue_data.get("primary_chapter", 0) or 0)
        if primary > 0:
            involved.add(primary)
        raw_involved = issue_data.get("chapters_involved", [])
        if isinstance(raw_involved, list):
            for chapter in raw_involved:
                try:
                    chapter_num = int(chapter)
                except (TypeError, ValueError):
                    continue
                if chapter_num > 0:
                    involved.add(chapter_num)
        for chapter in involved:
            if chapter in completed_set:
                scores[chapter] = scores.get(chapter, 0.0) + severity_score + confidence

    for item in chapter_issue_pool:
        if not isinstance(item, dict):
            continue
        try:
            chapter = int(item.get("chapter_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if chapter <= 0 or chapter not in completed_set:
            continue
        scores[chapter] = scores.get(chapter, 0.0) + (
            _audit_severity_weight(item.get("severity")) / 3
        )

    ranked = sorted(
        scores,
        key=lambda chapter: (-scores[chapter], order.get(chapter, 10**9)),
    )
    return ranked[: max(1, min(int(max_targets or 1), len(completed_chapters)))]


def _merge_two_phase_audit_results(summary_result: Any, targeted_result: Any) -> Any:
    """Keep summary-scan signals while prioritizing targeted full-text findings."""
    merged_issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    for result in (targeted_result, summary_result):
        for issue in getattr(result, "issues", []) or []:
            issue_data = _issue_as_dict(issue)
            key = (
                str(issue_data.get("issue_id", "") or "").strip(),
                str(issue_data.get("category", "") or "").strip(),
                int(issue_data.get("primary_chapter", 0) or 0),
                (lambda v: int(v[0]) if isinstance(v, list) and v else (int(v) if v else 0))(
                    issue_data.get("paragraph_index", 0)
                ),
                str(issue_data.get("description", "") or "").strip()[:120],
            )
            if key in seen:
                continue
            seen.add(key)
            merged_issues.append(issue_data)

    targeted_result.issues = merged_issues

    summary_plan = getattr(summary_result, "repair_plan", []) or []
    targeted_plan = getattr(targeted_result, "repair_plan", []) or []
    if isinstance(targeted_plan, list) or isinstance(summary_plan, list):
        targeted_result.repair_plan = [
            item
            for item in [
                *(targeted_plan if isinstance(targeted_plan, list) else []),
                *(summary_plan if isinstance(summary_plan, list) else []),
            ]
            if isinstance(item, dict)
        ]

    summary_score = float(getattr(summary_result, "consistency_score", 0.0) or 0.0)
    targeted_score = float(getattr(targeted_result, "consistency_score", 0.0) or 0.0)
    scores = [score for score in (summary_score, targeted_score) if score > 0]
    if scores:
        targeted_result.consistency_score = min(scores)

    targeted_result.summary = _derive_book_audit_artifact_summary(merged_issues)

    chapters = set(getattr(targeted_result, "chapters_audited", []) or [])
    chapters.update(getattr(summary_result, "chapters_audited", []) or [])
    if chapters:
        targeted_result.chapters_audited = sorted(int(ch) for ch in chapters if int(ch) > 0)
    return targeted_result


def _load_chapter_texts_for_numbers(
    layout: Any,
    chapter_numbers: list[int],
    max_chars: int,
) -> list[dict[str, Any]]:
    """Load chapter texts for specific chapter numbers, same format as the main audit."""
    texts: list[dict[str, Any]] = []
    for ch_num in chapter_numbers:
        text = ""
        chapter_path = layout.chapter_path(ch_num)
        review_draft_path = layout.chapter_review_draft_path(ch_num)
        if chapter_path.exists():
            text = chapter_path.read_text(encoding="utf-8")
        elif review_draft_path.exists():
            text = review_draft_path.read_text(encoding="utf-8")
        source_chars = len(text)
        truncated = source_chars > max_chars
        if truncated:
            text = text[:max_chars]
        numbered_text, paragraph_count, paragraphs = _build_numbered_paragraph_text(text)
        texts.append(
            {
                "chapter_number": ch_num,
                "numbered_text": numbered_text,
                "paragraph_count": paragraph_count,
                "paragraphs": paragraphs,
                "source_chars": source_chars,
                "truncated": truncated,
            }
        )
    return texts
