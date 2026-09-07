"""Prompt context helpers — pre-format data for Jinja2 templates.

These helpers extract repeated data-preparation logic out of .j2 templates
so that templates only handle simple interpolation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Sequence

# ── Generic list formatting ──────────────────────────────────────


def format_list_or_default(
    items: Iterable[Any] | None,
    sep: str = "；",
    default: str = "无",
) -> str:
    """Join *items* with *sep*, returning *default* when empty/None.

    Replaces the ubiquitous ``X | join('；') if X else '无'`` pattern in templates.
    """
    if not items:
        return default
    parts = [str(i).strip() for i in items if str(i).strip()]
    return sep.join(parts) if parts else default


# ── Canon character rendering ────────────────────────────────────


def format_canon_characters(characters: Dict[str, Any] | None) -> str:
    """Render canon character states as a multi-line text block.

    Replaces the repeated ``{% for name, cs in canon_context.characters.items() %}``
    blocks found in plan_chapter, draft_chapter, edit_chapter, volume_audit.
    """
    if not characters:
        return "暂无已知角色信息"
    lines: list[str] = []
    for name, cs in characters.items():
        alive = getattr(cs, "alive", True)
        status = "存活" if alive else "已故"
        location = getattr(cs, "location", "") or "未知"
        emotional_state = getattr(cs, "emotional_state", "") or ""
        inventory = getattr(cs, "inventory", []) or []
        parts = [f"【{name}】 状态={status}, 位置={location}"]
        if emotional_state:
            parts.append(f"情绪={emotional_state}")
        if inventory:
            parts.append(f"持有={format_list_or_default(inventory, '、')}")
        lines.append(", ".join(parts))
    return "\n".join(lines)


# ── Event / timeline rendering ───────────────────────────────────


def format_events(events: Sequence[Any] | None) -> str:
    """Render a list of timeline events as a numbered text block."""
    if not events:
        return "暂无事件记录"
    lines: list[str] = []
    for idx, ev in enumerate(events, 1):
        chapter = getattr(ev, "chapter", "?")
        desc = getattr(ev, "description", str(ev))
        chars = getattr(ev, "characters_involved", [])
        char_str = f" (涉及: {format_list_or_default(chars, '、')})" if chars else ""
        lines.append(f"{idx}. [第{chapter}章] {desc}{char_str}")
    return "\n".join(lines)


# ── Foreshadowing rendering ─────────────────────────────────────


def format_foreshadowing(items: Sequence[Any] | None) -> str:
    """Render active foreshadowing items."""
    if not items:
        return "暂无伏笔"
    lines: list[str] = []
    for fs in items:
        desc = getattr(fs, "description", str(fs))
        status = getattr(fs, "status", "")
        planted = getattr(fs, "planted_chapter", "?")
        lines.append(f"- [{status}] 第{planted}章埋设: {desc}")
    return "\n".join(lines)


# ── Relationship rendering ───────────────────────────────────────


def format_relationships(relationships: Sequence[Any] | None) -> str:
    """Render active relationships."""
    if not relationships:
        return "暂无关系记录"
    lines: list[str] = []
    for rel in relationships:
        chars = getattr(rel, "characters", [])
        rel_type = getattr(rel, "relationship_type", "")
        desc = getattr(rel, "description", "")
        pair = format_list_or_default(chars, " ↔ ", "?")
        parts = [f"- {pair}"]
        if rel_type:
            parts.append(f"({rel_type})")
        if desc:
            parts.append(f": {desc}")
        lines.append("".join(parts))
    return "\n".join(lines)


# ── Plot thread rendering ───────────────────────────────────────


def format_plot_threads(threads: Sequence[Any] | None) -> str:
    """Render active plot threads."""
    if not threads:
        return "暂无情节线索"
    lines: list[str] = []
    for t in threads:
        name = getattr(t, "thread_name", "") or getattr(t, "thread_id", "?")
        status = getattr(t, "status", "")
        desc = getattr(t, "description", "")
        parts = [f"- [{status}] {name}"]
        if desc:
            parts.append(f": {desc}")
        lines.append("".join(parts))
    return "\n".join(lines)


# ── Word-count → character-count recommendation ─────────────────


def recommend_character_count(expected_total_words: int | None) -> str:
    """Map total word target to a recommended main character count.

    Extracted from character-count recommendations originally in
    ``init_character_bible.j2``.
    nested ``{% if %}`` branches.
    """
    if expected_total_words is None:
        return ""
    words = int(expected_total_words)
    if words < 100_000:
        return "建议主要角色数量: 3-5人"
    if words < 300_000:
        return "建议主要角色数量: 5-8人"
    if words < 800_000:
        return "建议主要角色数量: 8-12人"
    return "建议主要角色数量: 12-15人"


# ── Alignment / repair report rendering ──────────────────────────


def format_alignment_report(report: Any | None) -> str:
    """Render an AlignmentReport as a text block for edit context."""
    if report is None:
        return ""
    score = getattr(report, "alignment_score", 0)
    summary = getattr(report, "summary", "")
    actions = getattr(report, "repair_actions", [])
    lines = [f"对齐评分: {score:.1f}/10"]
    if summary:
        lines.append(f"总结: {summary}")
    if actions:
        lines.append(f"建议修复: {format_list_or_default(actions, '；')}")
    return "\n".join(lines)


def format_repair_report(report: Any | None) -> str:
    """Render a ChapterRepairReport as a text block."""
    if report is None:
        return ""
    lines: list[str] = []
    for field_name, label in [
        ("factual_errors", "事实错误"),
        ("continuity_errors", "连续性错误"),
        ("expression_errors", "表达问题"),
        ("prompt_leaks", "提示泄漏"),
    ]:
        items = getattr(report, field_name, [])
        if items:
            lines.append(f"{label}: {format_list_or_default(items, '；')}")
    actions = getattr(report, "repair_actions", [])
    if actions:
        lines.append(f"修复建议: {format_list_or_default(actions, '；')}")
    return "\n".join(lines) if lines else ""


# ── Exit state rendering ────────────────────────────────────────


def format_exit_state(exit_state: Any | None) -> str:
    """Render a ChapterExitState for prompt context."""
    if exit_state is None:
        return ""
    lines: list[str] = []
    scene = getattr(exit_state, "scene_location", "")
    time_of_day = getattr(exit_state, "time_of_day", "")
    mood = getattr(exit_state, "mood", "")
    carry = getattr(exit_state, "must_carry_forward", [])
    if scene:
        lines.append(f"场景: {scene}")
    if time_of_day:
        lines.append(f"时间: {time_of_day}")
    if mood:
        lines.append(f"氛围: {mood}")
    if carry:
        lines.append(f"必须延续: {format_list_or_default(carry, '；')}")
    return "\n".join(lines) if lines else ""
