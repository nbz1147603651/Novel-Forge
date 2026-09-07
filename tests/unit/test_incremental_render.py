"""Tests for IncrementalDocumentRenderer — caching, incremental updates, edge cases."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser

from novel_forge.desktop.pages.document_renderer.incremental import (
    IncrementalDocumentRenderer,
    browser_html_renderer,
    update_browser_html,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture()
def browser(qapp: QApplication) -> QTextBrowser:
    return QTextBrowser()


@pytest.fixture()
def renderer(browser: QTextBrowser) -> IncrementalDocumentRenderer:
    return IncrementalDocumentRenderer(browser)


HTML_A = "<h1>Alpha</h1><p>First content</p>"
HTML_B = "<h1>Beta</h1><p>Second content</p>"
HTML_LARGE = "<div>" + "".join(f"<p>Paragraph {i}</p>" for i in range(200)) + "</div>"


class TestFirstLoad:
    def test_first_update_renders_content(self, renderer: IncrementalDocumentRenderer) -> None:
        renderer.update_content(HTML_A)
        assert renderer.last_html == HTML_A
        assert renderer.browser.toPlainText().strip()

    def test_first_load_renders_correct_text(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        text = renderer.browser.toPlainText()
        assert "Alpha" in text
        assert "First content" in text


class TestCacheHit:
    def test_same_content_is_noop(self, renderer: IncrementalDocumentRenderer) -> None:
        renderer.update_content(HTML_A)
        text_after_first = renderer.browser.toPlainText()
        renderer.update_content(HTML_A)
        assert renderer.browser.toPlainText() == text_after_first

    def test_same_content_does_not_change_last_html(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        renderer.update_content(HTML_A)
        assert renderer.last_html == HTML_A

    def test_external_document_change_invalidates_cache(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        renderer.browser.setPlainText("external replacement")

        renderer.update_content(HTML_A)

        assert "Alpha" in renderer.browser.toPlainText()
        assert "external replacement" not in renderer.browser.toPlainText()


class TestDifferentContent:
    def test_different_content_updates(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        renderer.update_content(HTML_B)
        text = renderer.browser.toPlainText()
        assert "Beta" in text
        assert renderer.last_html == HTML_B

    def test_visual_output_matches_setHtml(
        self, browser: QTextBrowser, qapp: QApplication,
    ) -> None:
        browser.setHtml(HTML_A)
        sethtml_text = browser.toPlainText()

        browser2 = QTextBrowser()
        r = IncrementalDocumentRenderer(browser2)
        r.update_content(HTML_A)
        incremental_text = browser2.toPlainText()

        assert sethtml_text == incremental_text


class TestEdgeCases:
    def test_none_content_does_not_crash(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        renderer.update_content(None)
        assert renderer.last_html == ""

    def test_empty_string_content(self, renderer: IncrementalDocumentRenderer) -> None:
        renderer.update_content(HTML_A)
        renderer.update_content("")
        assert renderer.last_html == ""

    def test_none_then_content(self, renderer: IncrementalDocumentRenderer) -> None:
        renderer.update_content(None)
        renderer.update_content(HTML_A)
        assert "Alpha" in renderer.browser.toPlainText()

    def test_empty_then_same_empty_is_noop(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content("")
        renderer.update_content("")
        assert renderer.last_html == ""


class TestReset:
    def test_reset_forces_next_render(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)
        renderer.reset()
        assert renderer.last_html == ""
        renderer.update_content(HTML_A)
        assert "Alpha" in renderer.browser.toPlainText()


class TestPerformanceSameContent:
    def test_1000_same_content_updates_minimal_rebuilds(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_LARGE)

        start = time.perf_counter()
        for _ in range(1000):
            renderer.update_content(HTML_LARGE)
        elapsed = time.perf_counter() - start

        assert renderer.last_html == HTML_LARGE
        assert elapsed < 1.0, f"1000 same-content updates took {elapsed:.3f}s (expected <1s)"


class TestPerformanceDifferentContent:
    def test_1000_different_content_updates_avg_under_1ms(
        self, renderer: IncrementalDocumentRenderer,
    ) -> None:
        renderer.update_content(HTML_A)

        start = time.perf_counter()
        for i in range(1000):
            renderer.update_content(f"<p>Content variant {i}</p>")
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 1000) * 1000
        assert avg_ms < 1.0, f"Avg update {avg_ms:.3f}ms (expected <1ms)"


class TestBrowserProperty:
    def test_browser_property_returns_wrapped_widget(
        self, browser: QTextBrowser, renderer: IncrementalDocumentRenderer,
    ) -> None:
        assert renderer.browser is browser

    def test_shared_helper_reuses_attached_renderer(self, browser: QTextBrowser) -> None:
        update_browser_html(browser, HTML_A)
        first = browser_html_renderer(browser)

        update_browser_html(browser, HTML_B)

        assert browser_html_renderer(browser) is first
        assert "Beta" in browser.toPlainText()
