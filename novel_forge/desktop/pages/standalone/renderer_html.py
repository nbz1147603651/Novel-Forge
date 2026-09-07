"""Shared HTML helper functions and CSS for document renderers.

Extracted from the monolithic ``document_renderers.py`` to reduce file size
and allow reuse.  All render_* functions import these helpers.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba
from novel_forge.desktop.theme.documents import render_document_css, render_document_markup

# ═══════════════════════════════════════════════════════════════════
# Common HTML base style — single source of truth for all renderers
# ═══════════════════════════════════════════════════════════════════

_BASE_CSS_TEMPLATE = """
* {
    box-sizing: border-box;
}
body {
    font-family: "PingFang SC", "Helvetica Neue", "Arial";
    color: {{text.primary}};
    font-size: 14px;
    line-height: 1.78;
    margin: 0;
    padding: 10px 8px;
    background: transparent;
    overflow-wrap: anywhere;
}
h1 {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    color: {{text.heading.deep}};
    font-size: 22px;
    font-weight: 700;
    margin: 0 0 22px 0;
    padding-bottom: 0;
    border-bottom: none;
}
h2 {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    color: {{text.heading}};
    font-size: 16px;
    font-weight: 600;
    margin: 20px 0 8px 0;
    padding-bottom: 5px;
    border-bottom: 1px solid rgba({{border.default}}, 0.18);
}
h3 {
    color: {{text.tab.hover}};
    font-size: 14px;
    font-weight: 700;
    margin: 16px 0 6px 0;
}
.section {
    background: rgba({{bg.surface}}, 0.7);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 10px;
    padding: 14px 18px;
    margin: 10px 0;
    overflow-x: visible;
}
.kv-row {
    margin: 7px 0;
}
.kv-label {
    color: {{text.muted.strong}};
    font-size: 13px;
    font-weight: 700;
    display: inline;
}
.kv-value {
    color: {{text.artifact}};
    font-size: 14px;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.tag {
    display: inline-block;
    background: rgba({{accent.primary}}, 0.10);
    color: {{accent.deep}};
    border: 1px solid rgba({{accent.primary}}, 0.25);
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 9px;
    font-weight: 600;
    margin: 1px 2px 1px 0;
    line-height: 1.2;
    white-space: nowrap;
}
.tag-muted {
    background: rgba({{border.default}}, 0.08);
    color: {{text.chapter.rail}};
    border-color: rgba({{border.default}}, 0.18);
}
.rule-item {
    background: rgba({{bg.inset}}, 0.7);
    border-left: 3px solid rgba({{accent.primary}}, 0.4);
    padding: 8px 14px;
    margin: 6px 0;
    border-radius: 0 6px 6px 0;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.theme-item {
    background: rgba({{status.success}}, 0.06);
    border-left: 3px solid rgba({{status.success}}, 0.4);
    padding: 8px 14px;
    margin: 6px 0;
    border-radius: 0 6px 6px 0;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.blueprint-meta {
    color: {{text.muted.strong}};
    font-size: 11px;
}
.blueprint-secondary {
    color: {{text.muted}};
    font-size: 11px;
}
.blueprint-note {
    color: {{accent.deep}};
    font-size: 11px;
}
.blueprint-convertible-badge {
    display: inline-block;
    background: rgba({{accent.primary}}, 0.10);
    color: {{accent.deep}};
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 10px;
    font-weight: 600;
}
.hint-block {
    background: rgba({{bg.input.soft}}, 0.9);
    border: 1px dashed rgba({{border.default}}, 0.2);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    line-height: 1.6;
    color: {{text.body}};
    overflow-wrap: anywhere;
    word-break: break-word;
}
.metric-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
    gap: 8px;
    margin: 8px 0 12px 0;
}
.metric-card {
    background: rgba({{bg.input}}, 0.58);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 8px;
    padding: 8px 10px;
}
.metric-label {
    color: {{text.muted.strong}};
    font-size: 11px;
    font-weight: 700;
    line-height: 1.3;
}
.metric-value {
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 20px;
    font-weight: 700;
    line-height: 1.35;
}
.mini-card {
    background: rgba({{bg.input}}, 0.54);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    padding: 10px 12px;
    margin: 8px 0;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.compact-list {
    margin: 6px 0;
    padding-left: 18px;
}
.compact-list li {
    margin: 3px 0;
    line-height: 1.62;
}
.score-bar-bg {
    background: rgba({{bg.control.hover}}, 0.5);
    border-radius: 4px;
    height: 8px;
    width: 100%;
}
.score-bar-fill {
    border-radius: 4px;
    height: 8px;
}
.prose-body {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 14px;
    line-height: 1.95;
    color: {{text.primary}};
    text-indent: 2em;
}
.prose-body p {
    margin: 0 0 0.8em 0;
    text-indent: 2em;
}
.chapter-header {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    color: {{text.heading.deep}};
    font-size: 17px;
    font-weight: 700;
    text-align: center;
    padding: 16px 0 12px 0;
    border-bottom: 1px solid rgba({{border.default}}, 0.15);
    margin-bottom: 20px;
}
.word-count {
    text-align: center;
    color: {{text.muted}};
    font-size: 12px;
    margin-bottom: 16px;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
}
th {
    background: rgba({{bg.inset}}, 0.7);
    color: {{text.body.alt}};
    font-size: 12px;
    font-weight: 700;
    text-align: left;
    padding: 8px 12px;
    border-bottom: 2px solid rgba({{border.default}}, 0.2);
}
td {
    padding: 7px 12px;
    border-bottom: 1px solid rgba({{border.default}}, 0.1);
    font-size: 13px;
    vertical-align: top;
    overflow-wrap: anywhere;
    word-break: break-word;
}
tr:hover td {
    background: rgba({{bg.hover.secondary}}, 0.6);
}
a.deep-link {
    color: {{accent.deep}};
    text-decoration: none;
    border-bottom: 1px dashed rgba({{accent.primary}}, 0.4);
    cursor: pointer;
}
a.deep-link:hover {
    color: {{accent.primary}};
    border-bottom-style: solid;
}
"""

_STREAM_REPORT_CSS_TEMPLATE = """
.report-body h2 {
    font-family: "Songti SC", "STSong", "Georgia", serif;
    color: {{text.heading}};
    font-size: 15px;
    font-weight: 700;
    margin: 14px 0 6px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba({{border.default}}, 0.16);
}
.report-body h3 {
    color: {{text.tab.hover}};
    font-size: 13px;
    font-weight: 700;
    margin: 10px 0 5px 0;
}
.report-body .section {
    background: rgba({{bg.surface}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    padding: 10px 12px;
    margin: 8px 0;
}
.report-body .kv-row {
    margin: 6px 0;
}
.report-body .kv-label {
    color: {{text.muted.strong}};
    font-size: 12px;
    font-weight: 700;
}
.report-body .kv-value {
    color: {{text.artifact}};
    font-size: 13px;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.report-body .tag {
    display: inline-block;
    background: rgba({{accent.primary}}, 0.10);
    color: {{accent.deep}};
    border: 1px solid rgba({{accent.primary}}, 0.25);
    border-radius: 3px;
    padding: 1px 4px;
    font-size: 9px;
    font-weight: 700;
    margin: 1px 2px 1px 0;
    line-height: 1.2;
    white-space: nowrap;
}
.report-body .tag-muted {
    background: rgba({{border.default}}, 0.08);
    color: {{text.chapter.rail}};
    border-color: rgba({{border.default}}, 0.18);
}
.report-body .metric-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
    gap: 7px;
    margin: 7px 0 10px 0;
}
.report-body .metric-card,
.report-body .mini-card {
    background: rgba({{bg.input}}, 0.58);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    padding: 8px 10px;
    margin: 6px 0;
}
.report-body .metric-label {
    color: {{text.muted.strong}};
    font-size: 11px;
    font-weight: 700;
}
.report-body .metric-value {
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 19px;
    font-weight: 700;
}
.report-body .rule-item,
.report-body .theme-item {
    background: rgba({{bg.inset}}, 0.70);
    border-left: 3px solid rgba({{accent.primary}}, 0.40);
    padding: 7px 11px;
    margin: 5px 0;
    border-radius: 0 6px 6px 0;
    font-size: 12px;
    line-height: 1.58;
    overflow-wrap: anywhere;
    word-break: break-word;
}
.report-body .theme-item {
    background: rgba({{status.success}}, 0.06);
    border-left-color: rgba({{status.success}}, 0.40);
}
.report-body .hint-block {
    background: rgba({{bg.input.soft}}, 0.90);
    border: 1px dashed rgba({{border.default}}, 0.20);
    border-radius: 8px;
    padding: 8px 11px;
    margin: 5px 0;
    font-size: 12px;
    line-height: 1.58;
    color: {{text.body}};
    overflow-wrap: anywhere;
    word-break: break-word;
}
.report-body .score-bar-bg {
    background: rgba({{bg.control.hover}}, 0.50);
    border-radius: 4px;
    height: 8px;
    width: 100%;
}
.report-body .score-bar-fill {
    border-radius: 4px;
    height: 8px;
}
.report-body table {
    border-collapse: collapse;
    width: 100%;
    margin: 7px 0;
}
.report-body th {
    background: rgba({{bg.inset}}, 0.70);
    color: {{text.body.alt}};
    font-size: 11px;
    font-weight: 700;
    text-align: left;
    padding: 6px 9px;
    border-bottom: 1px solid rgba({{border.default}}, 0.18);
}
.report-body td {
    padding: 6px 9px;
    border-bottom: 1px solid rgba({{border.default}}, 0.10);
    font-size: 12px;
    vertical-align: top;
    overflow-wrap: anywhere;
    word-break: break-word;
}
"""

_BROWSER_STYLESHEET_TEMPLATE = """
QTextBrowser {
    background: rgba({{bg.input}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 8px;
    padding: 0px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}
"""


def base_css() -> str:
    """Return the shared HTML document CSS for the active desktop theme."""

    return render_document_css(_BASE_CSS_TEMPLATE)


def browser_stylesheet() -> str:
    """Return the QTextBrowser shell stylesheet for the active desktop theme."""

    return render_document_css(_BROWSER_STYLESHEET_TEMPLATE)


def stream_report_css() -> str:
    """Return token-resolved CSS for task-focus structured reports."""
    return render_document_css(_STREAM_REPORT_CSS_TEMPLATE)


def html_wrap(body: str, title: str = "") -> str:
    """Wrap body HTML with the shared CSS."""
    header = f"<h1>{esc(title)}</h1>" if title else ""
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<style>{base_css()}</style>"
        f"</head><body>{header}{render_document_markup(body)}</body></html>"
    )


def esc(text: str) -> str:
    """HTML-escape text."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def nl2br(text: str) -> str:
    """Convert newlines to ``<br>``."""
    return esc(text).replace("\n", "<br>")


def make_browser(html: str) -> QTextBrowser:
    """Create a styled ``QTextBrowser`` with the given HTML content.

    The returned browser has an ``incremental_renderer`` attribute
    (:class:`IncrementalDocumentRenderer`) that callers can use for
    subsequent ``update_content()`` calls instead of ``setHtml()``.
    """
    from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html

    browser = QTextBrowser()
    browser.setObjectName("docViewerContent")
    browser.setOpenExternalLinks(False)
    update_browser_html(browser, render_document_markup(html))
    browser.setStyleSheet(browser_stylesheet())
    return browser


def score_color(score: float) -> str:
    """Pick a traffic-light color for a 0-10 score."""
    if score >= 8:
        return qcolor_hex("status.success.warm")
    if score >= 6:
        return qcolor_hex("accent.primary")
    return qcolor_hex("status.danger.deep")


def score_bar_html(score: float, max_score: float = 10.0) -> str:
    """Render an inline score bar as HTML."""
    pct = min(100, max(0, score / max_score * 100))
    color = score_color(score)
    return (
        f'<div style="display:flex; align-items:center; gap:8px;">'
        f'<div style="flex:1; background:{qcolor_rgba("bg.control.hover", 0.5)}; '
        f'border-radius:4px; height:8px; overflow:hidden;">'
        f'<div style="width:{pct:.0f}%; background:{color}; '
        f'height:8px; border-radius:4px;"></div></div>'
        f'<span style="font-size:13px; font-weight:700; color:{color}; '
        f'min-width:36px; text-align:right;">{score:.1f}</span></div>'
    )


def rel_metric_bar(label: str, value: float, color: str) -> str:
    """Render a token-aware horizontal relationship metric bar."""
    pct = min(100, max(0, value * 100))
    return (
        f'<div style="display:flex; align-items:center; margin:3px 0;">'
        f'<span class="metric-label">{esc(label)}</span>'
        f'<div style="flex:1; background:{qcolor_rgba("bg.control.hover", 0.5)}; '
        f'border-radius:4px; height:6px; overflow:hidden; margin:0 8px;">'
        f'<div style="width:{pct:.0f}%; background:{color}; '
        f'height:6px; border-radius:4px;"></div></div>'
        f'<span class="metric-val" style="color:{color};">'
        f"{value:.0%}</span></div>"
    )


def trust_color(value: float) -> str:
    """Return the active-theme semantic color for a trust metric."""
    if value >= 0.7:
        return qcolor_hex("status.success.warm")
    if value >= 0.4:
        return qcolor_hex("status.warning")
    return qcolor_hex("status.danger.deep")


def tension_color(value: float) -> str:
    """Return the active-theme semantic color for a tension metric."""
    if value >= 0.7:
        return qcolor_hex("status.danger.deep")
    if value >= 0.4:
        return qcolor_hex("status.warning")
    return qcolor_hex("status.success.warm")


# ═══════════════════════════════════════════════════════════════════
# Deep-link helpers — clickable anchors that navigate within the app
# ═══════════════════════════════════════════════════════════════════

DEEP_LINK_SCHEME = "nf"


def chapter_deep_link(
    project_id: str,
    chapter_number: int,
    label: str | None = None,
) -> str:
    """Return an ``<a>`` tag that triggers in-app chapter navigation.

    URL format: ``nf://chapter/{project_id}/{chapter_number}``
    """
    url = f"{DEEP_LINK_SCHEME}://chapter/{esc(project_id)}/{chapter_number}"
    display = esc(label or f"第 {chapter_number} 章")
    return f'<a class="deep-link" href="{url}">{display}</a>'


def parse_deep_link(url_string: str) -> dict[str, str | int] | None:
    """Parse a deep-link URL and return structured navigation data.

    Returns ``None`` if the URL is not a recognized deep link.
    Supported: ``nf://chapter/{project_id}/{chapter_number}``
    """
    if not url_string.startswith(f"{DEEP_LINK_SCHEME}://"):
        return None
    path = url_string[len(f"{DEEP_LINK_SCHEME}://") :]
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "chapter":
        try:
            return {
                "type": "chapter",
                "project_id": parts[1],
                "chapter_number": int(parts[2]),
            }
        except (ValueError, IndexError):
            return None
    return None


def connect_deep_links(
    browser: QTextBrowser,
    callback: object,
) -> None:
    """Connect a QTextBrowser's anchorClicked signal to a deep-link handler.

    *callback* receives ``(str)`` — the raw URL string from the anchor.
    Only call this on browsers that contain deep-link anchors.
    """
    browser.setOpenLinks(False)
    browser.anchorClicked.connect(callback)
