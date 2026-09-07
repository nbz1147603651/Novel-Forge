"""Helpers for rendering motif continuity data into prompt-safe strings."""

from __future__ import annotations

from typing import Any

from novel_forge.core.domain.guardrails import sanitize_story_text


def _clean_text(value: Any) -> str:
    return sanitize_story_text(str(value or "").strip())


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _format_forbidden_repetition(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            text = _clean_text(raw.get("name") or raw.get("motif"))
        else:
            text = _clean_text(raw)
        if text:
            items.append(text)
    return _dedupe(items)


def _format_suggested_callbacks(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            motif = _clean_text(raw.get("motif") or raw.get("name"))
            reason = _clean_text(raw.get("reason"))
            priority = _clean_text(raw.get("priority"))
            if motif and reason:
                text = f"{motif}：{reason}"
            elif motif and priority:
                text = f"{motif}（{priority}）"
            else:
                text = motif or reason
        else:
            text = _clean_text(raw)
        if text:
            items.append(text)
    return _dedupe(items)


def _format_active_motifs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            name = _clean_text(raw.get("name") or raw.get("motif"))
            category = _clean_text(raw.get("category"))
            text = f"{name}（{category}）" if name and category else name
        else:
            text = _clean_text(raw)
        if text:
            items.append(text)
    return _dedupe(items)


def format_motif_continuity_for_prompt(raw: Any) -> dict[str, list[str]]:
    """Convert structured motif continuity payloads into readable prompt strings."""
    if not isinstance(raw, dict) or not raw:
        return {}

    formatted = {
        "forbidden_repetition": _format_forbidden_repetition(
            raw.get("forbidden_repetition", [])
        ),
        "suggested_callbacks": _format_suggested_callbacks(
            raw.get("suggested_callbacks", [])
        ),
        "active_motifs": _format_active_motifs(raw.get("active_motifs", [])),
    }
    return {key: value for key, value in formatted.items() if value}
