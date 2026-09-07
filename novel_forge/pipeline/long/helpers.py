"""Helper utilities for long-form chapter orchestration."""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import Any

from novel_forge.core.utils.boundary_windows import take_tail_paragraphs
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout

_logger = get_logger("pipeline.long.helpers")


def load_json_if_exists(
    storage: FileSystemStorage,
    path: Path,
) -> dict[str, Any] | None:
    """Load JSON from path when present, else return None."""
    if not storage.exists(path):
        return None
    try:
        payload = storage.load_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_previous_chapter_ending(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    prev_chapter: int,
    *,
    min_chars: int = 600,
    max_chars: int = 1500,
    tail_paragraphs: int | None = None,
) -> str:
    """Load the ending of the previous chapter as complete paragraphs.

    When *tail_paragraphs* is provided, that exact paragraph window is used
    before applying *max_chars*. Otherwise this keeps the legacy behavior:
    work backwards paragraph-by-paragraph until *min_chars* is reached. This
    guarantees structurally clean output and stable token budgets regardless
    of chapter length.
    """
    candidates: list[tuple[int, int, Path]] = [(0, 100, layout.chapter_path(prev_chapter))]
    for version in range(4, -1, -1):
        candidates.append((1, version, layout.chapter_draft_path(prev_chapter, version)))

    existing_candidates: list[tuple[float, int, int, Path]] = []
    for kind, priority, path in candidates:
        if not storage.exists(path):
            continue
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            mtime = 0.0
        existing_candidates.append((mtime, -kind, priority, path))

    for _, _, _, path in sorted(existing_candidates, reverse=True):
        text = storage.load_text(path).strip()
        if not text:
            continue

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if tail_paragraphs is not None:
            return take_tail_paragraphs(text, tail_paragraphs, max_chars=max_chars)

        selected: list[str] = []
        total = 0
        for para in reversed(paragraphs):
            selected.insert(0, para)
            total += len(para)
            if total >= min_chars:
                break
        result = "\n\n".join(selected)
        if len(result) > max_chars:
            # 超限时从末尾裁切，并对齐到首个段落边界，保证段落完整
            result = result[-max_chars:]
            first_break = result.find("\n\n")
            if first_break != -1:
                result = result[first_break:].strip()
        return result.strip()
    return ""


def load_previous_volume_summary(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    previous_volume_number: int,
) -> str:
    """Load the previous volume audit summary for cross-volume continuity."""
    path = layout.volume_audit_report_path(previous_volume_number)
    data = load_json_if_exists(storage, path) or {}
    summary = str(data.get("volume_summary", "")).strip()
    next_focus = str(data.get("next_volume_focus", "")).strip()
    if summary and next_focus:
        return f"{summary}\n\n下一卷衔接建议：{next_focus}"
    return summary or next_focus


def load_prev_known_issues(runner: Any, bundle: Any, chapter_number: int) -> list[dict[str, str]]:
    """Load previously detected issues from stored reports for this chapter.

    Returns a compact list of {type, severity, summary} dicts suitable for
    injection into the draft prompt as "known issues to avoid".  At most 6
    entries are returned (highest-severity first) to stay within token budget.
    """
    issues: list[dict[str, str]] = []
    layout = getattr(bundle, "layout", None)
    if layout is None:
        return issues

    # --- Project-level issue ledger persisted by volume/book/summary audits ---
    try:
        from novel_forge.pipeline.long.services.issue_ledger import IssueLedgerService

        issues.extend(
            IssueLedgerService(layout.states_dir).load_prev_known_issues(chapter_number)
        )
    except Exception as exc:
        _logger.debug("load_project_issue_ledger_failed | chapter=%d | error=%s", chapter_number, exc)

    # --- Runtime notes injected by session handlers (including auto-repair hints) ---
    try:
        outline_notes = str(getattr(bundle.chapter_outline, "notes", "") or "").strip()
        if outline_notes:
            auto_block_match = _re.search(
                r"【自动修复提示】[\s\S]*?(?=\n\s*\n|$)",
                outline_notes,
            )
            auto_block = auto_block_match.group(0) if auto_block_match else ""
            source_text = auto_block or outline_notes
            for line in str(source_text).splitlines():
                text = line.strip()
                if not text:
                    continue
                if text.startswith("【自动修复提示】"):
                    continue
                text = _re.sub(r"^[\-•\d\.\)\s]+", "", text).strip()
                if not text:
                    continue
                if "需注意以下连贯性问题" in text:
                    continue
                issues.append(
                    {
                        "category": "自动修复提示",
                        "severity": "high",
                        "summary": text[:180],
                    }
                )
                if len(issues) >= 6:
                    break
    except Exception as exc:
        _logger.debug("load_outline_notes_failed | chapter=%d | error=%s", chapter_number, exc)

    # --- Chapter repair report (prompt_leaks, factual, continuity, expression) ---
    try:
        repair_path = layout.chapter_repair_report_path(chapter_number)
        if runner._storage.exists(repair_path):
            raw = runner._storage.load_json(repair_path)
            for field, label in [
                ("factual_errors", "事实错误"),
                ("continuity_errors", "连续性错误"),
                ("prompt_leaks", "提示词泄露"),
                ("expression_errors", "表达问题"),
            ]:
                for item in (raw.get(field) or [])[:2]:
                    text = str(item).strip()
                    if text:
                        issues.append({"category": label, "severity": "high", "summary": text})
    except Exception as exc:
        _logger.debug("load_prev_repair_report_failed | chapter=%d | error=%s", chapter_number, exc)

    # --- Causal validation report (structural / causal-chain issues) ---
    try:
        causal_path = layout.chapter_causal_report_path(chapter_number)
        if runner._storage.exists(causal_path):
            raw = runner._storage.load_json(causal_path)
            for raw_issue in raw.get("issues") or []:
                sev = str(raw_issue.get("severity", "medium")).lower()
                if sev not in {"critical", "high"}:
                    continue
                summary = str(
                    raw_issue.get("summary", "") or raw_issue.get("fix_suggestion", "")
                ).strip()
                loc = str(raw_issue.get("location", "")).strip()
                if summary:
                    label = f"{loc}：{summary}" if loc else summary
                    issues.append({"category": "因果链断裂", "severity": sev, "summary": label})
    except Exception as exc:
        _logger.debug("load_prev_causal_report_failed | chapter=%d | error=%s", chapter_number, exc)

    # --- Continuity report (include medium recurrent-prone types) ---
    try:
        cont_path = layout.continuity_report_path(chapter_number)
        if runner._storage.exists(cont_path):
            raw = runner._storage.load_json(cont_path)
            recurrent_medium_types = {
                "location_jump",
                "bridge_contract_not_followed",
                "time_marker_invalid",
                "text_repetition",
                "forbidden_element_violation",
                "pov_intrusion",
            }
            for raw_issue in raw.get("issues") or []:
                sev = str(raw_issue.get("severity", "medium")).lower()
                issue_type = str(raw_issue.get("issue_type", "")).lower()
                if sev not in {"critical", "high"} and issue_type not in recurrent_medium_types:
                    continue
                summary = str(raw_issue.get("summary", "")).strip()
                if not summary:
                    continue
                evidence = str(raw_issue.get("evidence", "")).strip()
                compact = summary if not evidence else f"{summary}（{evidence[:80]}）"
                issues.append(
                    {
                        "category": "连贯性风险",
                        "severity": sev if sev in {"critical", "high", "medium"} else "medium",
                        "summary": compact,
                    }
                )
    except Exception as exc:
        _logger.debug(
            "load_prev_continuity_report_failed | chapter=%d | error=%s", chapter_number, exc
        )

    # --- Editorial report (soft publication-level issues that should not recur) ---
    try:
        editorial_path = layout.reports_dir / f"chapter_{chapter_number:03d}_editorial.json"
        if runner._storage.exists(editorial_path):
            raw = runner._storage.load_json(editorial_path)
            for raw_issue in raw.get("findings") or []:
                sev = str(raw_issue.get("severity", "medium")).lower()
                if sev not in {"critical", "high", "medium"}:
                    continue
                summary = str(raw_issue.get("summary", "")).strip()
                recommendation = str(raw_issue.get("recommendation", "")).strip()
                text = summary if not recommendation else f"{summary}；建议：{recommendation}"
                if text:
                    issues.append(
                        {
                            "category": "编辑契约风险",
                            "severity": sev,
                            "summary": text[:180],
                        }
                    )
    except Exception as exc:
        _logger.debug("load_prev_editorial_report_failed | chapter=%d | error=%s", chapter_number, exc)

    # Deduplicate and cap at 6 to stay within token budget
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in issues:
        key = item["summary"][:60]
        if key not in seen:
            seen.add(key)
            deduped.append(item)
        if len(deduped) >= 6:
            break
    return deduped
