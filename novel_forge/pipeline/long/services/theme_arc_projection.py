"""Lightweight theme + character arc projection for chapter planning.

Derives per-chapter theme focus and arc milestone context from existing schemas
(StoryBible.themes, NarrativeBlueprint.character_arcs/narrative_phases) and
injects them as prompt context. NO schema changes, NO narrative_state writes.

This is a prompt-injection + alignment soft-check only.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def derive_theme_focus(
    themes: list[str],
    *,
    chapter_number: int,
    total_chapters: int = 0,
    narrative_phases: list[Any] | None = None,
) -> dict[str, Any]:
    """Derive this chapter's theme duty from existing flat theme list + position.

    Parameters
    ----------
    themes:
        ``StoryBible.themes`` — flat list of theme strings.
    chapter_number:
        Current chapter being planned.
    total_chapters:
        Total planned chapters (for position-based weighting).
    narrative_phases:
        ``NarrativeBlueprint.narrative_phases`` — list of NarrativePhase objects.

    Returns
    -------
    dict with keys:
        - ``primary_theme``: the theme most relevant to this chapter position
        - ``theme_list``: all themes (pass-through)
        - ``phase_context``: current phase name/description if available
        - ``position_label``: "early" / "mid" / "late" based on chapter position
    """
    if not themes:
        return {
            "primary_theme": "",
            "theme_list": [],
            "phase_context": "",
            "position_label": "",
        }

    # Determine position label
    position_label = _compute_position_label(chapter_number, total_chapters)

    # Find current narrative phase
    phase_context = ""
    current_phase = _find_current_phase(narrative_phases, chapter_number)
    if current_phase is not None:
        phase_name = str(getattr(current_phase, "phase_name", "") or "").strip()
        phase_desc = str(getattr(current_phase, "description", "") or "").strip()
        if phase_name:
            phase_context = phase_name
            if phase_desc:
                phase_context = f"{phase_name}: {phase_desc}"

    # Select primary theme based on position
    # Simple rotation: different themes take focus at different story positions
    primary_idx = 0
    if total_chapters > 0 and len(themes) > 1:
        # Rotate through themes based on chapter position
        progress = chapter_number / max(total_chapters, 1)
        if progress < 0.33:
            primary_idx = 0
        elif progress < 0.66:
            primary_idx = min(1, len(themes) - 1)
        else:
            primary_idx = min(2, len(themes) - 1) if len(themes) > 2 else len(themes) - 1

    return {
        "primary_theme": themes[primary_idx] if themes else "",
        "theme_list": list(themes),
        "phase_context": phase_context,
        "position_label": position_label,
    }


def select_arc_milestones(
    character_arcs: list[Any],
    chapter_number: int,
) -> list[dict[str, Any]]:
    """Select ArcMilestone entries active for this chapter.

    Parameters
    ----------
    character_arcs:
        ``NarrativeBlueprint.character_arcs`` — list of CharacterArcPlan objects.
    chapter_number:
        Current chapter number.

    Returns
    -------
    List of dicts with keys: character, arc_summary, milestone_description, chapter_range.
    """
    active: list[dict[str, Any]] = []
    if not character_arcs or chapter_number <= 0:
        return active

    for arc in character_arcs:
        character = str(getattr(arc, "character", "") or "").strip()
        arc_summary = str(getattr(arc, "arc_summary", "") or "").strip()
        milestones = list(getattr(arc, "milestones", []) or [])

        for milestone in milestones:
            ch_start = int(getattr(milestone, "chapter_start", 0) or 0)
            ch_end = int(getattr(milestone, "chapter_end", 0) or 0)
            if ch_start <= 0 and ch_end <= 0:
                continue
            if ch_start <= chapter_number <= ch_end:
                desc = str(getattr(milestone, "description", "") or "").strip()
                active.append(
                    {
                        "character": character,
                        "arc_summary": arc_summary,
                        "milestone_description": desc,
                        "chapter_range": (ch_start, ch_end),
                    }
                )

    return active


def build_theme_arc_context(
    *,
    themes: list[str],
    character_arcs: list[Any],
    chapter_number: int,
    total_chapters: int = 0,
    narrative_phases: list[Any] | None = None,
) -> dict[str, Any]:
    """Build combined theme + arc context for chapter planning prompt injection.

    Parameters
    ----------
    themes:
        ``StoryBible.themes``.
    character_arcs:
        ``NarrativeBlueprint.character_arcs``.
    chapter_number:
        Current chapter.
    total_chapters:
        Total planned chapters.
    narrative_phases:
        ``NarrativeBlueprint.narrative_phases``.

    Returns
    -------
    dict with keys:
        - ``theme_focus``: result of derive_theme_focus()
        - ``active_arc_milestones``: result of select_arc_milestones()
        - ``injection_text``: formatted text block ready for prompt injection
    """
    theme_focus = derive_theme_focus(
        themes,
        chapter_number=chapter_number,
        total_chapters=total_chapters,
        narrative_phases=narrative_phases,
    )
    arc_milestones = select_arc_milestones(character_arcs, chapter_number)

    injection_text = _format_injection_text(theme_focus, arc_milestones)

    return {
        "theme_focus": theme_focus,
        "active_arc_milestones": arc_milestones,
        "injection_text": injection_text,
    }


def format_theme_arc_alignment_question(
    theme_arc_context: dict[str, Any] | None,
) -> str:
    """Build a soft alignment question for the alignment audit.

    Returns a question string that asks whether the chapter executed
    its theme/arc duties. Warning only — no hard gate.
    """
    if not theme_arc_context:
        return ""

    theme_focus = theme_arc_context.get("theme_focus", {})
    primary_theme = theme_focus.get("primary_theme", "")
    milestones = theme_arc_context.get("active_arc_milestones", [])

    parts: list[str] = []
    if primary_theme:
        parts.append(f"本章主题职责：{primary_theme}")
    if milestones:
        for m in milestones[:3]:
            char = m.get("character", "")
            desc = m.get("milestone_description", "")
            if char and desc:
                parts.append(f"角色弧光 [{char}]：{desc}")

    if not parts:
        return ""

    duty_text = "；".join(parts)
    return f"本章是否执行了以下主题/弧光职责？({duty_text})"


# ────────────────────────────────────────────────────────────────────────────
# Private helpers
# ────────────────────────────────────────────────────────────────────────────


def _compute_position_label(chapter_number: int, total_chapters: int) -> str:
    """Classify chapter position as 'early' / 'mid' / 'late'."""
    if total_chapters <= 0:
        return ""
    progress = chapter_number / max(total_chapters, 1)
    if progress < 0.33:
        return "early"
    elif progress < 0.66:
        return "mid"
    else:
        return "late"


def _find_current_phase(
    narrative_phases: list[Any] | None,
    chapter_number: int,
) -> Any | None:
    """Find the narrative phase that contains the current chapter."""
    if not narrative_phases:
        return None
    for phase in narrative_phases:
        ch_start = int(getattr(phase, "chapter_start", 0) or 0)
        ch_end = int(getattr(phase, "chapter_end", 0) or 0)
        if ch_start <= chapter_number <= ch_end:
            return phase
    return None


def _format_injection_text(
    theme_focus: dict[str, Any],
    arc_milestones: list[dict[str, Any]],
) -> str:
    """Format theme + arc context as a text block for prompt injection."""
    lines: list[str] = []

    # Theme section
    primary = theme_focus.get("primary_theme", "")
    all_themes = theme_focus.get("theme_list", [])
    phase = theme_focus.get("phase_context", "")
    position = theme_focus.get("position_label", "")

    if primary or phase:
        lines.append("【本章主题职责】")
        if position:
            lines.append(f"  位置：{position}")
        if phase:
            lines.append(f"  当前阶段：{phase}")
        if primary:
            lines.append(f"  主要主题：{primary}")
        if all_themes and len(all_themes) > 1:
            other = [t for t in all_themes if t != primary]
            if other:
                lines.append(f"  次要主题：{'、'.join(other[:3])}")

    # Arc milestones section
    if arc_milestones:
        lines.append("【活跃角色弧光里程碑】")
        for m in arc_milestones[:5]:
            char = m.get("character", "")
            desc = m.get("milestone_description", "")
            ch_range = m.get("chapter_range", (0, 0))
            range_str = f"(第{ch_range[0]}-{ch_range[1]}章)" if ch_range[0] > 0 else ""
            if char and desc:
                lines.append(f"  {char} {range_str}：{desc}")

    return "\n".join(lines) if lines else ""
