"""Multi-candidate voice A/B preview panel (P1).

A standalone dialog embedded into the Voice Studio's first tab: picks one
character, deterministically selects up to 3 system-voice candidates, batch
synthesizes the same sample text with each candidate (streaming when the
provider supports it), plays them back side-by-side with ``DubbingPlayerWidget``,
and persists the author's selection back into the voice team.

Aligned with Reference/audiobook ``preview_data.json``: each candidate exposes
voiceId / voiceName / description / samplePath; the confirmed choice carries
speed + volume adjustments consumed by formal synthesis.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
from novel_forge.desktop.components.forms import SettingRow
from novel_forge.desktop.pages.voice_studio.workers import (
    ConfirmVoicePreviewWorker,
    VoicePreviewWorker,
)
from novel_forge.tts.services.voice_preview import _sample_text_for_character


class _CandidateCard(QFrame):
    """One selectable candidate with its own player."""

    def __init__(self, candidate: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.candidate = candidate
        self.setObjectName("voicePreviewCandidateCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.radio = QRadioButton()
        head.addWidget(self.radio)
        title = QLabel(str(candidate.get("voice_name") or candidate.get("voice_id") or "候选音色"))
        title.setObjectName("voicePreviewCandidateName")
        head.addWidget(title, 1)
        reasons = candidate.get("match_reasons") or []
        if reasons:
            reason_label = QLabel(" · ".join(str(item) for item in reasons))
            reason_label.setObjectName("voicePreviewCandidateReasons")
            head.addWidget(reason_label)
        layout.addLayout(head)

        description = str(candidate.get("description") or "").strip()
        if description:
            desc = QLabel(description)
            desc.setWordWrap(True)
            desc.setObjectName("voicePreviewCandidateDescription")
            layout.addWidget(desc)

        self.error_label: QLabel | None = None
        error = str(candidate.get("error") or "").strip()
        if error:
            self.error_label = QLabel(f"生成失败：{error}")
            self.error_label.setWordWrap(True)
            self.error_label.setObjectName("voicePreviewCandidateError")
            layout.addWidget(self.error_label)

        self.player = DubbingPlayerWidget(compact=True)
        self.player.setObjectName("voicePreviewCandidatePlayer")
        self.player.set_context_visible(False)
        layout.addWidget(self.player)

        sample_path = str(candidate.get("sample_path") or "")
        if sample_path:
            self.player.load_audio(sample_path)


class VoicePreviewDialog(QDialog):
    """Author-facing A/B candidate comparison for one character's voice."""

    voice_team_updated = Signal(dict)
    worker_failed = Signal(dict)

    def __init__(
        self,
        *,
        settings: Any,
        layout: Any,
        characters: list[dict[str, Any]],
        provider: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._characters = characters or []
        self._current_worker: Any = None
        self._plan: dict[str, Any] | None = None
        self._cards: list[_CandidateCard] = []
        self._radio_group: QButtonGroup | None = None

        self.setWindowTitle("音色候选 A/B 对比")
        self.resize(860, 620)
        self._build_ui()
        self._populate_character_combo()

    # ─── UI 构建 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._character_combo = QComboBox()
        self._character_combo.currentIndexChanged.connect(self._on_character_changed)
        character_row = SettingRow("角色", "选择要试听的角色（来自角色圣经）")
        character_row.set_input(self._character_combo)
        layout.addWidget(character_row)

        self._sample_edit = QTextEdit()
        self._sample_edit.setFixedHeight(64)
        self._sample_edit.setPlaceholderText("全部候选共用这段试听文本（约 10 秒）")
        sample_row = SettingRow("试听文本", "所有候选使用同一段文本，保证 A/B 可比")
        sample_row.set_input(self._sample_edit)
        layout.addWidget(sample_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._streaming_check = QCheckBox("流式生成（边生成边接收，低首包延迟）")
        self._streaming_check.setChecked(True)
        self._streaming_check.setToolTip(
            "MiniMax speech-2.8 支持 WebSocket 流式合成；其他平台自动回退普通合成"
        )
        controls.addWidget(self._streaming_check)
        controls.addStretch(1)
        self._generate_btn = _action_button("生成候选试听", primary=True)
        self._generate_btn.clicked.connect(self._on_generate)
        controls.addWidget(self._generate_btn)
        layout.addLayout(controls)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._status_label = QLabel("选择角色后点击「生成候选试听」，将为每个候选合成同一段文本。")
        self._status_label.setObjectName("voicePreviewStatus")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._candidates_host = QWidget()
        self._candidates_layout = QVBoxLayout(self._candidates_host)
        self._candidates_layout.setContentsMargins(0, 0, 0, 0)
        self._candidates_layout.setSpacing(6)
        self._candidates_layout.addStretch(1)
        scroll.setWidget(self._candidates_host)
        layout.addWidget(scroll, 1)

        param_row = QHBoxLayout()
        param_row.setSpacing(8)
        param_row.addWidget(QLabel("语速"))
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.5, 2.0)
        self._speed_spin.setSingleStep(0.05)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setValue(float(self._settings.tts_default_speed))
        self._speed_spin.setToolTip("确认后写入该角色的语速倍率（偏移 = 倍率 - 默认语速）")
        param_row.addWidget(self._speed_spin)
        param_row.addWidget(QLabel("音量"))
        self._volume_spin = QDoubleSpinBox()
        self._volume_spin.setRange(0.0, 2.0)
        self._volume_spin.setSingleStep(0.05)
        self._volume_spin.setDecimals(2)
        self._volume_spin.setValue(1.0)
        self._volume_spin.setToolTip("确认后写入该角色的音量倍率（偏移 = 倍率 - 1.0）")
        param_row.addWidget(self._volume_spin)
        param_row.addStretch(1)
        self._confirm_btn = _action_button("确认选用此音色")
        self._confirm_btn.setToolTip("将选中的候选写回配音团队，后续正式合成直接消费")
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._on_confirm)
        param_row.addWidget(self._confirm_btn)
        layout.addLayout(param_row)

    def _populate_character_combo(self) -> None:
        self._character_combo.blockSignals(True)
        self._character_combo.clear()
        for character in self._characters:
            cid = str(character.get("character_id") or character.get("id") or "").strip()
            name = str(character.get("name") or character.get("character_name") or "").strip()
            if not cid:
                continue
            self._character_combo.addItem(name or cid, character)
        self._character_combo.blockSignals(False)
        if self._character_combo.count() == 0:
            self._status_label.setText("角色圣经中暂无可用角色，请先创建角色。")
            self._generate_btn.setEnabled(False)
        else:
            self._on_character_changed(0)

    # ─── 交互 ───────────────────────────────────────────────────────────────

    def _on_character_changed(self, _index: int) -> None:
        character = self._character_combo.currentData()
        if not character:
            return
        name = str(character.get("name") or "").strip() or "角色"
        if not self._sample_edit.toPlainText().strip():
            self._sample_edit.setPlainText(_sample_text_for_character(name))

    def _selected_character(self) -> dict[str, Any] | None:
        character = self._character_combo.currentData()
        if not character:
            return None
        cid = str(character.get("character_id") or character.get("id") or "").strip()
        if not cid:
            return None
        return {
            "character_id": cid,
            "character_name": str(character.get("name") or cid),
            "name": str(character.get("name") or cid),
            "gender": str(character.get("gender") or ""),
            "age_hint": str(character.get("age") or character.get("age_hint") or ""),
            "personality": str(character.get("personality") or ""),
            "description": str(character.get("description") or character.get("voice") or ""),
        }

    def _on_generate(self) -> None:
        character = self._selected_character()
        if character is None or self._current_worker is not None:
            return
        sample_text = self._sample_edit.toPlainText().strip()
        if not sample_text:
            self._status_label.setText("请先填写试听文本。")
            return
        self._clear_cards()
        self._generate_btn.setEnabled(False)
        self._confirm_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_label.setText("正在挑选候选音色并批量合成试听…")

        worker = VoicePreviewWorker(
            project_id="desktop",
            characters=[character],
            settings=self._settings,
            layout=self._layout,
            provider=self._provider,
            sample_text=sample_text,
            streaming=self._streaming_check.isChecked(),
            candidate_count=3,
        )
        worker.signals.preview_progress.connect(self._on_preview_progress)
        worker.signals.preview_chunk.connect(self._on_preview_chunk)
        worker.signals.preview_plan_ready.connect(self._on_preview_plan_ready)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        worker.signals.worker_finished.connect(self._on_worker_finished)
        self._current_worker = worker
        worker.submit()

    def _on_preview_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setValue(round(done * 100 / total))
        self._status_label.setText(f"已合成 {done}/{total} 个候选…")

    def _on_preview_chunk(self, received_bytes: int) -> None:
        size_kb = received_bytes / 1024
        self._status_label.setText(f"流式接收中… 已收到 {size_kb:.0f} KB")

    def _on_preview_plan_ready(self, plan: dict[str, Any]) -> None:
        self._plan = plan
        self._clear_cards()
        candidates = plan.get("candidates") or []
        if not candidates:
            self._status_label.setText("未找到可用候选音色，请检查平台音色目录与角色画像。")
            return
        self._radio_group = QButtonGroup(self)
        for candidate in candidates:
            card = _CandidateCard(candidate)
            self._candidates_layout.insertWidget(self._candidates_layout.count() - 1, card)
            self._cards.append(card)
            self._radio_group.addButton(card.radio)
        if self._cards:
            self._cards[0].radio.setChecked(True)
            self._sync_confirm_state()
        total = len(candidates)
        failed = sum(1 for c in candidates if c.get("error"))
        self._status_label.setText(
            f"已生成 {total - failed}/{total} 个候选试听，点击左侧圆点选择后进行 A/B 对比。"
            + ("（有候选生成失败，可检查平台状态后重试）" if failed else "")
        )

    def _sync_confirm_state(self) -> None:
        selected = self._selected_candidate()
        self._confirm_btn.setEnabled(
            selected is not None and bool(selected.get("sample_path")) and not selected.get("error")
        )

    def _selected_candidate(self) -> dict[str, Any] | None:
        if self._radio_group is None:
            return None
        checked = self._radio_group.checkedButton()
        if checked is None:
            return None
        for card in self._cards:
            if card.radio is checked:
                return card.candidate
        return None

    def _on_confirm(self) -> None:
        candidate = self._selected_candidate()
        character = self._selected_character()
        if candidate is None or character is None:
            return
        if self._current_worker is not None:
            return
        self._confirm_btn.setEnabled(False)
        self._status_label.setText("正在写入配音团队…")
        worker = ConfirmVoicePreviewWorker(
            project_id="desktop",
            settings=self._settings,
            layout=self._layout,
            character_id=character["character_id"],
            voice_id=str(candidate.get("voice_id") or ""),
            speed=float(self._speed_spin.value()),
            volume=float(self._volume_spin.value()),
            provider=self._provider,
            sample_text=str((self._plan or {}).get("sample_text") or self._sample_edit.toPlainText()),
            sample_path=str(candidate.get("sample_path") or ""),
        )
        worker.signals.voice_team_updated.connect(self._on_team_confirmed)
        worker.signals.worker_failed.connect(self._on_worker_failed)
        worker.signals.worker_finished.connect(self._on_worker_finished)
        self._current_worker = worker
        worker.submit()

    def _on_team_confirmed(self, team: dict[str, Any]) -> None:
        self._status_label.setText("已确认音色并写入配音团队。")
        self.voice_team_updated.emit(team)

    def _on_worker_failed(self, _worker_id: str, payload: dict[str, Any]) -> None:
        self.worker_failed.emit(payload)

    def _on_worker_finished(self, _worker_id: str) -> None:
        self._current_worker = None
        self._generate_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._sync_confirm_state()

    def _clear_cards(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        self._radio_group = None

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self._current_worker is not None:
            self._current_worker.cancel()
            self._current_worker = None
        for card in self._cards:
            card.player.stop()
        super().closeEvent(event)


def _action_button(text: str, *, primary: bool = False) -> Any:
    from novel_forge.desktop.widgets import ActionButton

    return ActionButton(text, variant="primary" if primary else "secondary")
