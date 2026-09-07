"""Reusable text revision comparison payloads."""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Mapping
from typing import Any

from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.core.utils.version_diff import compute_diff


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _diff_hunks_for_renderer(
    original_text: str,
    revised_text: str,
    *,
    context_lines: int = 1,
    max_hunks: int = 80,
) -> list[dict[str, Any]]:
    original_lines = original_text.splitlines()
    revised_lines = revised_text.splitlines()
    matcher = difflib.SequenceMatcher(None, original_lines, revised_lines)
    hunks: list[dict[str, Any]] = []

    for group in matcher.get_grouped_opcodes(n=context_lines):
        for tag, old_start, old_end, new_start, new_end in group:
            old_text = "\n".join(original_lines[old_start:old_end]).strip()
            new_text = "\n".join(revised_lines[new_start:new_end]).strip()
            if not old_text and not new_text:
                continue
            hunks.append(
                {
                    "tag": tag,
                    "old_start": old_start + 1,
                    "old_end": old_end,
                    "new_start": new_start + 1,
                    "new_end": new_end,
                    "a_text": old_text,
                    "b_text": new_text,
                }
            )
            if len(hunks) >= max_hunks:
                return hunks
    return hunks


def build_text_revision_diff(
    original_text: str,
    revised_text: str,
    *,
    source: str,
    chapter_number: int | None = None,
    label_before: str = "原文",
    label_after: str = "修订后",
    status: str = "accepted",
    reason: str = "",
    patches_applied: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable comparison artifact for any text rewrite stage."""

    original = str(original_text or "")
    revised = str(revised_text or "")
    diff = compute_diff(
        original,
        revised,
        label_a=label_before,
        label_b=label_after,
    )
    before_words = display_word_count(original)
    after_words = display_word_count(revised)
    return {
        "artifact_type": "text_revision_diff",
        "source": str(source or "unknown"),
        "chapter_number": chapter_number,
        "label_before": label_before,
        "label_after": label_after,
        "status": str(status or "accepted"),
        "reason": str(reason or ""),
        "patches_applied": int(patches_applied or 0),
        "original_hash": _text_hash(original),
        "revised_hash": _text_hash(revised),
        "word_count_before": before_words,
        "word_count_after": after_words,
        "word_count_delta": after_words - before_words,
        "additions": diff.total_additions,
        "deletions": diff.total_deletions,
        "total_changes": diff.total_changes,
        "similarity_ratio": diff.similarity_ratio,
        "change_ratio": max(0.0, 1.0 - diff.similarity_ratio),
        "hunks": _diff_hunks_for_renderer(original, revised),
        "unified_diff": diff.unified_diff,
        "metadata": dict(metadata or {}),
    }
