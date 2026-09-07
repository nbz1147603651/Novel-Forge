"""Internal helpers shared by every story_artifacts/ render submodule.

The ``blueprint_element``, ``coherence``, ``outline`` and ``version_diff``
submodules each previously duplicated the same five private render helpers.
They have been consolidated here so the submodules now contain only their
own domain-specific render functions plus a one-line import of the shared
helpers.
"""

from __future__ import annotations

import re
from typing import Any

from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc

JsonDict = dict[str, Any]

__all__ = (
    "_format_age_display",
    "_revision_status_label",
    "_humanize_reason_label",
    "_safe_float_display",
    "_render_revision_diagnostics",
)


def _format_age_display(age: object) -> str:
    """Normalize age text for HTML snippets (avoid duplicated '岁')."""
    raw = str(age or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"\s+", "", raw)
    m = re.fullmatch(r"(\d+)(?:岁|歲)?", compact)
    if m:
        return f"{m.group(1)}岁"
    return raw


def _revision_status_label(status: Any) -> tuple[str, str]:
    raw = str(status or "").strip().lower()
    if raw == "accepted":
        return "已采纳", "[[nf:status.success.warm]]"
    if raw == "rejected":
        return "已回滚", "[[nf:status.danger.deep]]"
    if raw == "no_change":
        return "无改动", "[[nf:text.muted]]"
    return raw or "未知状态", "[[nf:text.muted]]"


def _humanize_reason_label(reason: Any) -> str:
    labels = {
        "": "无",
        "no_patches_applied": "未应用补丁",
        "change_ratio_exceeded": "变更率超过上限",
        "semantic_drift": "漂移保护触发",
    }
    raw = str(reason or "").strip()
    return labels.get(raw, raw or "无")


def _safe_float_display(value: Any, *, percent: bool = False) -> str:
    if isinstance(value, bool):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if percent:
        return f"{number * 100:.1f}%"
    return f"{number:.4f}"


def _render_revision_diagnostics(diff_result: JsonDict) -> str:
    from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc

    status = diff_result.get("status")
    reason = diff_result.get("reason")
    patches_applied = diff_result.get("patches_applied")
    change_ratio = diff_result.get("change_ratio")
    metadata = diff_result.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    if all(
        value in (None, "", {})
        for value in (status, reason, patches_applied, change_ratio, metadata)
    ):
        return ""

    status_text, status_color = _revision_status_label(status)
    change_cap = metadata.get("change_ratio_cap")
    drift = metadata.get("drift")
    drift = drift if isinstance(drift, dict) else {}

    tags = [
        f'<span class="tag" style="color:{status_color}; border-color:{status_color}; '
        f'background:rgba([[nf:white]], 0.45);">{_esc(status_text)}</span>',
        f'<span class="tag tag-muted">原因：{_esc(_humanize_reason_label(reason))}</span>',
        f'<span class="tag tag-muted">补丁：{_esc(str(patches_applied or 0))}</span>',
        f'<span class="tag tag-muted">变更率：{_esc(_safe_float_display(change_ratio, percent=True))}</span>',
    ]
    if change_cap not in (None, ""):
        tags.append(
            f'<span class="tag tag-muted">上限：'
            f"{_esc(_safe_float_display(change_cap, percent=True))}</span>"
        )
    if drift:
        tags.append(
            f'<span class="tag tag-muted">漂移信号：{_esc(str(drift.get("signal_count", 0)))}</span>'
        )
        tags.append(
            f'<span class="tag tag-muted">高风险：{_esc(str(drift.get("high_severity", 0)))}</span>'
        )

    details: list[str] = []
    if drift and isinstance(drift.get("signals"), list):
        signal_rows: list[str] = []
        for signal in drift["signals"][:3]:
            if not isinstance(signal, dict):
                continue
            severity = str(signal.get("severity") or "")
            category = str(signal.get("category") or "")
            description = str(signal.get("description") or "")
            signal_rows.append(f"<li>{_esc(category)} / {_esc(severity)}：{_esc(description)}</li>")
        if signal_rows:
            details.append(
                '<ul class="compact-list" style="margin-top:8px;">' + "".join(signal_rows) + "</ul>"
            )

    return (
        '<div class="section" style="padding:12px 14px;">'
        '<div style="margin-bottom:6px;">' + "".join(tags) + "</div>" + "".join(details) + "</div>"
    )


def _fragment_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def _fragment_chapter_span(raw: dict[str, Any]) -> str:
    start = raw.get("start_chapter", raw.get("chapter_start", raw.get("chapter", "")))
    end = raw.get("end_chapter", raw.get("chapter_end", raw.get("chapter_number", "")))
    if not start and raw.get("chapter_number"):
        start = raw.get("chapter_number")
    if start and end and str(start) != str(end):
        return f"Ch.{start}-{end}"
    if start or end:
        return f"Ch.{start or end}"
    chapters = raw.get("involved_chapters")
    if isinstance(chapters, list) and chapters:
        return "Ch." + "、".join(str(chapter) for chapter in chapters[:8])
    return "—"


def _fragment_badge(text: Any, *, muted: bool = True) -> str:
    cls = "tag-muted tag" if muted else "tag"
    return f'<span class="{cls}">{_esc(str(text))}</span>'


def _fragment_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _fragment_list(value: Any, *, limit: int = 8) -> list[str]:
    """Project an arbitrary value into a list of compact text fragments.

    Migrated from ``__init__.py`` after the surrounding render_* functions
    relocated to dedicated submodules; the helper is shared by
    ``narrative_blueprint`` / future cluster-D renderers.
    """
    from novel_forge.desktop.pages.document_renderer_reports import generic_key_label

    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value[:limit]:
        if isinstance(raw, dict):
            text = (
                raw.get("event")
                or raw.get("description")
                or raw.get("goal")
                or raw.get("target")
                or raw.get("summary")
                or raw.get("title")
                or raw.get("name")
                or ""
            )
            if not text:
                text = "；".join(
                    f"{generic_key_label(str(key))}：{_fragment_text(val)}"
                    for key, val in raw.items()
                    if val not in (None, "", [], {})
                )
        else:
            text = raw
        cleaned = _fragment_text(text)
        if cleaned:
            items.append(cleaned)
    if isinstance(value, list) and len(value) > limit:
        items.append(f"另有 {len(value) - limit} 项")
    return items


def _fragment_join(value: Any, *, limit: int = 8, empty: str = "—") -> str:
    items = _fragment_list(value, limit=limit)
    return "；".join(items) if items else empty


def _fragment_count_row(label: str, value: Any) -> str:
    """Compact 2-cell row for narrative/edit metadata counts.

    Migrated here from the parent ``__init__.py``; shared by ``editorial``,
    ``narrative_blueprint`` and the reading-power window renderer.
    """
    count = len(value) if isinstance(value, list) else int(bool(value))
    return f"<td>{_esc(label)}</td><td><b>{count}</b>"


def _fragment_dict_count(value: Any) -> int:
    return len(value) if isinstance(value, dict) else 0
