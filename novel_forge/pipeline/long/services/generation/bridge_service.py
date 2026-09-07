"""Opening-echo detection and deduplication between consecutive chapters."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.constants import PipelineConstants

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_overlap_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", "", text)


def is_opening_echo(current_block: str, reference_block: str) -> bool:
    current_norm = normalize_overlap_text(current_block)
    reference_norm = normalize_overlap_text(reference_block)
    if len(current_norm) < PipelineConstants.OPENING_ECHO_MIN_LENGTH or len(reference_norm) < PipelineConstants.OPENING_ECHO_MIN_LENGTH:
        return False
    if current_norm == reference_norm:
        return True
    shorter, longer = (
        (current_norm, reference_norm)
        if len(current_norm) <= len(reference_norm)
        else (reference_norm, current_norm)
    )
    if not longer.startswith(shorter):
        return False
    overlap_ratio = len(shorter) / max(1, len(longer))
    return overlap_ratio >= PipelineConstants.OPENING_ECHO_OVERLAP_RATIO


def remove_opening_echo(
    chapter_text: str,
    previous_chapter_ending: str,
) -> tuple[str, dict[str, Any] | None]:
    """Remove duplicated opening paragraphs that echo the previous chapter ending.

    Returns the (possibly cleaned) text and an optional report dict.
    """
    text = str(chapter_text or "")
    previous = str(previous_chapter_ending or "")
    if not text.strip() or not previous.strip():
        return text, None

    current_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    previous_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", previous) if p.strip()]
    if not current_paragraphs or not previous_paragraphs:
        return text, None

    candidates: list[tuple[str, str]] = []
    last_para = previous_paragraphs[-1]
    candidates.append(("last_paragraph", last_para))
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])", last_para)
        if part.strip()
    ]
    if sentences:
        candidates.append(("last_sentence", sentences[-1]))

    drop_paragraphs = 0
    matched_kind = ""
    first_para = current_paragraphs[0]

    for kind, candidate in candidates:
        if is_opening_echo(first_para, candidate):
            drop_paragraphs = 1
            matched_kind = kind
            break

    if drop_paragraphs <= 0 or len(current_paragraphs) <= drop_paragraphs:
        return text, None

    removed_preview = "\n\n".join(current_paragraphs[:drop_paragraphs])
    if len(removed_preview) > PipelineConstants.SAMPLE_PREVIEW_LENGTH:
        removed_preview = removed_preview[: PipelineConstants.SAMPLE_PREVIEW_LENGTH - 1] + "…"

    cleaned_text = "\n\n".join(current_paragraphs[drop_paragraphs:]).strip()
    if not cleaned_text:
        return text, None

    return cleaned_text, {
        "removed_paragraphs": drop_paragraphs,
        "matched_reference": matched_kind,
        "removed_preview": removed_preview,
    }
