"""Shared helper to render structured issue blocks for repair prompts.

Produces a consistent, compact text block from any domain-specific issue object
(CausalIssue, ContinuityIssue, ReadingPowerIssue, AuditIssue, or plain dict)
so that repair LLM prompts receive full structured context instead of bare
summary strings.
"""

from __future__ import annotations

from typing import Any


def _field(issue: Any, key: str, default: Any = "") -> Any:
    """Read a field from a dict or attribute-based object."""
    if isinstance(issue, dict):
        return issue.get(key, default)
    return getattr(issue, key, default)


def _clean(value: Any, *, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；： \n") + "…"


def _positive_int(value: Any) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return 0
    return ivalue if ivalue > 0 else 0


def _severity_zh(severity: str) -> str:
    """Map severity to Chinese label for prompt readability."""
    return {
        "critical": "严重",
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(str(severity or "").strip().lower(), str(severity or "中"))


def _format_location(issue: Any) -> str:
    """Build a human-readable location string."""
    location = _clean(_field(issue, "location"), limit=80)
    para_start = _positive_int(_field(issue, "paragraph_start", 0))
    para_end = _positive_int(_field(issue, "paragraph_end", 0))
    if para_start > 0:
        if para_end > para_start:
            para_range = f"第{para_start}-{para_end}段"
        else:
            para_range = f"第{para_start}段"
        if location:
            return f"{location}（{para_range}）"
        return para_range
    return location or "未定位"


def _format_fix_actions(issue: Any, *, limit: int = 200) -> str:
    """Join fix_actions into a readable string."""
    actions = _field(issue, "fix_actions", [])
    if not isinstance(actions, (list, tuple)):
        return ""
    parts: list[str] = []
    total_len = 0
    for action in actions[:5]:
        text = _clean(action, limit=120)
        if not text:
            continue
        if total_len + len(text) > limit:
            break
        parts.append(text)
        total_len += len(text) + 2
    return "；".join(parts)


def _format_postconditions(issue: Any, *, limit: int = 200) -> str:
    """Format postconditions as readable text."""
    postconditions = _field(issue, "postconditions", [])
    if not isinstance(postconditions, (list, tuple)) or not postconditions:
        return ""
    parts: list[str] = []
    total_len = 0
    for pc in postconditions[:4]:
        if isinstance(pc, dict):
            desc = _clean(pc.get("description", ""), limit=100)
        else:
            desc = _clean(getattr(pc, "description", ""), limit=100)
        if not desc:
            continue
        if total_len + len(desc) > limit:
            break
        parts.append(desc)
        total_len += len(desc) + 2
    return "；".join(parts)


def build_issue_prompt_block(
    issue: Any,
    *,
    include_evidence: bool = True,
    include_fix_actions: bool = True,
    include_postconditions: bool = True,
    max_evidence_chars: int = 300,
    max_fix_chars: int = 200,
) -> str:
    """Build a structured prompt block from any issue object.

    The block contains the issue ID, type, severity, location, evidence quote,
    fix suggestion, fix actions, and postconditions — all formatted for direct
    injection into repair LLM prompts.

    Example output::

        [Issue ch003-causal-a1b2c3d4]
        类型: causal_break | 严重度: 高 | 位置: 第5-7段
        证据: "原文引用..."
        建议修复: 补充因果链说明
        操作: 改写因果链；补充动机
        验收条件: 因果链完整连接
    """
    issue_id = _clean(_field(issue, "issue_id"), limit=60)
    issue_type = _clean(_field(issue, "issue_type"), limit=60)
    severity = _severity_zh(_field(issue, "severity", "medium"))
    location = _format_location(issue)

    # Header line
    id_label = issue_id or "no-id"
    lines: list[str] = [f"[Issue {id_label}]"]

    # Type / severity / location line
    detail_parts: list[str] = []
    if issue_type:
        detail_parts.append(f"类型: {issue_type}")
    detail_parts.append(f"严重度: {severity}")
    if location and location != "未定位":
        detail_parts.append(f"位置: {location}")
    lines.append(" | ".join(detail_parts))

    # Summary (always included — the core description)
    summary = _clean(_field(issue, "summary"), limit=240)
    if summary:
        lines.append(f"问题: {summary}")

    # Evidence
    if include_evidence:
        evidence_quote = _clean(
            _field(issue, "evidence_quote") or _field(issue, "evidence"),
            limit=max_evidence_chars,
        )
        if evidence_quote:
            lines.append(f"证据: \"{evidence_quote}\"")

    # Fix suggestion
    fix_suggestion = _clean(_field(issue, "fix_suggestion"), limit=max_fix_chars)
    if fix_suggestion:
        lines.append(f"建议修复: {fix_suggestion}")

    # Fix actions
    if include_fix_actions:
        fix_actions_text = _format_fix_actions(issue, limit=max_fix_chars)
        if fix_actions_text:
            lines.append(f"操作: {fix_actions_text}")

    # Fix mode
    fix_mode = _clean(_field(issue, "fix_mode"), limit=30)
    if fix_mode:
        lines.append(f"修复模式: {fix_mode}")

    # Postconditions
    if include_postconditions:
        pc_text = _format_postconditions(issue, limit=max_fix_chars)
        if pc_text:
            lines.append(f"验收条件: {pc_text}")

    return "\n".join(lines)


def build_issues_prompt_block(
    issues: list[Any],
    *,
    header: str = "",
    max_issues: int = 12,
    **kwargs: Any,
) -> str:
    """Build a multi-issue prompt block with an optional header.

    Convenience wrapper around :func:`build_issue_prompt_block` for rendering
    a list of issues into a single prompt section.
    """
    if not issues:
        return ""
    blocks: list[str] = []
    if header:
        blocks.append(header)
    for issue in issues[:max_issues]:
        blocks.append(build_issue_prompt_block(issue, **kwargs))
    return "\n\n".join(blocks)
