"""Sub-module of novel_forge.desktop.pages.document_renderer.reports.

Migration P2-step: ``render_generic_report`` (any JSON with scan-friendly
generic structure) and its 9 private ``_generic_*`` helpers live here.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.reports import (
    _GENERIC_META_KEYS,
)
from novel_forge.desktop.pages.document_renderer.reports._common import (
    generic_key_label,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

__all__ = [
    "render_generic_report",
    "render_generic_report_body_html",
]


def _generic_is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _generic_is_scalar(value: Any) -> bool:
    return isinstance(value, (str, bool, int, float)) or value is None


def _generic_scalar_html(value: Any) -> str:
    if value is None or value == "":
        return '<span class="kv-value" style="color:[[nf:text.muted]];">—</span>'
    if isinstance(value, bool):
        icon = "✓" if value else "✗"
        color = "[[nf:status.success.warm]]" if value else "[[nf:status.danger.deep]]"
        return f'<span style="color:{color}; font-weight:700;">{icon}</span>'
    if isinstance(value, (int, float)):
        return f'<span class="kv-value" style="font-weight:700;">{_esc(str(value))}</span>'
    return f'<span class="kv-value">{_nl2br(str(value))}</span>'


def _generic_item_title(item: dict[str, Any], fallback: str) -> str:
    for key in (
        "title",
        "name",
        "summary",
        "description",
        "issue_type",
        "candidate_id",
        "state_path",
        "chapter_number",
    ):
        value = item.get(key)
        if value not in (None, "", []):
            text = str(value).strip()
            if key == "chapter_number":
                return f"第 {text} 章"
            if text:
                return text
    return fallback


def _generic_metric(label: str, value: Any) -> str:
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{_esc(label)}</div>'
        f'<div class="metric-value">{_esc(str(value))}</div>'
        "</div>"
    )


def _generic_tag(text: Any, *, tone: str = "muted") -> str:
    cls = "tag" if tone == "accent" else "tag tag-muted"
    return f'<span class="{cls}">{_esc(str(text))}</span>'


def _generic_list_items(items: list[Any], *, limit: int = 8) -> str:
    rows: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            text = (
                item.get("summary")
                or item.get("description")
                or item.get("value")
                or item.get("reason")
                or str(item)
            )
        else:
            text = item
        if str(text).strip():
            rows.append(f"<li>{_nl2br(str(text))}</li>")
    if len(items) > limit:
        rows.append(f"<li>另有 {len(items) - limit} 项未展开。</li>")
    return f'<ul class="compact-list">{"".join(rows)}</ul>' if rows else ""


def _generic_render_value(value: Any, *, depth: int = 0, limit: int = 8) -> str:
    if _generic_is_scalar(value):
        return _generic_scalar_html(value)
    if isinstance(value, list):
        if not value:
            return '<span class="kv-value" style="color:[[nf:text.muted]];">（空）</span>'
        if all(_generic_is_scalar(item) for item in value):
            return _generic_list_items(value, limit=limit)
        cards: list[str] = []
        for index, item in enumerate(value[:limit], 1):
            if isinstance(item, dict):
                title = _generic_item_title(item, f"条目 {index}")
                cards.append(
                    '<div class="mini-card">'
                    f"<h3>{_esc(title)}</h3>"
                    f"{_generic_render_dict(item, depth=depth + 1, compact=True)}"
                    "</div>"
                )
            else:
                cards.append(f'<div class="rule-item">{_nl2br(str(item))}</div>')
        if len(value) > limit:
            cards.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(value) - limit} 项未展开。"
                "</div>"
            )
        return "\n".join(cards)
    if isinstance(value, dict):
        return _generic_render_dict(value, depth=depth + 1, compact=True)
    return f'<span class="kv-value">{_esc(str(value))}</span>'


def _generic_render_dict(
    payload: dict[str, Any],
    *,
    depth: int = 0,
    compact: bool = False,
    skip_empty: bool = True,
) -> str:
    scalar_rows: list[str] = []
    complex_rows: list[str] = []
    for key, value in payload.items():
        if key in _GENERIC_META_KEYS:
            continue
        if skip_empty and _generic_is_empty(value):
            continue
        label = _esc(generic_key_label(str(key)))
        if _generic_is_scalar(value) or (
            isinstance(value, list) and all(_generic_is_scalar(item) for item in value)
        ):
            scalar_rows.append(
                f"<tr><th>{label}</th><td>{_generic_render_value(value, depth=depth)}</td></tr>"
            )
            continue
        rendered = _generic_render_value(value, depth=depth)
        if compact:
            complex_rows.append(
                f'<div class="kv-row"><span class="kv-label">{label}</span>{rendered}</div>'
            )
        else:
            complex_rows.append(f'<div class="section"><h2>{label}</h2>{rendered}</div>')
    parts: list[str] = []
    if scalar_rows:
        table = f"<table><tbody>{''.join(scalar_rows)}</tbody></table>"
        parts.append(table if compact else f'<div class="section">{table}</div>')
    parts.extend(complex_rows)
    return "\n".join(parts)


def render_generic_report_body_html(data: dict[str, Any]) -> str:
    """Render an arbitrary JSON report as a scan-friendly HTML body."""

    return _generic_render_dict(data)


def render_generic_report(data: dict[str, Any], title: str = "报告") -> QTextBrowser:
    """Render an arbitrary JSON report in the shared document browser."""

    return _make_browser(_html_wrap(render_generic_report_body_html(data), title))


_INIT_STAGE_LABELS: dict[str, str] = {
    "blueprint_coherence": "蓝图一致性",
    "outline_inheritance": "大纲继承",
    "contract_coherence": "契约一致性",
    "claim_contract_coverage": "契约覆盖",
}


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _severity_tone(value: Any) -> str:
    raw = str(value or "").lower()
    return "accent" if raw in {"high", "critical", "blocked", "fail", "failed"} else "muted"
