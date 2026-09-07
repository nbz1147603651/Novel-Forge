"""Sub-module of novel_forge.desktop.pages.document_renderer.reports.

Migration P2-step: ``render_eval_report`` and its body variant live here,
along with the eval-time helpers (``_safe_float``, ``_safe_int``,
``_eval_dim_label``, ``_truthy_report_flag``,
``_creative_character_status``) and ``generic_key_label`` re-export.
``_BIBLE_FIELD_LABELS`` etc. are reused from :mod:`._common`.
"""

from __future__ import annotations

from typing import Any

from novel_forge.desktop.pages.document_renderer.reports._common import (
    _creative_character_status,
    _eval_dim_label,
    _safe_float,
    _safe_int,
    _truthy_report_flag,
)
from novel_forge.desktop.pages.document_renderer.reports.evaluation import (
    render_eval_report,
    render_eval_report_body_html,
)
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc

__all__ = [
    "render_eval_report",
    "render_eval_report_body_html",
    "_safe_float",
    "_safe_int",
    "_eval_dim_label",
    "_truthy_report_flag",
    "_creative_character_status",
]

_HUMANIZE_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

_HUMANIZE_SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}


def _humanize_severity_tone(severity: Any) -> str:
    raw = str(severity or "").strip().lower()
    return "accent" if raw in {"critical", "high"} else "muted"


def _humanize_score_label(score: float | None) -> str:
    if score is None:
        return "未评分"
    if score >= 8.5:
        return "自然"
    if score >= 7.0:
        return "轻微痕迹"
    if score >= 5.5:
        return "需要关注"
    return "AI 痕迹较重"


def _humanize_bool_label(value: Any) -> str:
    return "可自动修复" if bool(value) else "仅报告"


def _humanize_metric(label: str, value: Any, *, color: str | None = None) -> str:
    style = f' style="color:{color};"' if color else ""
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{_esc(label)}</div>'
        f'<div class="metric-value"{style}>{_esc(str(value))}</div>'
        "</div>"
    )


def _humanize_category_distribution(hits_by_category: dict[str, Any]) -> str:
    rows: list[str] = []
    normalized: list[tuple[str, int]] = []
    for category, raw_count in hits_by_category.items():
        count = _safe_int(raw_count, 0)
        if count > 0:
            normalized.append((str(category), count))
    if not normalized:
        return '<div class="hint-block">暂无分类命中。</div>'

    total = max(1, sum(count for _category, count in normalized))
    for category, count in sorted(normalized, key=lambda item: (-item[1], item[0])):
        width = max(4, min(100, int(round(count / total * 100))))
        rows.append(
            '<div style="margin:8px 0;">'
            '<div style="display:flex; justify-content:space-between; gap:8px; '
            'font-size: 12pt; color:[[nf:text.body.alt]];">'
            f"<strong>{_esc(category)}</strong><span>{count}</span></div>"
            '<div class="score-bar-bg" style="height:7px; margin-top:4px;">'
            f'<div class="score-bar-fill" style="width:{width}%; background:[[nf:accent.primary]];"></div>'
            "</div></div>"
        )
    return '<div class="section" style="padding:10px 12px;">' + "".join(rows) + "</div>"
