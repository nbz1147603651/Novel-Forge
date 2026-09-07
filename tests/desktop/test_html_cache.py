"""Tests for HTML hash cache (I-3)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser

pytestmark = pytest.mark.desktop


def _ensure_app() -> None:
    """Ensure a QApplication instance exists (side effect only)."""
    QApplication.instance() or QApplication([])  # noqa: F841


def test_should_set_html_first_call_returns_true():
    """First call for a widget returns True (no previous hash)."""
    from novel_forge.desktop.components.html_cache import should_set_html

    _ensure_app()
    browser = QTextBrowser()
    assert should_set_html(browser, "<p>hello</p>") is True


def test_should_set_html_same_payload_returns_false():
    """Second call with same payload returns False."""
    from novel_forge.desktop.components.html_cache import should_set_html

    _ensure_app()
    browser = QTextBrowser()
    html = "<p>hello world</p>"
    assert should_set_html(browser, html) is True
    assert should_set_html(browser, html) is False


def test_should_set_html_different_payload_returns_true():
    """Different payload returns True."""
    from novel_forge.desktop.components.html_cache import should_set_html

    _ensure_app()
    browser = QTextBrowser()
    assert should_set_html(browser, "<p>a</p>") is True
    assert should_set_html(browser, "<p>b</p>") is True


def test_should_set_html_new_widget_does_not_reuse_hash():
    """New widget (after old one destroyed) should not skip first setHtml."""
    from novel_forge.desktop.components.html_cache import should_set_html

    _ensure_app()
    old_browser = QTextBrowser()
    should_set_html(old_browser, "<p>shared</p>")
    old_browser.deleteLater()

    new_browser = QTextBrowser()
    # New widget has no _nf_last_html_hash property → first call returns True
    assert should_set_html(new_browser, "<p>shared</p>") is True


def test_should_set_html_oversize_returns_true():
    """Payload > 1 MB returns True (don't cache huge objects)."""
    from novel_forge.desktop.components.html_cache import should_set_html

    _ensure_app()
    browser = QTextBrowser()
    huge = "x" * (1_000_001)
    assert should_set_html(browser, huge) is True
    # 第二次也 True（不缓存）
    assert should_set_html(browser, huge) is True
