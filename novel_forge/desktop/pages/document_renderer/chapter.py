"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains chapter.py renderers.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QTextBrowser,
    QWidget,
)

from novel_forge.core.domain.guardrails import scrub_prompt_artifacts
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.desktop.pages.standalone.renderer_html import (
    esc as _esc,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    html_wrap as _html_wrap,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    make_browser as _make_browser,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]


def render_chapter_prose(text: str, chapter_num: int = 0, title: str = "") -> QTextBrowser:
    """Render chapter prose text with proper typographic formatting."""
    text, _ = scrub_prompt_artifacts(text)
    parts: list[str] = []

    # Header
    if chapter_num > 0 or title:
        header = f"第 {chapter_num} 章" if chapter_num > 0 else ""
        if title:
            header = f"{header} · {title}" if header else title
        parts.append(f'<div class="chapter-header">{_esc(header)}</div>')

    # Word count
    count = display_word_count(text)
    if count > 0:
        parts.append(f'<div class="word-count">约 {count:,} 字</div>')

    # Prose body — split into paragraphs, stripping any markdown headings
    # (e.g. "## 第7章") that the LLM may have written into the file and
    # that would duplicate the chapter header already rendered above.
    paragraphs = [
        p.strip() for p in text.split("\n") if p.strip() and not p.lstrip().startswith("#")
    ]
    body_parts = [f"<p>{_esc(p)}</p>" for p in paragraphs]
    parts.append(f'<div class="prose-body">{"".join(body_parts)}</div>')

    html = _html_wrap("\n".join(parts))
    browser = _make_browser(html)
    browser.setObjectName("chapterProseBrowser")
    return browser

