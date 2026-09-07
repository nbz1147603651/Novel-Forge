"""Dialog utilities: styled message boxes and confirmation dialogs.

Visual styling is fully delegated to ``novel_forge.desktop.theme.dialogs``;
this module sets only an ``objectName`` (``"appDialog"``) so the global
QSS can target the dialog.  A 200ms OutCubic fade-in animation
(``DIALOG_FADE_IN_DURATION_MS``) is scheduled via the Motion library
immediately before ``dialog.exec()`` — the animation runs while the
modal event loop blocks.  Opacity animations are safe on macOS (D1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.motion import Motion

# ── Animation constants (task 13 — Dialog visual refresh) ───────────────────
# 200ms OutCubic is the standard modal-enter curve (matches
# ``Motion.DURATIONS["notification"]`` and the new ``Toast.fade_in``).
# Opacity-only animations are safe on macOS (D1).
DIALOG_FADE_IN_DURATION_MS: int = 200


@dataclass
class ConfirmWithCheckboxResult:
    confirmed: bool
    checkbox_checked: bool


class MessageBoxAction(NamedTuple):
    """Declarative button config for styled desktop message boxes."""

    key: str
    text: str
    role: QMessageBox.ButtonRole
    variant: str = "secondary"
    default: bool = False


def _style_message_box_button(button: QPushButton, *, variant: str) -> None:
    button.setObjectName("actionButton")
    button.setProperty("variant", variant)
    button.setProperty("compact", True)
    button.style().unpolish(button)
    button.style().polish(button)


def _apply_dialog_fade_in(dialog: QDialog) -> QPropertyAnimation | None:
    """Schedule a 200ms OutCubic fade-in on *dialog* via the Motion library.

    Wraps :func:`novel_forge.desktop.motion.Motion.fade_in` so the
    animation is created with platform-aware safety guards (D11), and
    additionally stashes the animation as ``dialog._fade_in_anim`` for
    test introspection (the Motion library stores the same animation
    as the ``_motion_anim`` dynamic property).

    Returns the ``QPropertyAnimation`` (or ``None`` if animations are
    disabled on the current platform).
    """
    if not animations_supported():
        return None
    anim = Motion.fade_in(
        dialog,
        duration=DIALOG_FADE_IN_DURATION_MS,
        easing=QEasingCurve.Type.OutCubic,
    )
    # Cache the anim as a direct attribute for testability — Motion
    # already caches it as the ``_motion_anim`` dynamic property and
    # wires ``destroyed → anim.stop`` (D11).
    dialog._fade_in_anim = anim  # type: ignore[attr-defined]
    return anim


def _qmessagebox_to_standard_pixmap(
    icon: QMessageBox.Icon,
) -> QStyle.StandardPixmap | None:
    mapping = {
        QMessageBox.Icon.Information: QStyle.StandardPixmap.SP_MessageBoxInformation,
        QMessageBox.Icon.Warning: QStyle.StandardPixmap.SP_MessageBoxWarning,
        QMessageBox.Icon.Critical: QStyle.StandardPixmap.SP_MessageBoxCritical,
        QMessageBox.Icon.Question: QStyle.StandardPixmap.SP_MessageBoxQuestion,
    }
    return mapping.get(icon)


def show_message_box(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    actions: tuple[MessageBoxAction, ...] | None = None,
    escape_key: str | None = None,
) -> str:
    """Show a styled message dialog and return the clicked action key.

    Uses a custom QDialog (instead of QMessageBox) so text labels wrap
    correctly and never collide with the action button row — a layout
    regression that affected Chinese informative text with >2 buttons.

    A 200ms OutCubic fade-in animation is scheduled immediately before
    ``dialog.exec()`` and runs in parallel with the modal event loop.
    """
    dialog = _build_message_box_dialog(
        parent,
        title,
        text,
        informative_text=informative_text,
        icon=icon,
        actions=actions,
        escape_key=escape_key,
    )
    dialog.exec()
    return getattr(dialog, "_clicked_key", "")


def _build_message_box_dialog(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    actions: tuple[MessageBoxAction, ...] | None = None,
    escape_key: str | None = None,
) -> QDialog:
    """Build a styled message dialog with fade-in animation applied.

    Constructs the same ``QDialog#appDialog`` that :func:`show_message_box`
    uses, but stops short of calling ``dialog.exec()`` so callers (and
    tests) can inspect the dialog before it is shown.

    A 200ms OutCubic fade-in animation is attached to the dialog
    immediately before returning.  When animations are disabled on the
    current platform, the dialog is returned without an effect.
    """
    resolved_actions = actions or (
        MessageBoxAction(
            key="ok",
            text="知道了",
            role=QMessageBox.ButtonRole.AcceptRole,
            variant="primary",
            default=True,
        ),
    )

    dialog = QDialog(parent)
    dialog.setObjectName("appDialog")
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    # Wide enough for 4 long Chinese action labels; vertically adapts to text.
    dialog.setMinimumWidth(440)

    root = QVBoxLayout(dialog)
    root.setContentsMargins(24, 22, 24, 20)
    root.setSpacing(12)

    body_row = QHBoxLayout()
    body_row.setSpacing(12)

    icon_label: QLabel | None = None
    app = QApplication.instance()
    style: QStyle | None = app.style() if isinstance(app, QApplication) else None
    if style is not None and icon != QMessageBox.Icon.NoIcon:
        standard_icon = _qmessagebox_to_standard_pixmap(icon)
        if standard_icon is not None:
            icon_pixmap: QPixmap = style.standardIcon(standard_icon).pixmap(32, 32)
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            icon_label.setObjectName("dialogIcon")

    text_column = QVBoxLayout()
    text_column.setSpacing(6)

    text_label = QLabel(text)
    text_label.setObjectName("dialogText")
    text_label.setWordWrap(True)
    text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    text_column.addWidget(text_label)

    if informative_text:
        info_label = QLabel(informative_text)
        info_label.setObjectName("dialogInformative")
        info_label.setWordWrap(True)
        info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_column.addWidget(info_label)

    if icon_label is not None:
        body_row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
    body_row.addLayout(text_column, 1)
    root.addLayout(body_row)

    # Explicit spacer guarantees the button row never crowds the last text line
    # when the dialog is forced to a fixed width by the parent window.
    root.addSpacing(4)

    button_row = QHBoxLayout()
    button_row.setSpacing(8)
    button_row.addStretch(1)

    buttons: dict[str, QPushButton] = {}
    for action in resolved_actions:
        button = QPushButton(action.text)
        _style_message_box_button(button, variant=action.variant)
        button.setAccessibleName(action.text)
        button.setMinimumHeight(36)
        button_row.addWidget(button)
        buttons[action.key] = button
        if action.default:
            button.setDefault(True)
            button.setAutoDefault(True)

    root.addLayout(button_row)

    dialog.adjustSize()

    escape_button = buttons.get(escape_key) if escape_key else None

    def _done(key: str) -> None:
        dialog._clicked_key = key  # type: ignore[attr-defined]
        dialog.done(1 if key != "cancel" else 0)

    def _on_escape() -> None:
        if escape_button is not None:
            escape_button.click()

    for key, button in buttons.items():
        button.clicked.connect(lambda _checked=False, k=key: _done(k))

    if escape_button is not None:
        original_key_press = dialog.keyPressEvent

        def key_press_event(event):  # type: ignore[no-untyped-def]
            if event.key() == Qt.Key.Key_Escape:
                _on_escape()
                event.accept()
                return
            original_key_press(event)

        dialog.keyPressEvent = key_press_event  # type: ignore[method-assign]

    if not hasattr(dialog, "_clicked_key"):
        dialog._clicked_key = ""  # type: ignore[attr-defined]

    # Apply the fade-in animation AFTER all layout adjustments are
    # complete so the dialog paints at the final size from the first
    # visible frame.  No-op when the platform disables animations.
    _apply_dialog_fade_in(dialog)

    return dialog


def show_info_message(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
) -> None:
    show_message_box(
        parent,
        title,
        text,
        informative_text=informative_text,
        icon=QMessageBox.Icon.Information,
    )


def show_warning_message(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
) -> None:
    show_message_box(
        parent,
        title,
        text,
        informative_text=informative_text,
        icon=QMessageBox.Icon.Warning,
    )


def show_critical_message(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
) -> None:
    show_message_box(
        parent,
        title,
        text,
        informative_text=informative_text,
        icon=QMessageBox.Icon.Critical,
    )


def show_structured_result_dialog(
    parent: QWidget | None,
    title: str,
    *,
    headline: str = "",
    stats: list[tuple[str, str]] | None = None,
    sections: list[tuple[str, list[str]]] | None = None,
    button_text: str = "知道了",
) -> None:
    """Show a resizable, sectioned result dialog for long task summaries."""
    dialog = QDialog(parent)
    dialog.setObjectName("appDialog")
    dialog.setWindowTitle(title)
    dialog.setMinimumSize(860, 560)
    dialog.resize(*smart_dialog_size(dialog, 1120, 720))
    dialog.setSizeGripEnabled(True)
    dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

    root = QVBoxLayout(dialog)
    root.setContentsMargins(24, 22, 24, 20)
    root.setSpacing(14)

    title_label = QLabel(title)
    title_label.setObjectName("dialogTitle")
    title_label.setWordWrap(True)
    root.addWidget(title_label)

    if headline:
        headline_label = QLabel(headline)
        headline_label.setObjectName("dialogText")
        headline_label.setWordWrap(True)
        headline_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(headline_label)

    clean_stats = [(str(k).strip(), str(v).strip()) for k, v in (stats or []) if str(v).strip()]
    if clean_stats:
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, (label, value) in enumerate(clean_stats[:8]):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(4)
            value_label = QLabel(value)
            value_label.setObjectName("metricValue")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label_widget = QLabel(label)
            label_widget.setObjectName("metricLabel")
            label_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card_layout.addWidget(value_label)
            card_layout.addWidget(label_widget)
            grid.addWidget(card, index // 4, index % 4)
        root.addLayout(grid)

    tab_widget = QTabWidget()
    tab_widget.setObjectName("resultTabs")
    tab_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    clean_sections = [
        (
            str(name).strip() or f"详情 {index + 1}",
            [str(line) for line in lines if str(line).strip()],
        )
        for index, (name, lines) in enumerate(sections or [])
    ]
    if not clean_sections:
        clean_sections = [("详情", ["无更多详情。"])]
    for section_name, lines in clean_sections:
        text = QPlainTextEdit()
        text.setObjectName("resultDetailText")
        text.setReadOnly(True)
        text.setPlainText("\n".join(lines) if lines else "无更多详情。")
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tab_widget.addTab(text, section_name)
    root.addWidget(tab_widget, 1)

    buttons = QDialogButtonBox()
    ok_button = buttons.addButton(button_text, QDialogButtonBox.ButtonRole.AcceptRole)
    _style_message_box_button(ok_button, variant="primary")
    buttons.accepted.connect(dialog.accept)
    root.addWidget(buttons)

    dialog.exec()


def ask_confirmation(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    informative_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    confirm_variant: str = "primary",
    escape_key: str = "cancel",
) -> bool:
    return (
        show_message_box(
            parent,
            title,
            text,
            informative_text=informative_text,
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    key="confirm",
                    text=confirm_text,
                    role=QMessageBox.ButtonRole.AcceptRole,
                    variant=confirm_variant,
                    default=True,
                ),
                MessageBoxAction(
                    key="cancel",
                    text=cancel_text,
                    role=QMessageBox.ButtonRole.RejectRole,
                    variant="secondary",
                ),
            ),
            escape_key=escape_key,
        )
        == "confirm"
    )


def show_text_input_dialog(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    initial_text: str = "",
    placeholder_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    require_text: bool = True,
) -> tuple[str, bool]:
    """Show a styled single-line text input dialog.

    This replaces native ``QInputDialog`` prompts so small workflow popups keep
    the same surface, typography, and action-button styling as the rest of the
    desktop app.
    """
    dialog = QDialog(parent)
    dialog.setObjectName("appDialog")
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(420)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(12)

    text_label = QLabel(label)
    text_label.setWordWrap(True)
    text_label.setObjectName("dialogText")
    layout.addWidget(text_label)

    input_box = QLineEdit()
    input_box.setObjectName("dialogInput")
    input_box.setText(initial_text)
    input_box.setPlaceholderText(placeholder_text)
    input_box.setClearButtonEnabled(True)
    input_box.setMinimumHeight(38)
    input_box.setAccessibleName(label)
    layout.addWidget(input_box)

    button_layout = QHBoxLayout()
    button_layout.addStretch()

    cancel_btn = QPushButton(cancel_text)
    _style_message_box_button(cancel_btn, variant="secondary")
    cancel_btn.setAccessibleName(cancel_text)
    cancel_btn.clicked.connect(dialog.reject)
    button_layout.addWidget(cancel_btn)

    confirm_btn = QPushButton(confirm_text)
    _style_message_box_button(confirm_btn, variant="primary")
    confirm_btn.setAccessibleName(confirm_text)
    confirm_btn.clicked.connect(dialog.accept)
    button_layout.addWidget(confirm_btn)

    def _sync_confirm_enabled() -> None:
        confirm_btn.setEnabled((not require_text) or bool(input_box.text().strip()))

    input_box.textChanged.connect(lambda _text: _sync_confirm_enabled())
    input_box.returnPressed.connect(lambda: dialog.accept() if confirm_btn.isEnabled() else None)
    _sync_confirm_enabled()

    layout.addLayout(button_layout)
    input_box.setFocus()
    input_box.selectAll()

    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return input_box.text(), accepted


def show_multiline_input_dialog(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    heading: str = "",
    initial_text: str = "",
    placeholder_text: str = "",
    helper_text: str = "",
    context_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    reset_text: str = "",
    require_text: bool = True,
) -> tuple[str, bool]:
    """Show a responsive, styled editor for reviewing generated text.

    The dialog deliberately avoids native ``QInputDialog`` behavior: long
    Chinese text wraps inside the editor, the source context remains visible,
    destructive select-all is avoided, and actions use product terminology.
    ``Ctrl+Enter`` submits while ``Escape`` cancels through standard dialog
    behavior.
    """
    dialog = QDialog(parent)
    dialog.setObjectName("appDialog")
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumSize(560, 400)
    dialog.resize(*smart_dialog_size(parent or dialog, 700, 520, max_screen_fraction=0.78))
    dialog.setSizeGripEnabled(True)
    dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(26, 24, 26, 22)
    layout.setSpacing(10)

    title_label = QLabel(heading or title)
    title_label.setObjectName("dialogTitle")
    title_label.setWordWrap(True)
    layout.addWidget(title_label)

    if context_text:
        context_label = QLabel(context_text)
        context_label.setObjectName("dialogContext")
        context_label.setWordWrap(True)
        context_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(context_label)

    text_label = QLabel(label)
    text_label.setWordWrap(True)
    text_label.setObjectName("dialogText")
    text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(text_label)

    if helper_text:
        helper_label = QLabel(helper_text)
        helper_label.setObjectName("dialogHelper")
        helper_label.setWordWrap(True)
        layout.addWidget(helper_label)

    text_box = QPlainTextEdit()
    text_box.setObjectName("dialogTextInput")
    text_box.setPlainText(initial_text)
    text_box.setPlaceholderText(placeholder_text)
    text_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    text_box.setTabChangesFocus(True)
    text_box.setMinimumHeight(220)
    text_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    text_box.setAccessibleName(label)
    text_box.setAccessibleDescription(helper_text or label)
    cursor = text_box.textCursor()
    cursor.clearSelection()
    cursor.setPosition(0)
    text_box.setTextCursor(cursor)
    layout.addWidget(text_box, 1)

    footer = QHBoxLayout()
    footer.setSpacing(8)

    counter = QLabel()
    counter.setObjectName("dialogCounter")
    counter.setAccessibleName("当前简报字数")
    footer.addWidget(counter)

    reset_btn: QPushButton | None = None
    if reset_text and initial_text:
        reset_btn = QPushButton(reset_text)
        _style_message_box_button(reset_btn, variant="secondary")
        reset_btn.setAccessibleName(reset_text)
        reset_btn.setToolTip("恢复打开弹窗时的上游生成内容")
        footer.addWidget(reset_btn)

    footer.addStretch(1)

    cancel_btn = QPushButton(cancel_text)
    _style_message_box_button(cancel_btn, variant="secondary")
    cancel_btn.setAccessibleName(cancel_text)
    cancel_btn.clicked.connect(dialog.reject)
    footer.addWidget(cancel_btn)

    confirm_btn = QPushButton(confirm_text)
    _style_message_box_button(confirm_btn, variant="primary")
    confirm_btn.setAccessibleName(confirm_text)
    confirm_btn.setDefault(True)
    confirm_btn.setAutoDefault(True)
    confirm_btn.clicked.connect(dialog.accept)
    footer.addWidget(confirm_btn)
    layout.addLayout(footer)

    def _sync_state() -> None:
        content = text_box.toPlainText()
        counter.setText(f"{len(content.strip())} 字")
        confirm_btn.setEnabled((not require_text) or bool(content.strip()))

    text_box.textChanged.connect(_sync_state)
    if reset_btn is not None:
        reset_btn.clicked.connect(lambda: text_box.setPlainText(initial_text))

    submit_shortcut = QShortcut(QKeySequence("Ctrl+Return"), dialog)
    submit_shortcut.activated.connect(lambda: dialog.accept() if confirm_btn.isEnabled() else None)
    enter_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), dialog)
    enter_shortcut.activated.connect(lambda: dialog.accept() if confirm_btn.isEnabled() else None)
    command_submit_shortcut = QShortcut(QKeySequence("Meta+Return"), dialog)
    command_submit_shortcut.activated.connect(
        lambda: dialog.accept() if confirm_btn.isEnabled() else None
    )
    command_enter_shortcut = QShortcut(QKeySequence("Meta+Enter"), dialog)
    command_enter_shortcut.activated.connect(
        lambda: dialog.accept() if confirm_btn.isEnabled() else None
    )

    _sync_state()
    text_box.setFocus()
    _apply_dialog_fade_in(dialog)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return text_box.toPlainText(), accepted


def ask_confirmation_with_checkbox(
    parent: QWidget | None,
    title: str,
    text: str,
    checkbox_label: str,
    *,
    informative_text: str = "",
    confirm_text: str = "确定",
    cancel_text: str = "取消",
    confirm_variant: str = "primary",
    checkbox_default: bool = False,
) -> ConfirmWithCheckboxResult:
    """Show a confirmation dialog with a checkbox option.

    Returns a ConfirmWithCheckboxResult with confirmed=True if the user confirmed,
    and checkbox_checked indicating the checkbox state.
    """
    dialog = QDialog(parent)
    dialog.setObjectName("appDialog")
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(380)

    layout = QVBoxLayout(dialog)
    layout.setSpacing(12)

    text_label = QLabel(text)
    text_label.setWordWrap(True)
    text_label.setObjectName("dialogText")
    layout.addWidget(text_label)

    if informative_text:
        info_label = QLabel(informative_text)
        info_label.setWordWrap(True)
        info_label.setObjectName("dialogInformative")
        layout.addWidget(info_label)

    checkbox = QCheckBox(checkbox_label)
    checkbox.setChecked(checkbox_default)
    checkbox.setAccessibleName(checkbox_label)
    layout.addWidget(checkbox)

    layout.addSpacing(8)

    button_layout = QHBoxLayout()
    button_layout.addStretch()

    cancel_btn = QPushButton(cancel_text)
    _style_message_box_button(cancel_btn, variant="secondary")
    cancel_btn.setAccessibleName(cancel_text)
    cancel_btn.clicked.connect(lambda: dialog.done(0))
    button_layout.addWidget(cancel_btn)

    confirm_btn = QPushButton(confirm_text)
    _style_message_box_button(confirm_btn, variant=confirm_variant)
    confirm_btn.setAccessibleName(confirm_text)
    confirm_btn.clicked.connect(lambda: dialog.done(1))
    button_layout.addWidget(confirm_btn)

    layout.addLayout(button_layout)

    dialog.setLayout(layout)

    confirm_btn.setFocus()
    dialog.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    result = dialog.exec()

    return ConfirmWithCheckboxResult(
        confirmed=(result == 1),
        checkbox_checked=checkbox.isChecked(),
    )
