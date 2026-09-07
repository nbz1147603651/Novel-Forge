"""Voice Studio automation-mode selector."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSizePolicy,
    QWidget,
)

from novel_forge.tts.services.automation import AudioAutomationMode


class AudioAutomationModeSelector(QWidget):
    """Compact three-state selector shared by the post-production controls."""

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # This selector sits inside the post-production command bar.  Keep it
        # intrinsically compact so spare toolbar width benefits the work panes
        # instead of being distributed between three radio buttons.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(285)
        self.setMaximumWidth(380)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel("推进模式")
        label.setObjectName("cardMeta")
        layout.addWidget(label)

        self._group = QButtonGroup(self)
        buttons = (
            (
                AudioAutomationMode.MANUAL,
                "全人工",
                "只执行作者明确点击的步骤，不自动创建设计、脚本或声音素材。",
            ),
            (
                AudioAutomationMode.ASSISTED,
                "AI 伴随",
                "AI 设计并生成候选；声音素材需作者试听批准后才进入正式混音。",
            ),
            (
                AudioAutomationMode.AUTONOMOUS,
                "AI 自主",
                "AI 自动完成设计、生成、批准、混音和质检；未通过交付门时不会假报完成。",
            ),
        )
        self._buttons: dict[AudioAutomationMode, QRadioButton] = {}
        for button_id, (mode, text, tooltip) in enumerate(buttons):
            button = QRadioButton(text)
            button.setToolTip(tooltip)
            button.setAccessibleName(f"Audio automation: {text}")
            self._group.addButton(button, button_id)
            self._buttons[mode] = button
            layout.addWidget(button)
        self._buttons[AudioAutomationMode.ASSISTED].setChecked(True)
        self._group.idToggled.connect(self._emit_mode)

    def _emit_mode(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        modes = (
            AudioAutomationMode.MANUAL,
            AudioAutomationMode.ASSISTED,
            AudioAutomationMode.AUTONOMOUS,
        )
        if 0 <= button_id < len(modes):
            self.mode_changed.emit(modes[button_id].value)

    def current_mode(self) -> AudioAutomationMode:
        for mode, button in self._buttons.items():
            if button.isChecked():
                return mode
        return AudioAutomationMode.ASSISTED

    def set_mode(self, mode: AudioAutomationMode | str) -> None:
        try:
            resolved = mode if isinstance(mode, AudioAutomationMode) else AudioAutomationMode(mode)
        except ValueError:
            resolved = AudioAutomationMode.ASSISTED
        button = self._buttons[resolved]
        if not button.isChecked():
            button.setChecked(True)


__all__ = ["AudioAutomationModeSelector"]
