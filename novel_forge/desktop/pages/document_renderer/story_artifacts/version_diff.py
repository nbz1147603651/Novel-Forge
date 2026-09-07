"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Auto-generated in the M3.3 split. Contains version_diff.py renderers.
"""

from __future__ import annotations

import difflib
from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _render_revision_diagnostics,
)
from novel_forge.desktop.pages.standalone.renderer_html import base_css as _base_css
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import score_color as _score_color

JsonDict = dict[str, Any]

__all__ = ["render_version_diff"]

_DIFF_CSS = """
.diff-hunk { margin: 8px 0; font-family: "SF Mono", "Menlo", "Courier New"; font-size: 12pt; }
.diff-add { background: rgba([[nf:status.success]], 0.12); color: [[nf:status.success]]; padding: 1px 4px; }
.diff-del { background: rgba([[nf:status.danger.critical]], 0.12); color: [[nf:danger.red]]; text-decoration: line-through; padding: 1px 4px; }
.diff-ctx { color: [[nf:slate]]; padding: 1px 4px; }
.diff-stats { display: inline-block; margin: 2px 6px; font-size: 11pt; }
.diff-inline { background: [[nf:white]]; border: 1px solid [[nf:bg.control]]; border-radius: 6px; padding: 12px 13px; line-height: 1.75; margin: 8px 0; }
"""


def _inline_diff_html(old: str, new: str) -> str:
    sm = difflib.SequenceMatcher(None, str(old), str(new))
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(_esc(str(old)[i1:i2]))
        elif tag == "delete":
            parts.append(
                f'<span style="background:[[nf:bg.hover.accent]];color:[[nf:accent.dark]];'
                f'text-decoration:line-through;">{_esc(str(old)[i1:i2])}</span>'
            )
        elif tag == "insert":
            parts.append(
                f'<span style="background:[[nf:status.success.bg]];'
                f'color:[[nf:status.success.warm]];font-weight:600;">{_esc(str(new)[j1:j2])}</span>'
            )
        elif tag == "replace":
            parts.append(
                f'<span style="background:[[nf:bg.hover.accent]];color:[[nf:accent.dark]];'
                f'text-decoration:line-through;">{_esc(str(old)[i1:i2])}</span>'
            )
            parts.append(
                f'<span style="background:[[nf:status.success.bg]];'
                f'color:[[nf:status.success.warm]];font-weight:600;">{_esc(str(new)[j1:j2])}</span>'
            )
    return "".join(parts)


def render_version_diff(
    diff_result: JsonDict,
    label_a: str = "版本 A",
    label_b: str = "版本 B",
) -> QTextBrowser:
    """Render a VersionDiffResult dict as formatted HTML diff view."""
    additions = diff_result.get("additions", 0)
    deletions = diff_result.get("deletions", 0)
    similarity = diff_result.get("similarity_ratio", 0.0)
    hunks = diff_result.get("hunks", [])
    unified = diff_result.get("unified_diff", "")

    parts: list[str] = []
    diagnostics = _render_revision_diagnostics(diff_result)
    if diagnostics:
        parts.append(diagnostics)

    similarity_pct = f"{similarity * 100:.1f}%"
    similarity_color = _score_color(similarity * 10)
    parts.append(
        f'<div class="section" style="text-align:center; padding:16px;">'
        f'<div style="font-size: 28pt; font-weight:700; color:{similarity_color};">'
        f"{similarity_pct}</div>"
        f'<div style="color:[[nf:text.muted]]; font-size: 12pt;">相似度</div>'
        f'<div style="margin-top:8px;">'
        f'<span class="diff-stats" style="color:[[nf:status.success]];">+{additions}</span>'
        f'<span class="diff-stats" style="color:[[nf:danger.red]];">-{deletions}</span>'
        f"</div>"
        f'<div style="color:[[nf:text.muted]]; font-size: 11pt; margin-top:4px;">'
        f"{_esc(label_a)} → {_esc(label_b)}</div>"
        f"</div>"
    )

    if hunks:
        parts.append("<h2>变更详情</h2>")
        for hunk in hunks:
            tag = hunk.get("tag", "equal")
            a_text = hunk.get("a_text", "")
            b_text = hunk.get("b_text", "")
            if tag == "equal":
                if a_text.strip():
                    preview = a_text[:120] + ("..." if len(a_text) > 120 else "")
                    parts.append(f'<div class="diff-ctx">{_esc(preview)}</div>')
            elif tag == "replace":
                inline = _inline_diff_html(a_text, b_text).replace("\n", "<br>")
                parts.append(f'<div class="diff-inline">{inline}</div>')
            elif tag == "delete":
                inline = _inline_diff_html(a_text, "")
                parts.append(f'<div class="diff-inline">{inline}</div>')
            elif tag == "insert":
                inline = _inline_diff_html("", b_text)
                parts.append(f'<div class="diff-inline">{inline}</div>')
    elif unified:
        parts.append("<h2>Unified Diff</h2>")
        parts.append(f'<pre style="font-size: 11pt; white-space:pre-wrap;">{_esc(unified)}</pre>')

    css = _base_css() + _DIFF_CSS
    html = (
        f"<html><head><style>{css}</style></head>"
        f"<body><h1>版本对比</h1>{''.join(parts)}</body></html>"
    )
    return _make_browser(html)
