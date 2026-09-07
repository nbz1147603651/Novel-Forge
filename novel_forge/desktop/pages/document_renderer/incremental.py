"""Incremental document renderers using QTextCursor.insertHtml().

These functions insert HTML fragments into an existing QTextBrowser via
QTextCursor, avoiding the full DOM rebuild that setHtml() triggers.
They produce the same text content as the originals in
``document_renderer_reports.py``.

Usage::

    browser = QTextBrowser()
    browser.setStyleSheet(...)
    render_eval_report_incremental(browser, data)
    # browser now contains the rendered report
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextBrowser, QTextEdit

from novel_forge.desktop.pages.document_renderer.reports import (
    _BIBLE_FIELD_LABELS,
    _EVAL_DIM_LABELS,
    _TRANSITION_MODE_LABELS,
)
from novel_forge.desktop.pages.standalone.renderer_html import base_css, browser_stylesheet
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_bar_html as _score_bar_html
from novel_forge.desktop.theme.documents import render_document_markup


def _init_browser(browser: QTextBrowser) -> QTextCursor:
    browser.setObjectName("docViewerContent")
    browser.setOpenExternalLinks(False)
    browser.setStyleSheet(browser_stylesheet())
    cursor = QTextCursor(browser.document())
    cursor.movePosition(QTextCursor.MoveOperation.End)
    return cursor


def _insert_html(cursor: QTextCursor, html: str) -> None:
    cursor.insertHtml(render_document_markup(html))


def _insert_header(cursor: QTextCursor, title: str) -> None:
    _insert_html(cursor, f"<h1>{_esc(title)}</h1>")


def _inject_css(browser: QTextBrowser) -> None:
    cursor = QTextCursor(browser.document())
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertHtml(f"<style>{base_css()}</style>")


# ── render_eval_report_incremental ──────────────────────────────────


def render_eval_report_incremental(browser: QTextBrowser, data: dict[str, Any]) -> None:
    """Incrementally render an eval report into *browser*.

    Produces the same text content as ``render_eval_report()`` from
    ``document_renderer_reports.py``.
    """
    _inject_css(browser)
    cursor = _init_browser(browser)

    overall = data.get("overall_score")
    passed = data.get("passed")
    threshold = data.get("threshold", 6.0)
    summary = data.get("summary", "")
    scores = data.get("scores", [])

    _insert_header(cursor, "质量评估报告")

    # Score header
    if overall is not None:
        mark_color = "[[nf:status.success.warm]]" if passed else "[[nf:status.danger.deep]]"
        mark_text = "✓ 通过" if passed else "✗ 未通过"
        _insert_html(
            cursor,
            f'<div class="section" style="text-align:center; padding:20px;">'
            f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{mark_color};\">{overall:.1f}</div>"
            f'<div style="font-size: 14pt; color:{mark_color}; font-weight:600;">{mark_text}</div>'
            f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:4px;">阈值 {threshold:.1f}</div></div>',
        )

    # Scores
    if scores:
        _insert_html(cursor, "<h2>各维度评分</h2>")
        for item in scores:
            dim = item.get("dimension", "?")
            score = item.get("score", 0)
            comment = item.get("comment", "")
            score_val = float(score) if isinstance(score, (int, float)) else 0
            _insert_html(
                cursor,
                f'<div style="margin:10px 0;"><div style="display:flex; justify-content:space-between; margin-bottom:3px;">'
                f'<span class="kv-label">{_esc(_EVAL_DIM_LABELS.get(dim, dim) or dim)}</span></div>'
                f"{_score_bar_html(score_val)}",
            )
            if comment:
                _insert_html(
                    cursor,
                    f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; margin-top:3px; padding-left:2px;">{_esc(comment)}</div>',
                )
            _insert_html(cursor, "</div>")

    # Summary
    if summary:
        _insert_html(
            cursor,
            f'<h2>总结</h2><div class="hint-block">{_nl2br(summary)}</div>',
        )

    # Repair suggestions
    repair_suggestions = data.get("repair_suggestions", [])
    if repair_suggestions:
        _insert_html(cursor, "<h2>修复建议</h2>")
        _DIM_REPAIR_MAP = {
            "causal_chain": ("因果校验", "[[nf:status.success.warm]]"),
            "continuity": ("连贯性修复", "[[nf:status.success.warm]]"),
        }
        has_manual_only = any(
            isinstance(s, dict) and s.get("dimension", "") not in _DIM_REPAIR_MAP
            for s in repair_suggestions
        )
        for suggestion in repair_suggestions:
            if isinstance(suggestion, dict):
                dim = suggestion.get("dimension", "")
                dim_label = _EVAL_DIM_LABELS.get(dim, dim)
                priority = suggestion.get("priority", "medium")
                issue = _esc(suggestion.get("issue", ""))
                location = _esc(suggestion.get("location", ""))
                fix = _esc(suggestion.get("suggestion", ""))
                prio_color = {"high": "[[nf:status.danger.deep]]", "medium": "[[nf:status.warning.alt]]", "low": "[[nf:chart.8]]"}.get(
                    priority, "[[nf:text.muted]]"
                )
                adoption_module, adoption_color = _DIM_REPAIR_MAP.get(dim, (None, None))
                adoption_badge = (
                    f' <span style="font-size: 10pt; color:{adoption_color}; '
                    f"background:rgba([[nf:status.success.warm]], 0.08); padding:1px 5px; border-radius:3px; "
                    f'font-weight:500;">已纳入{adoption_module}</span>'
                    if adoption_module
                    else ' <span style="font-size: 10pt; color:[[nf:text.muted]]; '
                    "background:rgba([[nf:text.muted]], 0.08); padding:1px 5px; border-radius:3px; "
                    'font-weight:400;">需手动处理</span>'
                )
                _insert_html(
                    cursor,
                    f'<div class="hint-block">'
                    f'<span style="color:{prio_color}; font-weight:600;">[{_esc(priority)}]</span> '
                    f'<span style="font-weight:600;">{dim_label}</span>'
                    f"{(' · ' + location) if location else ''}"
                    f"{adoption_badge}<br/>"
                    f"💡 {issue}{'：' + fix if fix else ''}"
                    f"</div>",
                )
            else:
                _insert_html(
                    cursor,
                    f'<div class="hint-block">💡 {_esc(str(suggestion))}</div>',
                )
        if has_manual_only:
            _insert_html(
                cursor,
                '<div style="color:[[nf:text.muted]]; font-size: 11pt; margin-top:4px; padding:0 4px;">'
                "标注「需手动处理」的建议不属于因果/连贯性修复范畴，不会被自动采纳，需人工参考后在正文中手动修改。"
                "</div>",
            )


# ── render_bridge_incremental ───────────────────────────────────────


def render_bridge_incremental(browser: QTextBrowser, data: dict[str, Any]) -> None:
    """Incrementally render a chapter bridge into *browser*.

    Produces the same text content as ``render_bridge()`` from
    ``document_renderer_reports.py``.
    """
    _inject_css(browser)
    cursor = _init_browser(browser)

    from_ch = data.get("from_chapter", "?")
    to_ch = data.get("to_chapter", "?")
    mode = data.get("transition_mode", "")

    _insert_header(cursor, "章节桥接")

    mode_label = _TRANSITION_MODE_LABELS.get(mode, mode)
    _insert_html(
        cursor,
        f'<div class="section" style="text-align:center; padding:16px;">'
        f"<div style=\"font-size: 28pt; font-weight:700; font-family:'Songti SC',serif; color:[[nf:text.heading]];\">"
        f"第 {from_ch} 章 → 第 {to_ch} 章</div>"
        f'<div style="color:[[nf:text.muted]]; font-size: 13pt; margin-top:6px;">',
    )
    if mode_label:
        _insert_html(cursor, f'<span class="tag">{_esc(mode_label)}</span>')
    pov = data.get("opening_pov", "")
    if pov:
        _insert_html(cursor, f'<span class="tag-muted tag">{_esc(pov)} 视角</span>')
    _insert_html(cursor, "</div></div>")

    # Opening details
    time_val = data.get("opening_time", "")
    loc_val = data.get("opening_location", "")
    if time_val or loc_val:
        _insert_html(cursor, '<h2>开场设定</h2><div class="section">')
        if time_val:
            _insert_html(
                cursor,
                f'<div class="kv-row"><span class="kv-label">时间：</span><span class="kv-value">{_esc(time_val)}</span></div>',
            )
        if loc_val:
            _insert_html(
                cursor,
                f'<div class="kv-row"><span class="kv-label">地点：</span><span class="kv-value">{_esc(loc_val)}</span></div>',
            )
        _insert_html(cursor, "</div>")

    # Bridge summary
    summary = data.get("bridge_summary", "")
    if summary:
        _insert_html(
            cursor,
            f'<h2>桥接概述</h2><div class="hint-block">{_nl2br(summary)}</div>',
        )

    # Emotional carryover
    emotion = data.get("emotional_carryover", "")
    if emotion:
        _insert_html(
            cursor,
            f'<h2>情感承接</h2><div class="theme-item">{_nl2br(emotion)}</div>',
        )

    # Action handoff
    handoff = data.get("action_handoff", "")
    if handoff:
        _insert_html(
            cursor,
            f'<h2>行动交接</h2><div class="rule-item">{_nl2br(handoff)}</div>',
        )

    # Causal link
    causal_link = data.get("causal_link")
    if isinstance(causal_link, dict):
        prev_event = causal_link.get("previous_event", "")
        mechanism = causal_link.get("causal_mechanism", "")
        unresolved = causal_link.get("unresolved_question", "")
        open_threads = causal_link.get("open_threads", [])
        if prev_event or mechanism or unresolved or open_threads:
            _insert_html(cursor, '<h2>因果承接</h2><div class="section">')
            if prev_event:
                _insert_html(
                    cursor,
                    f'<div class="kv-row"><span class="kv-label">前章事件：</span><div class="kv-value">{_nl2br(prev_event)}</div></div>',
                )
            if mechanism:
                _insert_html(
                    cursor,
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">因果机制：</span><div class="kv-value">{_nl2br(mechanism)}</div></div>',
                )
            if unresolved:
                _insert_html(
                    cursor,
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">悬而未决：</span><div class="kv-value" style="color:[[nf:accent.primary]];">{_nl2br(unresolved)}</div></div>',
                )
            if open_threads:
                thread_html = "".join(f"<li>{_esc(t)}</li>" for t in open_threads)
                _insert_html(
                    cursor,
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">开放线索：</span><ul style="margin:4px 0; padding-left:20px;">{thread_html}</ul></div>',
                )
            _insert_html(cursor, "</div>")

    # Relationship beat
    rel_beat = data.get("relationship_beat")
    if isinstance(rel_beat, dict):
        _insert_html(cursor, '<h2>关系节拍</h2><div class="section">')
        for key, label in (
            ("current_trust_level", "信任度"),
            ("unspoken_tension", "潜在张力"),
            ("power_dynamic", "权力动态"),
        ):
            value = rel_beat.get(key, "")
            if value:
                _insert_html(
                    cursor,
                    f'<div class="kv-row"><span class="kv-label">{label}：</span>'
                    f'<div class="kv-value">{_nl2br(value)}</div></div>',
                )
        _insert_html(cursor, "</div>")

    # Sensory anchors
    anchors = data.get("sensory_anchors", [])
    if anchors:
        _insert_html(cursor, "<h2>感官锚点</h2>")
        for anchor in anchors:
            _insert_html(cursor, f'<div class="rule-item">{_esc(anchor)}</div>')

    # Pending questions
    questions = data.get("pending_questions", [])
    if questions:
        _insert_html(cursor, "<h2>悬而未决的问题</h2>")
        q_html = "".join(f"<li>{_esc(question)}</li>" for question in questions)
        _insert_html(cursor, f"<ul>{q_html}</ul>")

    # Forbidden repetition
    forbidden = data.get("forbidden_repetition", [])
    if forbidden:
        _insert_html(cursor, "<h2>下一章避免重复</h2>")
        f_html = "".join(f"<li>{_esc(item)}</li>" for item in forbidden)
        _insert_html(cursor, f"<ul>{f_html}</ul>")


# ── render_story_bible_incremental ──────────────────────────────────


def render_story_bible_incremental(browser: QTextBrowser, data: dict[str, Any]) -> None:
    """Incrementally render a story bible into *browser*.

    Produces the same text content as ``render_story_bible()`` from
    ``document_renderer_reports.py``.
    """
    _inject_css(browser)
    cursor = _init_browser(browser)

    title = data.get("title", "世界观设定")
    _insert_header(cursor, title)

    # Bible fields
    for key, label in _BIBLE_FIELD_LABELS.items():
        value = data.get(key, "")
        if value:
            _insert_html(
                cursor,
                f'<div class="section"><h3>{_esc(label)}</h3>'
                f'<div class="kv-value">{_nl2br(value)}</div></div>',
            )

    # Rules
    rules = data.get("rules", [])
    if rules:
        _insert_html(cursor, "<h2>世界规则</h2>")
        for rule in rules:
            _insert_html(cursor, f'<div class="rule-item">{_nl2br(rule)}</div>')

    # Themes
    themes = data.get("themes", [])
    if themes:
        _insert_html(cursor, "<h2>核心主题</h2>")
        for theme in themes:
            _insert_html(cursor, f'<div class="theme-item">{_nl2br(theme)}</div>')


# ── IncrementalDocumentRenderer ─────────────────────────────────────


class IncrementalDocumentRenderer:
    """Reusable wrapper for cached HTML updates on a QTextBrowser/QTextEdit.

    Avoids the full DOM rebuild that repeated ``setHtml()`` calls trigger.

    Every update goes through ``QTextCursor.insertHtml()``.  The renderer skips
    identical content while the underlying document revision is unchanged,
    and rebuilds when another code path has replaced the document contents.

    Usage::

        browser = QTextBrowser()
        renderer = IncrementalDocumentRenderer(browser)

        # First load
        renderer.update_content("<h1>Hello</h1>")

        # Same content — no-op (cache hit, 0 DOM rebuilds)
        renderer.update_content("<h1>Hello</h1>")

        # Different content — clear + insertHtml()
        renderer.update_content("<h1>World</h1>")

        # None / empty — clears the browser safely
        renderer.update_content(None)
    """

    def __init__(self, browser: QTextEdit) -> None:
        self._browser = browser
        self._last_html: str = ""
        self._last_document_revision: int | None = None

    @property
    def browser(self) -> QTextEdit:
        return self._browser

    @property
    def last_html(self) -> str:
        return self._last_html

    def reset(self) -> None:
        self._last_html = ""
        self._last_document_revision = None

    def update_content(self, text: str | None) -> None:
        """Render HTML into the document.

        Historical note: this method's name included 'incremental' but it was
        actually fully re-rendering on every change (clear() + insertHtml()).
        The only "increment" was the in-memory string-equality fast path
        below. Phase M2.4 (2026-07) clarifies the docstring without changing
        behavior — current max chapter is 28 KB and does not bottleneck
        QTextBrowser.setHtml/insertHtml.

        To enable chunked rendering for very large chapters in the future,
        instantiate components/chunked_html_setter.py and route HTML through
        it. Bench first via scripts/benchmark_chapter_render.py; do not
        activate unconditionally because chunked insertHtml at character
        boundaries can split tags/entities and QApplication.processEvents
        inside paint has re-entrancy risks.

        Behavior:
        - **Same content and document revision**: **no-op** — skips all work.
        - **Different or externally replaced content**: clears the document
          and uses ``QTextCursor.insertHtml()`` for a deterministic rebuild.
        - **None or empty string**: clears the browser.

        Args:
            text: The HTML string to render.  ``None`` is treated as empty.
        """
        html = render_document_markup(text) if text is not None else ""

        document = self._browser.document()
        if html == self._last_html and document.revision() == self._last_document_revision:
            return

        self._browser.clear()
        if html:
            cursor = QTextCursor(document)
            cursor.insertHtml(html)

        self._last_html = html
        self._last_document_revision = document.revision()


def browser_html_renderer(browser: QTextEdit) -> IncrementalDocumentRenderer:
    """Return the renderer attached to *browser*, creating it when needed."""
    existing = getattr(browser, "incremental_renderer", None)
    if isinstance(existing, IncrementalDocumentRenderer) and existing.browser is browser:
        return existing
    renderer = IncrementalDocumentRenderer(browser)
    browser.incremental_renderer = renderer  # type: ignore[attr-defined]
    return renderer


def update_browser_html(browser: QTextEdit, html: str | None) -> None:
    """Update a rich-text widget through its shared cached renderer."""
    browser_html_renderer(browser).update_content(html)
