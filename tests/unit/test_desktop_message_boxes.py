from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from novel_forge.desktop.widgets import (
    ask_confirmation,
    show_info_message,
    show_multiline_input_dialog,
    show_text_input_dialog,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _collect_buttons(dialog: QDialog) -> dict[str, QPushButton]:
    return {button.text(): button for button in dialog.findChildren(QPushButton)}


def _snapshot_dialog_buttons(dialog: QDialog) -> dict[str, dict[str, object]]:
    return {
        text: {
            "object_name": button.objectName(),
            "variant": button.property("variant"),
            "compact": button.property("compact"),
        }
        for text, button in _collect_buttons(dialog).items()
    }


def test_ask_confirmation_uses_styled_app_buttons(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    captured_dialog: dict[str, QDialog] = {}

    def _exec(self: QDialog) -> int:
        captured_dialog["dialog"] = self
        buttons = _collect_buttons(self)
        buttons["删除项目"].click()
        return 0

    monkeypatch.setattr(QDialog, "exec", _exec)

    accepted = ask_confirmation(
        None,
        "确认删除",
        "确定要永久删除项目「遗物人生」？",
        informative_text="此操作不可撤销，项目目录及所有生成内容将被清除。",
        confirm_text="删除项目",
        cancel_text="取消",
        confirm_variant="danger",
    )

    assert accepted is True
    snapshots = _snapshot_dialog_buttons(captured_dialog["dialog"])
    assert snapshots["删除项目"]["object_name"] == "actionButton"
    assert snapshots["删除项目"]["variant"] == "danger"
    assert snapshots["删除项目"]["compact"] is True
    assert snapshots["取消"]["variant"] == "secondary"


def test_show_info_message_uses_localized_primary_button(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    captured_dialog: dict[str, QDialog] = {}

    def _exec(self: QDialog) -> int:
        captured_dialog["dialog"] = self
        buttons = _collect_buttons(self)
        buttons["知道了"].click()
        return 0

    monkeypatch.setattr(QDialog, "exec", _exec)

    show_info_message(None, "已保存", "配置已更新。")

    snapshots = _snapshot_dialog_buttons(captured_dialog["dialog"])
    assert snapshots["知道了"]["object_name"] == "actionButton"
    assert snapshots["知道了"]["variant"] == "primary"
    assert snapshots["知道了"]["compact"] is True


def test_show_text_input_dialog_uses_app_dialog_style(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot: dict[str, object] = {}

    def _exec(self: QDialog) -> int:
        input_box = self.findChild(QLineEdit, "dialogInput")
        assert input_box is not None
        input_box.setText("星火预设")
        buttons = _collect_buttons(self)
        snapshot.update(
            {
                "object_name": self.objectName(),
                "input_name": input_box.objectName(),
                "confirm_variant": buttons["保存"].property("variant"),
                "cancel_variant": buttons["取消"].property("variant"),
                "confirm_compact": buttons["保存"].property("compact"),
            }
        )
        buttons["保存"].click()
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(QDialog, "exec", _exec)

    text, accepted = show_text_input_dialog(
        None,
        "存为预设",
        "为此预设命名：",
        confirm_text="保存",
    )

    assert accepted is True
    assert text == "星火预设"
    assert snapshot["object_name"] == "appDialog"
    assert snapshot["input_name"] == "dialogInput"
    assert snapshot["confirm_variant"] == "primary"
    assert snapshot["cancel_variant"] == "secondary"
    assert snapshot["confirm_compact"] is True


def test_show_multiline_input_dialog_wraps_and_localizes_actions(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    snapshot: dict[str, object] = {}

    def _exec(self: QDialog) -> int:
        text_box = self.findChild(QPlainTextEdit, "dialogTextInput")
        assert text_box is not None
        buttons = _collect_buttons(self)
        context = self.findChild(QLabel, "dialogContext")
        counter = self.findChild(QLabel, "dialogCounter")
        assert context is not None
        assert counter is not None

        snapshot.update(
            {
                "object_name": self.objectName(),
                "line_wrap": text_box.lineWrapMode(),
                "horizontal_scroll": text_box.horizontalScrollBarPolicy(),
                "tab_changes_focus": text_box.tabChangesFocus(),
                "initial_selected": text_box.textCursor().hasSelection(),
                "context": context.text(),
                "confirm_variant": buttons["生成并试听"].property("variant"),
                "cancel_variant": buttons["取消"].property("variant"),
            }
        )
        text_box.setPlainText("微调后")
        assert counter.text() == "3 字"
        buttons["恢复上游简报"].click()
        assert text_box.toPlainText() == "上游简报"
        text_box.setPlainText("最终简报")
        buttons["生成并试听"].click()
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(QDialog, "exec", _exec)

    text, accepted = show_multiline_input_dialog(
        None,
        "AI 音色设计",
        "根据上游角色特质生成，可在提交前微调。",
        heading="确认角色音色简报",
        initial_text="上游简报",
        helper_text="Ctrl + Enter 可快速提交。",
        context_text="角色 · 沈岸    平台 · MiniMax",
        confirm_text="生成并试听",
        reset_text="恢复上游简报",
    )

    assert accepted is True
    assert text == "最终简报"
    assert snapshot["object_name"] == "appDialog"
    assert snapshot["line_wrap"] == QPlainTextEdit.LineWrapMode.WidgetWidth
    assert snapshot["horizontal_scroll"] == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert snapshot["tab_changes_focus"] is True
    assert snapshot["initial_selected"] is False
    assert "MiniMax" in str(snapshot["context"])
    assert snapshot["confirm_variant"] == "primary"
    assert snapshot["cancel_variant"] == "secondary"


def test_message_box_action_layout_avoids_text_overlap(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Regression: long Chinese informative text with >2 action buttons must
    not visually overlap the button row. The text label must wrap, the
    button row must sit below the text body, and the dialog must size
    tall enough to contain both without geometry collision.
    """
    from novel_forge.desktop.components.dialogs import (
        MessageBoxAction,
        show_message_box,
    )

    captured: dict[str, object] = {}

    def _exec(self: QDialog) -> int:
        labels = list(self.findChildren(QLabel))
        text_label = next(
            (lbl for lbl in labels if lbl.objectName() == "dialogText"),
            None,
        )
        info_label = next(
            (lbl for lbl in labels if lbl.objectName() == "dialogInformative"),
            None,
        )
        assert text_label is not None, "dialogText label missing"
        assert info_label is not None, "dialogInformative label missing"
        assert text_label.wordWrap() is True
        assert info_label.wordWrap() is True

        buttons = _collect_buttons(self)
        assert set(buttons) == {
            "保存为待应用修订",
            "等待任务完成后应用",
            "停止连跑并保存",
            "取消",
        }

        # Force a layout pass so geometry reflects actual rendered position.
        self.layout().activate()
        self.adjustSize()

        captured["button_top"] = min(b.geometry().top() for b in buttons.values())
        captured["info_bottom"] = info_label.geometry().bottom()
        captured["dialog_size"] = (self.width(), self.height())
        return 0

    monkeypatch.setattr(QDialog, "exec", _exec)

    show_message_box(
        None,
        "后台写作任务正在运行",
        "当前项目仍有后台任务会写入章节或叙事状态。",
        informative_text=(
            "活跃任务：归档前修复（阶段 2/2）·弈心锁玉 / 第 5 章。"
            "为避免旧上下文污染下文，建议先保存为待应用修订。"
        ),
        icon=QMessageBox.Icon.Warning,
        actions=(
            MessageBoxAction(
                key="stage",
                text="保存为待应用修订",
                role=QMessageBox.ButtonRole.AcceptRole,
                variant="primary",
                default=True,
            ),
            MessageBoxAction(
                key="wait",
                text="等待任务完成后应用",
                role=QMessageBox.ButtonRole.AcceptRole,
                variant="secondary",
            ),
            MessageBoxAction(
                key="stop_save",
                text="停止连跑并保存",
                role=QMessageBox.ButtonRole.DestructiveRole,
                variant="danger",
            ),
            MessageBoxAction(
                key="cancel",
                text="取消",
                role=QMessageBox.ButtonRole.RejectRole,
                variant="secondary",
            ),
        ),
        escape_key="cancel",
    )

    info_bottom = captured["info_bottom"]
    button_top = captured["button_top"]
    width, height = captured["dialog_size"]

    assert button_top > info_bottom, (
        f"Button row (top={button_top}) overlaps info label "
        f"(bottom={info_bottom}); dialog size={width}x{height}"
    )


def test_desktop_code_uses_styled_message_box_helpers() -> None:
    desktop_root = Path(__file__).parents[2] / "novel_forge" / "desktop"
    forbidden = (
        "QMessageBox.information(",
        "QMessageBox.warning(",
        "QMessageBox.critical(",
        "QMessageBox.question(",
        "QInputDialog.getText(",
        "QInputDialog.getMultiLineText(",
    )

    offenders: list[str] = []
    for path in desktop_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in forbidden):
            offenders.append(str(path.relative_to(desktop_root.parent.parent)))

    assert offenders == []
