"""Authoring dialog for chapter ambience, music, and sound-effect cues."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.widgets import ActionButton, Badge
from novel_forge.tts.schemas import BGMTiming, DubbingScript, SFXCue, SoundscapeCue

CueKind = Literal["soundscape", "bgm", "sfx"]


class SoundDesignEditorDialog(QDialog):
    """Edit sound cues against stable script-segment anchors.

    Segment anchors deliberately remain the authoring source of truth.  The
    production pipeline resolves them to measured millisecond positions after
    speech synthesis, so a narration-speed change cannot silently desynchronise
    an effect or music transition.
    """

    def __init__(
        self,
        script: DubbingScript,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle(f"编辑第 {script.chapter_number} 章声场设计")
        self.resize(*smart_dialog_size(parent or self, 1040, 720, max_screen_fraction=0.84))

        self._script = script.model_copy(deep=True)
        self._soundscapes = [cue.model_copy(deep=True) for cue in script.soundscapes]
        self._bgm = [cue.model_copy(deep=True) for cue in script.bgm_suggestions]
        self._sfx = [cue.model_copy(deep=True) for cue in script.sfx_cues]
        self._current_kind: CueKind | None = None
        self._current_index = -1
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("声场与混音设计")
        title.setObjectName("voiceDialogTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self._count_badge = Badge("0 项声音线索", tone="muted")
        title_row.addWidget(self._count_badge)
        layout.addLayout(title_row)

        description = QLabel(
            "环境音和 BGM 使用起止片段锚点，音效使用触发片段与毫秒偏移。"
            "正式合成后，系统会按真实语音时长换算到时间轴，并自动在人声下压背景轨。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        content = QHBoxLayout()
        content.setSpacing(12)
        left = QVBoxLayout()
        self._cue_list = QListWidget()
        self._cue_list.setObjectName("voiceSoundCueList")
        self._cue_list.setAccessibleName("声场与混音线索列表")
        self._cue_list.setMinimumWidth(280)
        left.addWidget(self._cue_list, 1)

        add_row = QHBoxLayout()
        for label, kind in (
            ("新增环境", "soundscape"),
            ("新增 BGM", "bgm"),
            ("新增音效", "sfx"),
        ):
            button = ActionButton(label, variant="secondary")
            button.clicked.connect(lambda _checked=False, value=kind: self._add_cue(value))
            add_row.addWidget(button)
        left.addLayout(add_row)
        self._delete_btn = ActionButton("删除当前线索", variant="danger")
        self._delete_btn.clicked.connect(self._delete_current)
        left.addWidget(self._delete_btn)

        left_widget = QWidget()
        left_widget.setLayout(left)
        content.addWidget(left_widget, 2)

        editor = QWidget()
        form = QGridLayout(editor)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self._kind_badge = Badge("选择声音线索", tone="muted")
        form.addWidget(self._kind_badge, 0, 0, 1, 4)

        form.addWidget(QLabel("名称 / 曲目"), 1, 0)
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("例如：雨夜街道、低频悬疑铺底、玻璃轻响")
        form.addWidget(self._name_input, 1, 1, 1, 3)

        form.addWidget(QLabel("听觉与剧情意图"), 2, 0)
        self._description_input = QTextEdit()
        self._description_input.setAcceptRichText(False)
        self._description_input.setMaximumHeight(92)
        self._description_input.setPlaceholderText("说明声音为什么出现，以及希望听众注意到什么…")
        form.addWidget(self._description_input, 2, 1, 1, 3)

        self._start_label = QLabel("开始片段")
        form.addWidget(self._start_label, 3, 0)
        self._start_combo = QComboBox()
        form.addWidget(self._start_combo, 3, 1)
        self._end_label = QLabel("结束片段")
        form.addWidget(self._end_label, 3, 2)
        self._end_combo = QComboBox()
        self._end_combo.addItem("延续至章节末尾", None)
        form.addWidget(self._end_combo, 3, 3)

        self._offset_label = QLabel("触发偏移")
        form.addWidget(self._offset_label, 4, 0)
        self._offset_spin = QSpinBox()
        self._offset_spin.setRange(-30_000, 30_000)
        self._offset_spin.setSingleStep(100)
        self._offset_spin.setSuffix(" ms")
        form.addWidget(self._offset_spin, 4, 1)
        self._duration_label = QLabel("音效时长")
        form.addWidget(self._duration_label, 4, 2)
        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(0, 120_000)
        self._duration_spin.setSingleStep(100)
        self._duration_spin.setSuffix(" ms")
        form.addWidget(self._duration_spin, 4, 3)

        form.addWidget(QLabel("轨道音量"), 5, 0)
        self._volume_spin = QDoubleSpinBox()
        self._volume_spin.setRange(0.0, 100.0)
        self._volume_spin.setSingleStep(1.0)
        self._volume_spin.setSuffix(" %")
        form.addWidget(self._volume_spin, 5, 1)
        self._duck_label = QLabel("对白下压")
        form.addWidget(self._duck_label, 5, 2)
        self._duck_spin = QDoubleSpinBox()
        self._duck_spin.setRange(0.0, 30.0)
        self._duck_spin.setSingleStep(0.5)
        self._duck_spin.setSuffix(" dB")
        form.addWidget(self._duck_spin, 5, 3)

        self._fade_in_label = QLabel("淡入")
        form.addWidget(self._fade_in_label, 6, 0)
        self._fade_in_spin = QSpinBox()
        self._fade_in_spin.setRange(0, 10_000)
        self._fade_in_spin.setSingleStep(100)
        self._fade_in_spin.setSuffix(" ms")
        form.addWidget(self._fade_in_spin, 6, 1)
        self._fade_out_label = QLabel("淡出")
        form.addWidget(self._fade_out_label, 6, 2)
        self._fade_out_spin = QSpinBox()
        self._fade_out_spin.setRange(0, 10_000)
        self._fade_out_spin.setSingleStep(100)
        self._fade_out_spin.setSuffix(" ms")
        form.addWidget(self._fade_out_spin, 6, 3)

        self._loop_check = QCheckBox("素材不足时循环铺满区间")
        form.addWidget(self._loop_check, 7, 0, 1, 2)
        self._overlap_check = QCheckBox("允许音效与对白同时发生")
        self._overlap_check.setToolTip("仅在剧情必须同步发生时开启；否则系统会尝试移动到最近的对白空隙。")
        form.addWidget(self._overlap_check, 7, 2, 1, 2)

        self._anchor_hint = QLabel()
        self._anchor_hint.setObjectName("voiceSoundAnchorHint")
        self._anchor_hint.setWordWrap(True)
        form.addWidget(self._anchor_hint, 8, 0, 1, 4)
        form.setRowStretch(9, 1)
        content.addWidget(editor, 4)
        layout.addLayout(content, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = ActionButton("取消", variant="quiet")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = ActionButton("保存声场设计", variant="primary")
        save.clicked.connect(self._save)
        footer.addWidget(save)
        layout.addLayout(footer)

        for segment in script.segments:
            preview = " ".join(segment.text.split())
            if len(preview) > 24:
                preview = f"{preview[:24]}…"
            label = f"第 {segment.segment_index + 1} 段 · {preview}"
            self._start_combo.addItem(label, segment.segment_index)
            self._end_combo.addItem(label, segment.segment_index)

        self._cue_list.currentRowChanged.connect(self._on_row_changed)
        self._populate_list()
        if self._cue_list.count():
            self._cue_list.setCurrentRow(0)
        else:
            self._set_form_enabled(False)

    def _cue_refs(self) -> list[tuple[CueKind, int, object]]:
        refs: list[tuple[CueKind, int, object]] = []
        refs.extend(("soundscape", index, cue) for index, cue in enumerate(self._soundscapes))
        refs.extend(("bgm", index, cue) for index, cue in enumerate(self._bgm))
        refs.extend(("sfx", index, cue) for index, cue in enumerate(self._sfx))
        return refs

    def _populate_list(self, *, select: tuple[CueKind, int] | None = None) -> None:
        self._cue_list.blockSignals(True)
        self._cue_list.clear()
        selected_row = -1
        labels = {"soundscape": "环境", "bgm": "BGM", "sfx": "音效"}
        for row, (kind, index, cue) in enumerate(self._cue_refs()):
            if kind == "soundscape":
                name = cue.name
            elif kind == "bgm":
                name = cue.track_name or cue.mood or "背景音乐"
            else:
                name = cue.effect_name
            item = QListWidgetItem(f"{labels[kind]} · {name}")
            item.setData(Qt.ItemDataRole.UserRole, (kind, index))
            self._cue_list.addItem(item)
            if select == (kind, index):
                selected_row = row
        self._cue_list.blockSignals(False)
        count = self._cue_list.count()
        self._count_badge.setText(f"{count} 项声音线索")
        self._count_badge.set_tone("default" if count else "muted")
        if selected_row >= 0:
            self._cue_list.setCurrentRow(selected_row)

    def _current_cue(self) -> object | None:
        if self._current_kind == "soundscape" and 0 <= self._current_index < len(self._soundscapes):
            return self._soundscapes[self._current_index]
        if self._current_kind == "bgm" and 0 <= self._current_index < len(self._bgm):
            return self._bgm[self._current_index]
        if self._current_kind == "sfx" and 0 <= self._current_index < len(self._sfx):
            return self._sfx[self._current_index]
        return None

    def _on_row_changed(self, row: int) -> None:
        if self._loading:
            return
        if self._current_kind is not None and not self._commit_current():
            return
        item = self._cue_list.item(row)
        payload = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(payload, tuple) or len(payload) != 2:
            self._current_kind = None
            self._current_index = -1
            self._set_form_enabled(False)
            return
        self._current_kind = payload[0]
        self._current_index = int(payload[1])
        self._load_current()

    def _load_current(self) -> None:
        cue = self._current_cue()
        if cue is None or self._current_kind is None:
            self._set_form_enabled(False)
            return
        self._loading = True
        try:
            self._set_form_enabled(True)
            if self._current_kind == "soundscape":
                self._kind_badge.setText("环境声轨")
                self._kind_badge.set_tone("default")
                self._name_input.setText(cue.name)
                self._description_input.setPlainText(cue.description)
                start, end = cue.start_segment_index, cue.end_segment_index
                self._volume_spin.setValue(cue.volume * 100)
                self._duck_spin.setValue(cue.ducking_db)
                self._fade_in_spin.setValue(cue.fade_in_ms)
                self._fade_out_spin.setValue(cue.fade_out_ms)
                self._loop_check.setChecked(cue.loop)
            elif self._current_kind == "bgm":
                self._kind_badge.setText("背景音乐轨")
                self._kind_badge.set_tone("default")
                self._name_input.setText(cue.track_name or cue.mood)
                self._description_input.setPlainText(cue.mood)
                start, end = cue.start_segment_index, cue.end_segment_index
                self._volume_spin.setValue(cue.volume * 100)
                self._duck_spin.setValue(cue.ducking_db)
                self._fade_in_spin.setValue(cue.fade_in_ms)
                self._fade_out_spin.setValue(cue.fade_out_ms)
                self._loop_check.setChecked(cue.loop)
            else:
                self._kind_badge.setText("剧情音效点")
                self._kind_badge.set_tone("default")
                self._name_input.setText(cue.effect_name)
                self._description_input.setPlainText(cue.description)
                start, end = cue.trigger_segment_index, None
                self._volume_spin.setValue(cue.volume * 100)
                self._offset_spin.setValue(cue.offset_ms)
                self._duration_spin.setValue(cue.duration_ms)
                self._overlap_check.setChecked(cue.allow_dialogue_overlap)
            self._start_combo.setCurrentIndex(max(self._start_combo.findData(start), 0))
            self._end_combo.setCurrentIndex(max(self._end_combo.findData(end), 0))
            self._update_field_visibility()
        finally:
            self._loading = False

    def _update_field_visibility(self) -> None:
        is_sfx = self._current_kind == "sfx"
        for widget in (self._end_label, self._end_combo, self._duck_label, self._duck_spin,
                       self._fade_in_label, self._fade_in_spin, self._fade_out_label,
                       self._fade_out_spin, self._loop_check):
            widget.setVisible(not is_sfx)
        for widget in (self._offset_label, self._offset_spin, self._duration_label,
                       self._duration_spin, self._overlap_check):
            widget.setVisible(is_sfx)
        self._start_label.setText("触发片段" if is_sfx else "开始片段")
        if is_sfx:
            self._anchor_hint.setText(
                "音效先锁定剧情片段，再应用正负毫秒偏移；默认会避开对白，无法避开时会在混音清单中提示。"
            )
        else:
            self._anchor_hint.setText(
                "起止片段会在语音生成后换算成真实时间；对白出现时按“对白下压”自动降低本轨音量。"
            )

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self._name_input,
            self._description_input,
            self._start_combo,
            self._end_combo,
            self._offset_spin,
            self._duration_spin,
            self._volume_spin,
            self._duck_spin,
            self._fade_in_spin,
            self._fade_out_spin,
            self._loop_check,
            self._overlap_check,
            self._delete_btn,
        ):
            widget.setEnabled(enabled)

    def _commit_current(self) -> bool:
        cue = self._current_cue()
        if cue is None or self._current_kind is None:
            return True
        name = self._name_input.text().strip()
        if not name:
            self._anchor_hint.setText("请填写声音线索名称，便于匹配素材和审计成品。")
            return False
        description = self._description_input.toPlainText().strip()
        start = self._start_combo.currentData()
        volume = self._volume_spin.value() / 100.0
        if self._current_kind == "soundscape":
            self._soundscapes[self._current_index] = cue.model_copy(
                update={
                    "name": name,
                    "description": description,
                    "start_segment_index": start,
                    "end_segment_index": self._end_combo.currentData(),
                    "volume": volume,
                    "ducking_db": self._duck_spin.value(),
                    "fade_in_ms": self._fade_in_spin.value(),
                    "fade_out_ms": self._fade_out_spin.value(),
                    "loop": self._loop_check.isChecked(),
                }
            )
        elif self._current_kind == "bgm":
            self._bgm[self._current_index] = cue.model_copy(
                update={
                    "track_name": name,
                    "mood": description,
                    "start_segment_index": start,
                    "end_segment_index": self._end_combo.currentData(),
                    "volume": volume,
                    "ducking_db": self._duck_spin.value(),
                    "fade_in_ms": self._fade_in_spin.value(),
                    "fade_out_ms": self._fade_out_spin.value(),
                    "loop": self._loop_check.isChecked(),
                }
            )
        else:
            self._sfx[self._current_index] = cue.model_copy(
                update={
                    "effect_name": name,
                    "description": description,
                    "trigger_segment_index": start,
                    "offset_ms": self._offset_spin.value(),
                    "duration_ms": self._duration_spin.value(),
                    "volume": volume,
                    "allow_dialogue_overlap": self._overlap_check.isChecked(),
                }
            )
        return True

    def _add_cue(self, kind: CueKind) -> None:
        if self._current_kind is not None and not self._commit_current():
            return
        first_index = self._script.segments[0].segment_index if self._script.segments else None
        if kind == "soundscape":
            self._soundscapes.append(
                SoundscapeCue(name="新环境声", start_segment_index=first_index)
            )
            index = len(self._soundscapes) - 1
        elif kind == "bgm":
            self._bgm.append(BGMTiming(track_name="新背景音乐", start_segment_index=first_index))
            index = len(self._bgm) - 1
        else:
            self._sfx.append(SFXCue(effect_name="新音效", trigger_segment_index=first_index))
            index = len(self._sfx) - 1
        self._current_kind = None
        self._current_index = -1
        self._populate_list(select=(kind, index))

    def _delete_current(self) -> None:
        kind = self._current_kind
        index = self._current_index
        if kind is None or index < 0:
            return
        target = self._soundscapes if kind == "soundscape" else self._bgm if kind == "bgm" else self._sfx
        if index < len(target):
            target.pop(index)
        self._current_kind = None
        self._current_index = -1
        self._populate_list()
        if self._cue_list.count():
            self._cue_list.setCurrentRow(min(index, self._cue_list.count() - 1))
        else:
            self._set_form_enabled(False)
            self._kind_badge.setText("选择声音线索")
            self._kind_badge.set_tone("muted")

    def _save(self) -> None:
        if self._commit_current():
            self.accept()

    @property
    def has_changes(self) -> bool:
        return (
            self._soundscapes != self._script.soundscapes
            or self._bgm != self._script.bgm_suggestions
            or self._sfx != self._script.sfx_cues
        )

    def edited_script(self) -> DubbingScript:
        self._commit_current()
        return self._script.model_copy(
            update={
                "soundscapes": list(self._soundscapes),
                "bgm_suggestions": list(self._bgm),
                "sfx_cues": list(self._sfx),
            }
        )


__all__ = ("SoundDesignEditorDialog",)
