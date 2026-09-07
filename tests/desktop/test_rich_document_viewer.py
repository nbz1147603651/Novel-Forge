from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser, QToolButton

from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer


@pytest.fixture
def app(qtbot):  # noqa: ANN001
    return qtbot


def _make_json(tmp_path: Path) -> Path:
    p = tmp_path / "spec.json"
    p.write_text('{"title": "test", "genre": "生活治愈", "length_target": 100}', encoding="utf-8")
    return p


def test_rich_document_viewer_wraps_textbrowser(tmp_path: Path) -> None:
    """RichDocumentViewer 包装 QTextBrowser 并暴露 body_browser()."""
    _app = QApplication.instance() or QApplication([])
    body = QTextBrowser()
    body.setHtml("<h1>hello</h1>")
    viewer = RichDocumentViewer(body, file_path=tmp_path / "x.json")
    assert viewer.body_browser() is body


def test_rich_document_viewer_has_toolbar(tmp_path: Path) -> None:
    """工具条包含：复制全文 / 在文件夹中显示 / 切换原始 JSON。"""
    _app = QApplication.instance() or QApplication([])
    body = QTextBrowser()
    path = _make_json(tmp_path)
    viewer = RichDocumentViewer(body, file_path=path, raw_text='{"raw": true}')
    toolbar_buttons: list[str] = []
    for child in viewer.findChildren(QToolButton):
        toolbar_buttons.append(child.toolTip())
    assert any("复制" in t for t in toolbar_buttons)
    assert any("文件夹" in t for t in toolbar_buttons)
    assert any("原始" in t for t in toolbar_buttons)


def test_toggle_raw_mode_emits_signal(tmp_path: Path, qtbot) -> None:  # noqa: ANN001
    """点击切换原始 JSON 按钮会发射 raw_mode_toggled 信号。"""
    _app = QApplication.instance() or QApplication([])
    body = QTextBrowser()
    path = _make_json(tmp_path)
    viewer = RichDocumentViewer(body, file_path=path, raw_text='{"raw": true}')

    with qtbot.waitSignal(viewer.raw_mode_toggled, timeout=1000):
        for child in viewer.findChildren(QToolButton):
            if "原始" in child.toolTip():
                child.click()
                break


def test_toggle_back_to_rich_restores_original_html(tmp_path: Path, qtbot) -> None:
    """点击切换到原始后再切回，原 HTML 应被恢复（不在 showEvent 之前也不能坏）。"""
    _app = QApplication.instance() or QApplication([])
    body = QTextBrowser()
    body.setHtml("<h2>original</h2>")
    path = _make_json(tmp_path)
    viewer = RichDocumentViewer(body, file_path=path, raw_text='{"raw": true}')
    # 不调 showEvent，直接点击切换
    raw_btn = next(b for b in viewer.findChildren(QToolButton) if "原始" in b.toolTip())
    raw_btn.click()  # 切到原始
    raw_btn.click()  # 切回富渲染
    # 切回后 body 应包含 original
    html = body.toHtml()
    assert "original" in html, f"original HTML not restored, got: {html[:200]}"