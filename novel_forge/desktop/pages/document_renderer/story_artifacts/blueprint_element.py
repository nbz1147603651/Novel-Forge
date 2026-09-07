"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Auto-generated in the M3.3 split. Contains blueprint_element.py renderers.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = ["render_blueprint_element_selection"]


def render_blueprint_element_selection(data: JsonDict) -> QTextBrowser:
    """Render blueprint element selector output."""
    parts: list[str] = []
    mode = data.get("mode", "")
    version = data.get("library_version", "")
    summary = data.get("selector_summary", "")
    genres = data.get("genre_inference", [])
    constraints = data.get("focus_constraints", [])

    if mode or version:
        tags: list[str] = []
        if mode:
            tags.append(f"模式：{_esc(str(mode))}")
        if version:
            tags.append(f"库版本：{_esc(str(version))}")
        parts.append(
            '<div style="text-align:center; margin-bottom:10px;">'
            + "".join(
                f'<span class="tag-muted tag" style="margin:0 4px;">{tag}</span>' for tag in tags
            )
            + "</div>"
        )
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')
    if genres:
        parts.append(
            f'<div class="section"><h3>题材判断</h3><div class="kv-value">{_esc("、".join(str(v) for v in genres))}</div></div>'
        )
    if constraints:
        parts.append("<h2>执行约束</h2>")
        for item in constraints:
            parts.append(f'<div class="rule-item">· {_esc(str(item))}</div>')

    def _render_element_group(title: str, elements: Any) -> None:
        if not isinstance(elements, list) or not elements:
            return
        parts.append(f"<h2>{_esc(title)}</h2>")
        for raw in elements:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name", "")
            element_id = raw.get("element_id", "")
            category = raw.get("category", "")
            desc = raw.get("description", "")
            rationale = raw.get("rationale", "")
            reason = raw.get("selection_reason", "")
            genres = raw.get("recommended_genres", [])
            prompt_hint = raw.get("prompt_hint", "")
            ui_hint = raw.get("ui_hint", "")
            tags: list[str] = []
            if element_id:
                tags.append(_esc(str(element_id)))
            if category:
                tags.append(_esc(str(category)))
            if ui_hint:
                tags.append(f"UI:{_esc(str(ui_hint))}")
            genre_text = ""
            if isinstance(genres, list) and genres:
                genre_text = f'<div style="font-size: 11pt;color:[[nf:text.muted]];">推荐题材：{_esc("、".join(str(v) for v in genres))}</div>'
            reason_text = ""
            if reason:
                reason_text = f'<div style="font-size: 11pt;color:[[nf:accent.deep]];">选用原因：{_esc(str(reason))}</div>'
            rationale_text = ""
            if rationale:
                rationale_text = (
                    f'<div style="font-size: 11pt;color:[[nf:text.muted.strong]];">价值：{_esc(str(rationale))}</div>'
                )
            prompt_text = ""
            if prompt_hint:
                prompt_text = f'<div style="font-size: 11pt;color:[[nf:text.body.alt]];">提示词：{_esc(str(prompt_hint))}</div>'
            parts.append(
                '<div class="rule-item">'
                f"<b>{_esc(str(name))}</b>"
                + (
                    f'<span class="tag-muted tag" style="margin-left:8px;">{" | ".join(tags)}</span>'
                    if tags
                    else ""
                )
                + (f"<br>{_nl2br(str(desc))}" if desc else "")
                + rationale_text
                + reason_text
                + genre_text
                + prompt_text
                + "</div>"
            )

    _render_element_group("必要项", data.get("required_elements", []))
    _render_element_group("扩展项", data.get("extension_elements", []))

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无叙事要素选择数据</div>'
        )
    return _make_browser(_html_wrap("\n".join(parts), "叙事要素选择"))
