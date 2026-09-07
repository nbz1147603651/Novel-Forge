from __future__ import annotations

from typing import Any

from novel_forge.core.utils.field_extractor import field

PREVIOUS_FINAL_MARKER = "[extend_outline:previous_final]"


def build_chapter_position(
    outline: Any,
    chapter_number: int,
    *,
    previous_total: int | None = None,
) -> dict[str, Any]:
    """Return stable whole-book position semantics for one chapter."""

    number = max(1, int(chapter_number or 1))
    total = _resolve_total_chapters(outline, number)
    remaining = max(0, total - number)
    label = _position_label(number, total)
    previously_final = bool(previous_total and number == int(previous_total) and total > previous_total)
    previously_final = previously_final or _chapter_notes_have_previous_final_marker(
        outline,
        number,
    )
    return {
        "chapter_number": number,
        "total_chapters": total,
        "is_last_chapter": number == total,
        "is_previously_final_chapter": previously_final,
        "remaining_chapters": remaining,
        "position_label": label,
    }


def _resolve_total_chapters(outline: Any, chapter_number: int) -> int:
    total = int(field(outline, "total_chapters", 0) or 0)
    chapters = field(outline, "chapters", []) or []
    chapter_numbers: list[int] = []
    for chapter in chapters:
        number = int(field(chapter, "chapter_number", 0) or 0)
        if number > 0:
            chapter_numbers.append(number)
    if chapter_numbers:
        total = max(total, max(chapter_numbers))
    return max(total, chapter_number)


def _position_label(chapter_number: int, total_chapters: int) -> str:
    if chapter_number >= total_chapters:
        return "final"
    if total_chapters - chapter_number == 1:
        return "penultimate"
    if chapter_number == 1:
        return "opening"
    return "middle"


def _chapter_notes_have_previous_final_marker(outline: Any, chapter_number: int) -> bool:
    chapters = field(outline, "chapters", []) or []
    for chapter in chapters:
        if int(field(chapter, "chapter_number", 0) or 0) != chapter_number:
            continue
        notes = str(field(chapter, "notes", "") or "")
        return PREVIOUS_FINAL_MARKER in notes
    return False


__all__ = ["PREVIOUS_FINAL_MARKER", "build_chapter_position"]
