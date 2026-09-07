"""Dialog classes for Voice Studio.

Extracted from ``page.py`` to reduce single-file size.  Both dialogs
are self-contained: they take data objects (``DubbingScript``,
``VoiceCastEntry``, etc.) as inputs and have no import dependency on
``VoiceStudioPage``.
"""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.pages.voice_studio.provider_ui import provider_ui_spec
from novel_forge.desktop.theme import qcolor_hex
from novel_forge.desktop.widgets import ActionButton, Badge, ask_confirmation
from novel_forge.tts.platform.field_mapping import (
    FieldSupport,
    provider_field_profile,
    provider_language_value,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    LanguageRun,
    SegmentType,
    TTSProvider,
    VoiceCastEntry,
)
from novel_forge.tts.script_integrity import unresolved_speaker_indices

from .helpers import emotion_display

__all__ = (
    "RebuildConfirmDialog",
    "ScriptSegmentEditorDialog",
)


class RebuildConfirmDialog(QDialog):
    """On-brand, scoped rebuild dialog for an existing voice team."""

    def __init__(
        self,
        characters: list[dict[str, Any]],
        voice_team_entries: list[VoiceCastEntry],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle("重新构建配音团队")
        self.setModal(True)
        self.setMinimumSize(560, 460)
        self.resize(*smart_dialog_size(parent or self, 680, 560, max_screen_fraction=0.72))
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("重新构建配音团队")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self._selection_badge = Badge("已选 0 位", tone="muted")
        title_row.addWidget(self._selection_badge)
        layout.addLayout(title_row)

        context = QLabel(
            "只会重新分配勾选角色的音色；未勾选的既有分配保持不变。"
            "旁白采用独立的作品级音色设计，可在左侧「旁白」条目中单独重新生成。"
        )
        context.setObjectName("dialogContext")
        context.setWordWrap(True)
        layout.addWidget(context)

        section = QLabel("选择需要重建的角色")
        section.setObjectName("voiceRebuildSection")
        layout.addWidget(section)

        self._list = QListWidget()
        self._list.setObjectName("voiceRebuildList")
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setAlternatingRowColors(False)
        self._list.setMinimumHeight(250)
        layout.addWidget(self._list, 1)

        # Build character_id -> entry map
        entry_map: dict[str, VoiceCastEntry] = {
            entry.character_id: entry for entry in voice_team_entries
        }

        # Populate list
        self._char_items: list[tuple[str, QListWidgetItem]] = []
        for char in characters:
            char_id = str(char.get("character_id", char.get("name", "")) or "")
            char_name = str(char.get("name", char_id) or char_id)
            role = char.get("role", "")
            role_label = {
                "protagonist": "主角",
                "deuteragonist": "副主",
                "antagonist": "反派",
                "supporting": "配角",
                "minor": "龙套",
            }.get(role, role)
            team_entry = entry_map.get(char_id)
            source_label = ""
            if team_entry:
                source_map = {
                    "designed": "AI设计",
                    "cloned": "克隆",
                    "system": "系统",
                    "manual": "手动",
                    "library": "音色库",
                }
                source_label = source_map.get(team_entry.voice_source, team_entry.voice_source)
            suffix = f" · {role_label}" if role_label else ""
            source_suffix = f" · {source_label}" if source_label else ""
            item = QListWidgetItem(f"{char_name}{suffix}{source_suffix}")
            item.setData(Qt.ItemDataRole.UserRole, char_id)
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                team_entry.voice_source if team_entry else "",
            )
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(
                f"{char_name} · {role_label or '未标注定位'} · "
                f"当前来源：{source_label or '尚未分配'}"
            )
            self._list.addItem(item)
            self._char_items.append((char_id, item))

        btn_row = QHBoxLayout()
        select_all_btn = ActionButton("全选", variant="quiet")
        select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(select_all_btn)

        select_system_btn = ActionButton("仅系统匹配", variant="secondary")
        select_system_btn.clicked.connect(lambda: self._select_by_source("system"))
        btn_row.addWidget(select_system_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        action_row = QHBoxLayout()
        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.clicked.connect(self.reject)
        action_row.addWidget(cancel_btn)
        action_row.addStretch()

        self._confirm_btn = ActionButton("重建选中角色", variant="primary")
        self._confirm_btn.clicked.connect(self._on_confirm)
        action_row.addWidget(self._confirm_btn)
        layout.addLayout(action_row)

        self._list.itemChanged.connect(self._update_selection_state)
        self._update_selection_state()

    def _select_all(self) -> None:
        for _, item in self._char_items:
            item.setCheckState(Qt.CheckState.Checked)

    def _select_by_source(self, source: str) -> None:
        for _, item in self._char_items:
            if item.data(Qt.ItemDataRole.UserRole + 1) == source:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _on_confirm(self) -> None:
        if self.get_selected_ids():
            self.accept()

    def _update_selection_state(self, _item: QListWidgetItem | None = None) -> None:
        """Keep the destructive action explicit and count-backed."""
        count = len(self.get_selected_ids())
        self._selection_badge.setText(f"已选 {count} 位")
        self._selection_badge.set_tone("default" if count else "muted")
        self._confirm_btn.setText(f"重建 {count} 位角色" if count else "选择要重建的角色")
        self._confirm_btn.setEnabled(count > 0)

    def get_selected_ids(self) -> list[str]:
        """Return list of selected character IDs."""
        selected = []
        for char_id, item in self._char_items:
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(char_id)
        return selected


class ScriptSegmentEditorDialog(QDialog):
    """Human-in-the-loop editor for one dubbing script and its individual segments."""

    def __init__(
        self,
        script: DubbingScript,
        *,
        initial_segment_index: int | None = None,
        provider: str = "",
        performance_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle(f"编辑第 {script.chapter_number} 章配音脚本")
        self.resize(*smart_dialog_size(parent or self, 1120, 760, max_screen_fraction=0.85))

        self._script = script.model_copy(deep=True)
        self._original_segments = [segment.model_copy(deep=True) for segment in script.segments]
        self._segments = [segment.model_copy(deep=True) for segment in script.segments]
        # Keep stable source identities while rows are deleted and reindexed.
        # These identities let us remap cue/audit references and still restore
        # the correct original segment after a structural edit.
        self._segment_origin_indices = [segment.segment_index for segment in script.segments]
        # Origins absorbed into a merged narration point at the surviving
        # origin so sound cues and review records stay on the combined segment.
        self._merged_origin_indices: dict[int, int] = {}
        self._unresolved_origin_indices = set(unresolved_speaker_indices(script))
        self._speaker_changed_indices: set[int] = set()
        self._current_row = -1
        self._loading = False
        inherited_provider = ""
        if parent is not None and hasattr(parent, "_get_current_provider"):
            inherited_provider = str(parent._get_current_provider())
        self._provider = provider.strip().lower() or inherited_provider or "mock"
        self._performance_only = performance_only

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        heading = QLabel("逐段编辑配音脚本")
        heading.setObjectName("voiceDialogTitle")
        layout.addWidget(heading)
        description = QLabel(
            (
                "这里保存的是待审试听指导，不会改动源配音脚本或已装配成品。"
                "只有生成试听并点击“接受此版”后才会推广为正式分段，"
                "随后再到后处理重新装配全章。"
                if performance_only
                else "修改文本、情绪、语气或语速后统一保存。保存会使本章旧音频和字幕待重建，"
                "避免文本与成品不一致。"
            )
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self._review_panel = QFrame()
        self._review_panel.setObjectName("voiceSpeakerReviewPanel")
        review_layout = QHBoxLayout(self._review_panel)
        review_layout.setContentsMargins(10, 7, 10, 7)
        review_layout.setSpacing(8)
        self._review_badge = Badge("说话人复核", tone="muted")
        review_layout.addWidget(self._review_badge)
        self._review_hint = QLabel()
        self._review_hint.setObjectName("voiceSpeakerReviewHint")
        self._review_hint.setWordWrap(True)
        review_layout.addWidget(self._review_hint, 1)
        self._next_review_btn = ActionButton("下一处待复核", variant="secondary")
        self._next_review_btn.clicked.connect(self._go_to_next_unresolved)
        review_layout.addWidget(self._next_review_btn)
        self._batch_accept_btn = ActionButton("全部采纳推荐", variant="secondary")
        self._batch_accept_btn.setToolTip(
            "将置信度≥75%的候选角色自动填入所有待复核片段；低置信度段仍保留待人工处理。"
        )
        self._batch_accept_btn.clicked.connect(self._batch_accept_recommendations)
        review_layout.addWidget(self._batch_accept_btn)
        layout.addWidget(self._review_panel)

        self._platform_mapping_hint = QLabel()
        self._platform_mapping_hint.setObjectName("voicePlatformMappingHint")
        self._platform_mapping_hint.setWordWrap(True)
        layout.addWidget(self._platform_mapping_hint)

        speaker_row = QHBoxLayout()
        speaker_row.addWidget(QLabel("片段类型"))
        self._segment_type_combo = QComboBox()
        self._segment_type_combo.setAccessibleName("当前片段类型")
        self._segment_type_combo.addItem("旁白", SegmentType.NARRATION.value)
        self._segment_type_combo.addItem("角色对白", SegmentType.DIALOGUE.value)
        self._segment_type_combo.addItem("内心独白", SegmentType.INNER_THOUGHT.value)
        self._segment_type_combo.addItem("背景音乐提示", SegmentType.BGM.value)
        self._segment_type_combo.addItem("音效提示", SegmentType.SFX.value)
        self._segment_type_combo.addItem("静音 / 停顿", SegmentType.SILENCE.value)
        self._segment_type_combo.setToolTip(
            "仅将当前叙事时刻实际发声的内容标为对白；其余引号内容保持旁白。"
        )
        speaker_row.addWidget(self._segment_type_combo, 1)
        speaker_row.addWidget(QLabel("说话人"))
        self._speaker_combo = QComboBox()
        self._speaker_combo.setAccessibleName("当前对白说话人")
        self._speaker_combo.addItem("旁白 / 未指定", "")
        voice_team = getattr(parent, "_voice_team", None)
        for entry in voice_team.entries if voice_team is not None else []:
            if entry.character_id:
                self._speaker_combo.addItem(entry.character_name, entry.character_id)
        self._speaker_combo.setToolTip(
            "正文证据不足的对白会标记为待复核；在这里人工指定后才能正式合成。"
        )
        speaker_row.addWidget(self._speaker_combo, 1)
        self._confirm_speaker_btn = ActionButton("确认本段说话人", variant="secondary")
        self._confirm_speaker_btn.setToolTip(
            "即使系统已给出候选角色，也需要人工确认一次，之后本段才会解除待复核。"
        )
        self._confirm_speaker_btn.clicked.connect(self._confirm_current_speaker_review)
        speaker_row.addWidget(self._confirm_speaker_btn)
        layout.addLayout(speaker_row)

        content = QHBoxLayout()
        content.setSpacing(12)
        self._segment_list = QListWidget()
        self._segment_list.setObjectName("voiceScriptSegmentList")
        self._segment_list.setMinimumWidth(230)
        self._segment_list.setAccessibleName("配音脚本片段列表")
        self._segment_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._segment_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        content.addWidget(self._segment_list, 2)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        self._segment_heading = QLabel()
        self._segment_heading.setObjectName("voiceSectionTitle")
        editor_layout.addWidget(self._segment_heading)

        editor_layout.addWidget(QLabel("台词 / 旁白"))
        self._text_input = QTextEdit()
        self._text_input.setObjectName("voiceScriptTextEditor")
        self._text_input.setAcceptRichText(False)
        self._text_input.setMinimumHeight(170)
        self._text_input.setPlaceholderText("输入这一段要由角色或旁白说出的内容…")
        self._text_input.setAccessibleName("当前片段文本")
        editor_layout.addWidget(self._text_input, 1)

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(6)
        controls.addWidget(QLabel("情绪"), 0, 0)
        self._emotion_combo = QComboBox()
        for emotion in EmotionTag:
            label, _ = emotion_display(emotion)
            self._emotion_combo.addItem(label, emotion.value)
        self._emotion_combo.setAccessibleName("当前片段情绪")
        controls.addWidget(self._emotion_combo, 0, 1)
        controls.addWidget(QLabel("语气提示"), 0, 2)
        self._tone_input = QLineEdit()
        self._tone_input.setPlaceholderText("例如：克制、迟疑、压低声音")
        self._tone_input.setAccessibleName("当前片段语气提示")
        controls.addWidget(self._tone_input, 0, 3)
        controls.addWidget(QLabel("强度"), 0, 4)
        self._intensity_slider = QSlider(Qt.Horizontal)
        self._intensity_slider.setRange(0, 100)
        self._intensity_slider.setValue(50)
        self._intensity_slider.setTickInterval(10)
        self._intensity_slider.setAccessibleName("当前片段情绪强度")
        controls.addWidget(self._intensity_slider, 0, 5)
        self._intensity_label = QLabel("50%")
        self._intensity_label.setMinimumWidth(32)
        controls.addWidget(self._intensity_label, 0, 6)
        self._intensity_slider.valueChanged.connect(
            lambda v: self._intensity_label.setText(f"{v}%")
        )
        controls.addWidget(QLabel("语速"), 1, 0)
        self._speed_combo = QComboBox()
        self._speed_combo.addItem("使用项目默认", None)
        for speed in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5):
            self._speed_combo.addItem(f"{speed:.1f}×", speed)
        self._speed_combo.setAccessibleName("当前片段语速")
        controls.addWidget(self._speed_combo, 1, 1)
        self._estimate_label = QLabel()
        self._estimate_label.setObjectName("voiceScriptDurationEstimate")
        controls.addWidget(self._estimate_label, 1, 2, 1, 2)
        controls.addWidget(QLabel("音量"), 2, 0)
        self._volume_combo = QComboBox()
        self._volume_combo.addItem("使用项目默认", None)
        for volume in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4):
            self._volume_combo.addItem(f"{volume:.1f}×", volume)
        self._volume_combo.setAccessibleName("当前片段音量")
        controls.addWidget(self._volume_combo, 2, 1)
        controls.addWidget(QLabel("音高"), 2, 2)
        self._pitch_combo = QComboBox()
        self._pitch_combo.addItem("使用项目默认", None)
        for pitch in range(-4, 5):
            self._pitch_combo.addItem(f"{pitch:+d} 半音", pitch)
        self._pitch_combo.setAccessibleName("当前片段音高")
        controls.addWidget(self._pitch_combo, 2, 3)
        controls.addWidget(QLabel("重音词"), 3, 0)
        self._stress_input = QLineEdit()
        self._stress_input.setPlaceholderText("用逗号分隔，例如：绝不，今晚")
        self._stress_input.setAccessibleName("当前片段重音词")
        controls.addWidget(self._stress_input, 3, 1)
        controls.addWidget(QLabel("旁白距离"), 3, 2)
        self._distance_combo = QComboBox()
        self._distance_combo.addItem("不指定", "")
        self._distance_combo.addItem("近距（贴近角色）", "close")
        self._distance_combo.addItem("中距（常规叙述）", "medium")
        self._distance_combo.addItem("远距（全知/宏观）", "distant")
        controls.addWidget(self._distance_combo, 3, 3)
        controls.addWidget(QLabel("发音覆盖"), 4, 0)
        self._pronunciation_input = QLineEdit()
        self._pronunciation_input.setPlaceholderText("用分号分隔：燕少飞/(yan4)(shao3)(fei1)")
        self._pronunciation_input.setAccessibleName("当前片段发音覆盖")
        controls.addWidget(self._pronunciation_input, 4, 1)
        controls.addWidget(QLabel("空间 / 设备效果"), 4, 2)
        self._voice_effect_combo = QComboBox()
        self._voice_effect_combo.addItem("无（推荐）", "")
        self._voice_effect_combo.addItem("空旷回声", "spacious_echo")
        self._voice_effect_combo.addItem("礼堂广播", "auditorium_echo")
        self._voice_effect_combo.addItem("电话失真", "lofi_telephone")
        self._voice_effect_combo.addItem("机械声", "robotic")
        controls.addWidget(self._voice_effect_combo, 4, 3)
        controls.addWidget(QLabel("主要语言"), 5, 0)
        self._language_combo = QComboBox()
        for label, value in (
            ("自动识别", "auto"),
            ("普通话", "zh"),
            ("粤语", "yue"),
            ("英语", "en"),
            ("日语", "ja"),
            ("韩语", "ko"),
            ("法语", "fr"),
            ("德语", "de"),
            ("西班牙语", "es"),
            ("葡萄牙语", "pt"),
            ("俄语", "ru"),
        ):
            self._language_combo.addItem(label, value)
        controls.addWidget(self._language_combo, 5, 1)
        controls.addWidget(QLabel("表达风格"), 5, 2)
        self._delivery_style_combo = QComboBox()
        for label, value in (
            ("自然", "natural"),
            ("贴近 / 私语", "intimate"),
            ("有声书叙事", "narrative"),
            ("自然交谈", "conversational"),
            ("戏剧表达", "dramatic"),
            ("播音表达", "broadcast"),
        ):
            self._delivery_style_combo.addItem(label, value)
        controls.addWidget(self._delivery_style_combo, 5, 3)
        controls.addWidget(QLabel("能量"), 6, 0)
        self._energy_combo = QComboBox()
        for label, numeric_value in (("收敛", 0.25), ("自然", 0.5), ("充沛", 0.8)):
            self._energy_combo.addItem(label, numeric_value)
        controls.addWidget(self._energy_combo, 6, 1)
        controls.addWidget(QLabel("吐字"), 6, 2)
        self._articulation_combo = QComboBox()
        for label, numeric_value in (("松弛", 0.35), ("自然", 0.6), ("清晰利落", 0.85)):
            self._articulation_combo.addItem(label, numeric_value)
        controls.addWidget(self._articulation_combo, 6, 3)
        controls.addWidget(QLabel("气声"), 7, 0)
        self._breathiness_combo = QComboBox()
        for label, numeric_value in (("很少", 0.1), ("自然", 0.2), ("明显", 0.65)):
            self._breathiness_combo.addItem(label, numeric_value)
        controls.addWidget(self._breathiness_combo, 7, 1)
        controls.addWidget(QLabel("张力"), 7, 2)
        self._tension_combo = QComboBox()
        for label, numeric_value in (("放松", 0.15), ("自然", 0.3), ("紧绷", 0.75)):
            self._tension_combo.addItem(label, numeric_value)
        controls.addWidget(self._tension_combo, 7, 3)
        controls.addWidget(QLabel("表达意图"), 8, 0)
        self._intent_input = QLineEdit()
        self._intent_input.setPlaceholderText("例如：让对方相信自己其实并不害怕")
        controls.addWidget(self._intent_input, 8, 1, 1, 3)
        controls.addWidget(QLabel("平台原生补充"), 9, 0)
        self._platform_instruction_input = QLineEdit()
        self._platform_instruction_input.setPlaceholderText(
            "仅发送给当前平台，例如 Qwen/CosyVoice 的自然语言 instruct"
        )
        controls.addWidget(self._platform_instruction_input, 9, 1, 1, 3)
        editor_layout.addLayout(controls)

        guidance_panel = QFrame()
        guidance_panel.setObjectName("voiceScriptGuidancePanel")
        guidance_layout = QVBoxLayout(guidance_panel)
        guidance_layout.setContentsMargins(9, 7, 9, 7)
        guidance_layout.setSpacing(5)

        self._synthesis_direction_label = QLabel()
        self._synthesis_direction_label.setObjectName("voiceScriptSynthesisDirection")
        self._synthesis_direction_label.setWordWrap(True)
        guidance_layout.addWidget(self._synthesis_direction_label)

        self._advice_label = QLabel()
        self._advice_label.setObjectName("voiceScriptSmartAdvice")
        self._advice_label.setWordWrap(True)
        guidance_layout.addWidget(self._advice_label)
        editor_layout.addWidget(guidance_panel)

        assist_row = QHBoxLayout()
        self._apply_suggestion_btn = ActionButton("应用安全建议", variant="secondary")
        self._apply_suggestion_btn.setToolTip("仅应用标点和明显语气建议，不会改写正文含义")
        self._apply_suggestion_btn.clicked.connect(self._apply_safe_suggestion)
        assist_row.addWidget(self._apply_suggestion_btn)
        self._revert_segment_btn = ActionButton("还原当前段", variant="quiet")
        self._revert_segment_btn.clicked.connect(self._revert_current_segment)
        assist_row.addWidget(self._revert_segment_btn)
        assist_row.addStretch()
        editor_layout.addLayout(assist_row)

        # The performance form is intentionally deep.  Give it its own scroll
        # viewport so it can shrink below its size hint without sliding under
        # the dialog footer on laptop-height screens.
        editor_scroll = QScrollArea()
        editor_scroll.setObjectName("voiceScriptEditorScroll")
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor_scroll.setWidget(editor)
        content.addWidget(editor_scroll, 5)
        layout.addLayout(content, 1)

        footer = QHBoxLayout()
        self._previous_btn = ActionButton("上一段", variant="quiet")
        self._previous_btn.clicked.connect(lambda: self._move_segment(-1))
        footer.addWidget(self._previous_btn)
        self._next_btn = ActionButton("下一段", variant="quiet")
        self._next_btn.clicked.connect(lambda: self._move_segment(1))
        footer.addWidget(self._next_btn)
        self._delete_segment_btn = ActionButton("删除当前段", variant="danger")
        self._delete_segment_btn.setToolTip("删除后会自动重排片段编号和音效锚点")
        self._delete_segment_btn.clicked.connect(self._delete_current_segment)
        self._delete_segment_btn.setVisible(not performance_only)
        footer.addWidget(self._delete_segment_btn)
        footer.addStretch()
        cancel_btn = ActionButton("取消", variant="quiet")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        save_btn = ActionButton(
            "保存试听指导" if performance_only else "保存脚本",
            variant="primary",
        )
        save_btn.setToolTip(
            "只保存待审指导，不覆盖正式版本"
            if performance_only
            else "保存修改，并使本章派生的音频和字幕待重建"
        )
        save_btn.clicked.connect(self._save)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

        self._populate_segment_list()
        self._segment_list.currentRowChanged.connect(self._on_segment_changed)
        self._segment_type_combo.currentIndexChanged.connect(self._on_segment_type_changed)
        self._text_input.textChanged.connect(self._refresh_smart_advice)
        self._emotion_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._tone_input.textChanged.connect(self._refresh_smart_advice)
        self._speed_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._volume_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._pitch_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._stress_input.textChanged.connect(self._refresh_smart_advice)
        self._distance_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._pronunciation_input.textChanged.connect(self._refresh_smart_advice)
        self._voice_effect_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._language_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._delivery_style_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._energy_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._articulation_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._breathiness_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._tension_combo.currentIndexChanged.connect(self._refresh_smart_advice)
        self._intent_input.textChanged.connect(self._refresh_smart_advice)
        self._platform_instruction_input.textChanged.connect(self._refresh_smart_advice)
        self._refresh_platform_mapping_hint()
        self._refresh_review_state()
        target_row = self._row_for_segment(initial_segment_index)
        self._segment_list.setCurrentRow(target_row)

    @staticmethod
    def _segment_kind(segment: DubbingSegment) -> str:
        return {
            SegmentType.NARRATION: "旁白",
            SegmentType.DIALOGUE: segment.character_name or "角色对白",
            SegmentType.INNER_THOUGHT: f"{segment.character_name or '角色'}·内心",
            SegmentType.BGM: "背景音乐",
            SegmentType.SFX: "音效",
            SegmentType.SILENCE: "静音",
        }.get(segment.segment_type, "片段")

    @classmethod
    def _segment_label(
        cls,
        segment: DubbingSegment,
        *,
        display_number: int,
    ) -> str:
        """Create a concise, scannable label for the editor sidebar."""
        preview = re.sub(r"\s+", " ", segment.text).strip()
        if len(preview) > 22:
            preview = f"{preview[:22]}…"
        return f"{display_number:02d}  {cls._segment_kind(segment)}  ·  {preview}"

    def _populate_segment_list(self) -> None:
        self._segment_list.clear()
        for row, segment in enumerate(self._segments):
            item = QListWidgetItem(
                self._segment_label(segment, display_number=row + 1)
            )
            item.setData(Qt.ItemDataRole.UserRole, segment.segment_index)
            item.setToolTip(segment.text)
            self._apply_segment_review_style(item, row)
            self._segment_list.addItem(item)

    def _row_needs_review(self, row: int) -> bool:
        if row < 0 or row >= len(self._segment_origin_indices):
            return False
        origin_index = self._segment_origin_indices[row]
        return (
            origin_index in self._unresolved_origin_indices
            and origin_index not in self._speaker_changed_indices
        )

    def _apply_segment_review_style(self, item: QListWidgetItem, row: int) -> None:
        """Make unresolved rows visible without relying on a transient toast."""
        if self._row_needs_review(row):
            item.setText(
                "待复核 · "
                f"{self._segment_label(self._segments[row], display_number=row + 1)}"
            )
            item.setForeground(QBrush(QColor(qcolor_hex("status.danger.deep"))))
            item.setBackground(QBrush(QColor(qcolor_hex("status.danger.rate"))))
            item.setToolTip("正文证据不足：请核对片段类型和说话人，并点击“确认本段说话人”。")
        else:
            item.setForeground(QBrush())
            item.setBackground(QBrush())

    def _remaining_unresolved_rows(self) -> list[int]:
        return [row for row in range(len(self._segments)) if self._row_needs_review(row)]

    def _refresh_review_state(self) -> None:
        remaining = self._remaining_unresolved_rows()
        total = len(self._unresolved_origin_indices)
        resolved = max(0, total - len(remaining))
        signature = (total, tuple(remaining), resolved)
        if getattr(self, "_review_state_signature", None) == signature:
            return
        self._review_state_signature = signature
        previous_tone = self._review_panel.property("tone")
        if total <= 0:
            self._review_badge.setText("说话人已确认")
            self._review_badge.set_tone("success")
            self._review_hint.setText("本章没有需要人工复核的说话人。")
            self._next_review_btn.setVisible(False)
            self._review_panel.setProperty("tone", "success")
        elif remaining:
            self._review_badge.setText(f"待复核 {len(remaining)} 段")
            self._review_badge.set_tone("danger")
            self._review_hint.setText(
                f"已确认 {resolved}/{total}。红色片段必须核对片段类型与说话人，"
                "确认后才可进入配音室和正式合成。"
            )
            self._next_review_btn.setVisible(True)
            self._review_panel.setProperty("tone", "danger")
        else:
            self._review_badge.setText(f"复核完成 {total}/{total}")
            self._review_badge.set_tone("success")
            self._review_hint.setText("所有说话人均已人工确认，保存脚本后即可正式合成。")
            self._next_review_btn.setVisible(False)
            self._review_panel.setProperty("tone", "success")
        if self._review_panel.property("tone") != previous_tone:
            self._review_panel.style().unpolish(self._review_panel)
            self._review_panel.style().polish(self._review_panel)

    def _go_to_next_unresolved(self) -> None:
        if self._current_row >= 0 and not self._commit_current():
            return
        remaining = self._remaining_unresolved_rows()
        if not remaining:
            self._refresh_review_state()
            return
        target = next((row for row in remaining if row > self._current_row), remaining[0])
        self._segment_list.setCurrentRow(target)

    def _batch_accept_recommendations(self) -> None:
        """Auto-fill high-confidence candidates for all unresolved segments."""
        accepted_count = 0
        for row in range(len(self._segments)):
            if not self._row_needs_review(row):
                continue
            origin_index = self._segment_origin_indices[row]
            candidates = self._get_speaker_candidates(origin_index)
            if not candidates:
                continue
            best_char_id, best_confidence, _ = candidates[0]
            if best_confidence < 0.75 or not best_char_id:
                continue
            # Find the character name from the voice team combo.
            combo_idx = self._speaker_combo.findData(best_char_id)
            char_name = (
                self._speaker_combo.itemText(combo_idx).split(" (推荐")[0]
                if combo_idx >= 0
                else best_char_id
            )
            segment = self._segments[row]
            self._segments[row] = segment.model_copy(
                update={
                    "character_id": best_char_id,
                    "character_name": char_name,
                    "segment_type": SegmentType.DIALOGUE,
                }
            )
            self._speaker_changed_indices.add(origin_index)
            accepted_count += 1
        if accepted_count:
            self._populate_segment_list()
            self._refresh_review_state()
            if self._current_row >= 0:
                self._load_segment(self._current_row)
            self._advice_label.setText(
                f"智能提示：已自动采纳 {accepted_count} 段高置信度推荐。"
            )
        else:
            self._advice_label.setText(
                "智能提示：没有置信度≥75%的候选角色，请逐段手动确认。"
            )

    def _confirm_current_speaker_review(self) -> None:
        if self._current_row < 0:
            return
        selected_type = SegmentType(
            str(self._segment_type_combo.currentData() or SegmentType.NARRATION.value)
        )
        if (
            selected_type in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
            and not self._speaker_combo.currentData()
        ):
            self._advice_label.setText(
                "智能提示：对白或内心独白必须指定配音团队中的角色，才能完成复核。"
            )
            return
        if not self._commit_current():
            return
        origin_index = self._segment_origin_indices[self._current_row]
        self._speaker_changed_indices.add(origin_index)
        if selected_type == SegmentType.NARRATION:
            self._merge_adjacent_narration()
        item = self._segment_list.item(self._current_row)
        if item is not None:
            item.setText(
                self._segment_label(
                    self._segments[self._current_row],
                    display_number=self._current_row + 1,
                )
            )
            item.setToolTip(self._segments[self._current_row].text)
            self._apply_segment_review_style(item, self._current_row)
        self._refresh_review_state()
        self._go_to_next_unresolved()

    def _merge_adjacent_narration(self) -> bool:
        """Join a confirmed narrator fragment with contiguous narrator fragments."""
        if (
            self._current_row < 0
            or self._segments[self._current_row].segment_type != SegmentType.NARRATION
        ):
            return False

        start = self._current_row
        while start > 0 and self._segments[start - 1].segment_type == SegmentType.NARRATION:
            start -= 1
        end = self._current_row
        while (
            end + 1 < len(self._segments)
            and self._segments[end + 1].segment_type == SegmentType.NARRATION
        ):
            end += 1
        if start == end:
            return False

        contiguous_segments = self._segments[start : end + 1]
        contiguous_origins = self._segment_origin_indices[start : end + 1]
        first_segment = contiguous_segments[0]
        merged_text = "".join(segment.text for segment in contiguous_segments)
        merged_segment = first_segment.model_copy(
            update={
                "text": merged_text,
                # A performance rewrite for one old fragment cannot safely be
                # reused for the newly combined source text.
                "spoken_text": "",
                "language_runs": [
                    LanguageRun(language=first_segment.language_code, text=merged_text)
                ],
            }
        )
        surviving_origin = contiguous_origins[0]
        for origin in contiguous_origins[1:]:
            self._merged_origin_indices[origin] = surviving_origin
        self._speaker_changed_indices.update(contiguous_origins)
        self._segments[start : end + 1] = [merged_segment]
        self._segment_origin_indices[start : end + 1] = [surviving_origin]
        self._segments = [
            segment.model_copy(update={"segment_index": index})
            for index, segment in enumerate(self._segments)
        ]

        self._current_row = start
        self._loading = True
        try:
            self._populate_segment_list()
            self._segment_list.setCurrentRow(start)
        finally:
            self._loading = False
        self._load_segment(start)
        self._advice_label.setText(f"已确认旁白，并合并了 {len(contiguous_segments)} 段连续旁白。")
        return True

    def _row_for_segment(self, segment_index: int | None) -> int:
        if segment_index is not None:
            for row, segment in enumerate(self._segments):
                if segment.segment_index == segment_index:
                    return row
        return 0 if self._segments else -1

    def _on_segment_changed(self, row: int) -> None:
        if self._loading or row < 0 or row >= len(self._segments):
            return
        if self._current_row >= 0 and not self._commit_current():
            self._segment_list.blockSignals(True)
            self._segment_list.setCurrentRow(self._current_row)
            self._segment_list.blockSignals(False)
            return
        self._load_segment(row)

    def _load_segment(self, row: int) -> None:
        self._loading = True
        try:
            segment = self._segments[row]
            self._current_row = row
            self._segment_heading.setText(
                f"第 {row + 1} 段 · {self._segment_kind(segment)}"
            )
            self._text_input.setPlainText(
                segment.synthesis_text if self._performance_only else segment.text
            )
            type_index = self._segment_type_combo.findData(segment.segment_type.value)
            self._segment_type_combo.setCurrentIndex(max(type_index, 0))
            self._segment_type_combo.setEnabled(not self._performance_only)
            speaker_index = self._speaker_combo.findData(segment.character_id)
            if speaker_index < 0 and segment.character_id:
                self._speaker_combo.addItem(
                    segment.character_name or segment.character_id,
                    segment.character_id,
                )
                speaker_index = self._speaker_combo.count() - 1
            self._speaker_combo.setCurrentIndex(max(speaker_index, 0))
            self._speaker_combo.setEnabled(
                not self._performance_only
                and segment.segment_type in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
            )
            self._refresh_confirmation_action(segment.segment_type)
            self._confirm_speaker_btn.setVisible(
                not self._performance_only and self._row_needs_review(row)
            )
            emotion_index = self._emotion_combo.findData(segment.emotion.value)
            self._emotion_combo.setCurrentIndex(max(emotion_index, 0))
            self._intensity_slider.setValue(int(segment.emotion_intensity * 100))
            self._tone_input.setText(segment.tone_hint)
            speed_index = self._speed_combo.findData(segment.speed_override)
            self._speed_combo.setCurrentIndex(max(speed_index, 0))
            volume_index = self._volume_combo.findData(segment.vol_override)
            self._volume_combo.setCurrentIndex(max(volume_index, 0))
            pitch_index = self._pitch_combo.findData(segment.pitch_override)
            self._pitch_combo.setCurrentIndex(max(pitch_index, 0))
            self._stress_input.setText("，".join(segment.stress_words))
            distance_index = self._distance_combo.findData(segment.narrator_distance)
            self._distance_combo.setCurrentIndex(max(distance_index, 0))
            self._distance_combo.setEnabled(segment.segment_type == SegmentType.NARRATION)
            self._pronunciation_input.setText("；".join(segment.pronunciation_overrides))
            effect_index = self._voice_effect_combo.findData(segment.voice_effect.sound_effects)
            self._voice_effect_combo.setCurrentIndex(max(effect_index, 0))
            language_value = segment.language_code
            if language_value == "auto":
                language_value = {
                    "Chinese": "zh",
                    "Chinese,Yue": "yue",
                    "English": "en",
                }.get(segment.language_boost, "auto")
            language_index = self._language_combo.findData(language_value)
            if language_index < 0 and language_value:
                self._language_combo.addItem(language_value, language_value)
                language_index = self._language_combo.count() - 1
            self._language_combo.setCurrentIndex(max(language_index, 0))
            direction = segment.vocal_direction
            self._delivery_style_combo.setCurrentIndex(
                max(self._delivery_style_combo.findData(direction.delivery_style), 0)
            )
            self._set_nearest_combo_value(self._energy_combo, direction.energy)
            self._set_nearest_combo_value(self._articulation_combo, direction.articulation)
            self._set_nearest_combo_value(self._breathiness_combo, direction.breathiness)
            self._set_nearest_combo_value(self._tension_combo, direction.tension)
            self._intent_input.setText(direction.intent)
            extension = segment.platform_extensions.get(self._provider, {})
            self._platform_instruction_input.setText(str(extension.get("instruct") or ""))
            self._refresh_smart_advice()
            self._apply_speaker_recommendation(segment, row)
            self._previous_btn.setEnabled(row > 0)
            self._next_btn.setEnabled(row < len(self._segments) - 1)
        finally:
            self._loading = False

    def _on_segment_type_changed(self) -> None:
        if self._loading:
            return
        selected_type = SegmentType(
            str(self._segment_type_combo.currentData() or SegmentType.NARRATION.value)
        )
        self._refresh_confirmation_action(selected_type)
        self._speaker_combo.setEnabled(
            not self._performance_only
            and selected_type in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        )
        self._distance_combo.setEnabled(selected_type == SegmentType.NARRATION)
        self._refresh_smart_advice()

    def _refresh_confirmation_action(self, segment_type: SegmentType) -> None:
        """Make a pending review explicit when the selected role is narration."""
        if segment_type == SegmentType.NARRATION:
            self._confirm_speaker_btn.setText("确认本段为旁白")
            self._confirm_speaker_btn.setToolTip(
                "确认本段为旁白；上下相邻片段也是旁白时会自动合并。"
            )
            return
        self._confirm_speaker_btn.setText("确认本段说话人")
        self._confirm_speaker_btn.setToolTip(
            "即使系统已给出候选角色，也需要人工确认一次，之后本段才会解除待复核。"
        )

    def _apply_speaker_recommendation(self, segment: DubbingSegment, row: int) -> None:
        """Highlight recommended candidates from adjudication metadata."""
        if not self._row_needs_review(row):
            return
        origin_index = self._segment_origin_indices[row]
        candidates = self._get_speaker_candidates(origin_index)
        if not candidates:
            return
        # Annotate combo items with confidence suffix.
        best_char_id = ""
        best_confidence = 0.0
        best_reason = ""
        for char_id, confidence, reason in candidates:
            if confidence > best_confidence:
                best_confidence = confidence
                best_char_id = char_id
                best_reason = reason
            combo_idx = self._speaker_combo.findData(char_id)
            if combo_idx >= 0:
                base_name = self._speaker_combo.itemText(combo_idx).split(" (推荐")[0]
                self._speaker_combo.setItemText(
                    combo_idx, f"{base_name} (推荐 {int(confidence * 100)}%)"
                )
        # Auto-select the highest confidence candidate.
        if best_char_id:
            best_idx = self._speaker_combo.findData(best_char_id)
            if best_idx >= 0:
                self._speaker_combo.setCurrentIndex(best_idx)
            if best_reason:
                self._advice_label.setText(f"智能提示：{best_reason}")

    def _get_speaker_candidates(
        self, segment_index: int
    ) -> list[tuple[str, float, str]]:
        """Extract (character_id, confidence, rationale) from adjudication metadata."""
        results: list[tuple[str, float, str]] = []
        seen: set[str] = set()
        # Source 1: speaker_adjudication.decisions
        adjudication = self._script.metadata.get("speaker_adjudication")
        if isinstance(adjudication, dict):
            for decision in adjudication.get("decisions") or []:
                if not isinstance(decision, dict):
                    continue
                if int(decision.get("segment_index", -1)) != segment_index:
                    continue
                char_id = str(decision.get("character_id") or "").strip()
                if not char_id or char_id in seen:
                    continue
                try:
                    confidence = float(decision.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                reason = str(decision.get("rationale") or "")
                results.append((char_id, confidence, reason))
                seen.add(char_id)
        # Source 2: professional_script_review.llm_review.manual_review_items
        review = self._script.metadata.get("professional_script_review")
        if isinstance(review, dict):
            llm_review = review.get("llm_review")
            if isinstance(llm_review, dict):
                for item in llm_review.get("manual_review_items") or []:
                    if not isinstance(item, dict):
                        continue
                    if int(item.get("segment_index", -1)) != segment_index:
                        continue
                    char_id = str(item.get("recommended_character_id") or "").strip()
                    if not char_id or char_id in seen:
                        continue
                    try:
                        confidence = float(item.get("confidence", 0.0))
                    except (TypeError, ValueError):
                        confidence = 0.0
                    reason = str(item.get("rationale") or "")
                    results.append((char_id, confidence, reason))
                    seen.add(char_id)
        results.sort(key=lambda item: item[1], reverse=True)
        return results

    def _commit_current(self) -> bool:
        if self._current_row < 0:
            return True
        text = self._text_input.toPlainText().strip()
        if not text:
            self._advice_label.setText("请保留本段文本；如需删除，请先在脚本编辑器外确认结构调整。")
            return False
        previous = self._segments[self._current_row]
        selected_type = (
            previous.segment_type
            if self._performance_only
            else SegmentType(
                str(self._segment_type_combo.currentData() or SegmentType.NARRATION.value)
            )
        )
        emotion_value = str(self._emotion_combo.currentData() or EmotionTag.NEUTRAL.value)
        stress_words = [
            value.strip()
            for value in re.split(r"[,，]", self._stress_input.text())
            if value.strip()
        ]
        pronunciation_overrides = [
            value.strip()
            for value in re.split(r"[;；]", self._pronunciation_input.text())
            if value.strip()
        ]
        language_code = str(self._language_combo.currentData() or "auto")
        platform_extensions = {
            key: dict(value) for key, value in previous.platform_extensions.items()
        }
        provider_extension = platform_extensions.setdefault(self._provider, {})
        instruction = self._platform_instruction_input.text().strip()
        if instruction:
            provider_extension["instruct"] = instruction
        else:
            provider_extension.pop("instruct", None)
        language_boost = provider_language_value(
            self._provider,
            language_code,
            previous.language_boost,
        )
        if self._provider == TTSProvider.MINIMAX.value:
            provider_extension["language_boost"] = language_boost
            provider_extension["voice_effect"] = previous.voice_effect.model_copy(
                update={"sound_effects": str(self._voice_effect_combo.currentData() or "")}
            ).model_dump(mode="json")
        elif not provider_extension:
            platform_extensions.pop(self._provider, None)
        language_runs = list(previous.language_runs)
        if text != previous.synthesis_text or language_code != previous.language_code:
            language_runs = [LanguageRun(language=language_code, text=text)]
        text_update = (
            {
                "spoken_text": "" if text == previous.text else text,
            }
            if self._performance_only
            else {"text": text}
        )
        speaker_update: dict[str, str] = {}
        if not self._performance_only and selected_type in {
            SegmentType.DIALOGUE,
            SegmentType.INNER_THOUGHT,
        }:
            character_id = str(self._speaker_combo.currentData() or "")
            character_name = self._speaker_combo.currentText() if character_id else ""
            speaker_update = {
                "character_id": character_id,
                "character_name": character_name,
            }
        elif not self._performance_only:
            speaker_update = {"character_id": "", "character_name": ""}
        if not self._performance_only and (
            selected_type != previous.segment_type
            or speaker_update.get("character_id", previous.character_id) != previous.character_id
        ):
            self._speaker_changed_indices.add(self._segment_origin_indices[self._current_row])
        self._segments[self._current_row] = previous.model_copy(
            update={
                **text_update,
                **speaker_update,
                "segment_type": selected_type,
                "spoken_text": (
                    text_update.get("spoken_text", previous.spoken_text)
                    if self._performance_only and selected_type != SegmentType.NARRATION
                    else ""
                ),
                "emotion": EmotionTag(emotion_value),
                "emotion_intensity": self._intensity_slider.value() / 100.0,
                "tone_hint": self._tone_input.text().strip(),
                "speed_override": self._speed_combo.currentData(),
                "vol_override": self._volume_combo.currentData(),
                "pitch_override": self._pitch_combo.currentData(),
                "stress_words": stress_words,
                "narrator_distance": (
                    str(self._distance_combo.currentData() or "")
                    if selected_type == SegmentType.NARRATION
                    else ""
                ),
                "pronunciation_overrides": pronunciation_overrides,
                "language_boost": language_boost,
                "language_code": language_code,
                "language_runs": language_runs,
                "vocal_direction": previous.vocal_direction.model_copy(
                    update={
                        "delivery_style": str(
                            self._delivery_style_combo.currentData() or "natural"
                        ),
                        "energy": float(self._energy_combo.currentData() or 0.5),
                        "articulation": float(self._articulation_combo.currentData() or 0.6),
                        "breathiness": float(self._breathiness_combo.currentData() or 0.2),
                        "tension": float(self._tension_combo.currentData() or 0.3),
                        "intent": self._intent_input.text().strip(),
                    }
                ),
                "platform_extensions": platform_extensions,
                "voice_effect": previous.voice_effect.model_copy(
                    update={"sound_effects": str(self._voice_effect_combo.currentData() or "")}
                ),
            }
        )
        item = self._segment_list.item(self._current_row)
        if item is not None:
            item.setText(
                self._segment_label(
                    self._segments[self._current_row],
                    display_number=self._current_row + 1,
                )
            )
            item.setToolTip(text)
            self._apply_segment_review_style(item, self._current_row)
        self._refresh_review_state()
        return True

    def _refresh_smart_advice(self) -> None:
        if self._current_row < 0:
            return
        selected_type = SegmentType(
            str(self._segment_type_combo.currentData() or SegmentType.NARRATION.value)
        )
        text = self._text_input.toPlainText().strip()
        compact_length = len(re.sub(r"\s+", "", text))
        speed = self._speed_combo.currentData() or 1.0
        estimated_seconds = max(1, round(compact_length / (4.2 * float(speed))))
        suggestions = [f"约 {compact_length} 字，按当前语速预计 {estimated_seconds} 秒。"]
        if text and text[-1] not in "。！？!?…":
            suggestions.append("句末缺少停顿标点，可应用安全建议补全。")
        if compact_length > 90:
            suggestions.append("本段偏长，建议在语义转折处拆为两段，便于听感和重录。")
        if (
            selected_type in {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
            and not self._speaker_combo.currentData()
        ):
            suggestions.append("对白未标注角色，建议补充角色名称以避免音色误配。")
        if selected_type == SegmentType.NARRATION and self._speaker_combo.currentData():
            suggestions.append("旁白段会使用旁白音色，不会保留角色说话人。")
        if self._emotion_combo.currentData() == EmotionTag.NEUTRAL.value and any(
            mark in text for mark in "！？?!"
        ):
            suggestions.append("文本包含强语气标点，可按上下文选择更明确的情绪标签。")
        self._estimate_label.setText(f"预计时长：{estimated_seconds} 秒")
        emotion = str(self._emotion_combo.currentText() or "中性")
        tone = self._tone_input.text().strip() or "自然表达"
        speed_text = self._speed_combo.currentText()
        volume_text = self._volume_combo.currentText()
        pitch_text = self._pitch_combo.currentText()
        extras: list[str] = []
        if self._stress_input.text().strip():
            extras.append("已标重音")
        if self._pronunciation_input.text().strip():
            extras.append("已设发音字典")
        if self._voice_effect_combo.currentData():
            extras.append(f"空间效果「{self._voice_effect_combo.currentText()}」")
        extras.append(
            f"{self._delivery_style_combo.currentText()} · {self._energy_combo.currentText()}"
        )
        if self._platform_instruction_input.text().strip():
            extras.append(f"{self._provider} 原生补充")
        extras_text = f" 附加：{' · '.join(extras)}。" if extras else ""
        self._synthesis_direction_label.setText(
            "合成指令："
            f"情绪「{emotion}」· 语气「{tone}」· 语速「{speed_text}」· "
            f"音量「{volume_text}」· 音高「{pitch_text}」。{extras_text}"
        )
        self._advice_label.setText("智能提示：" + "  ".join(suggestions))

    @staticmethod
    def _set_nearest_combo_value(combo: QComboBox, value: float) -> None:
        if combo.count() <= 0:
            return
        index = min(
            range(combo.count()),
            key=lambda item: abs(float(combo.itemData(item)) - float(value)),
        )
        combo.setCurrentIndex(index)

    def _refresh_platform_mapping_hint(self) -> None:
        profile = provider_field_profile(self._provider)
        grouped: dict[FieldSupport, list[str]] = {
            FieldSupport.NATIVE: [],
            FieldSupport.PORTABLE: [],
            FieldSupport.DIRECTOR_ONLY: [],
            FieldSupport.UNSUPPORTED: [],
        }
        labels = {
            "emotion": "情绪",
            "tone": "语气",
            "speed": "语速",
            "volume": "音量",
            "pitch": "音高",
            "stress": "重音",
            "paralinguistic": "副语言",
            "pronunciation": "发音",
            "language": "语言",
            "delivery_style": "表达风格",
            "energy": "能量",
            "articulation": "吐字",
            "breathiness": "气声",
            "resonance": "共鸣",
            "tension": "张力",
            "intimacy": "亲密度",
            "spatial_effect": "空间效果",
        }
        for field, mapping in profile.mappings.items():
            grouped[mapping.support].append(labels.get(field.value, field.value))
        self._platform_mapping_hint.setText(
            f"当前平台：{provider_ui_spec(self._provider).label}　"
            f"原生：{'、'.join(grouped[FieldSupport.NATIVE]) or '无'}　|　"
            f"本地补齐：{'、'.join(grouped[FieldSupport.PORTABLE]) or '无'}　|　"
            f"仅作导演提示：{'、'.join(grouped[FieldSupport.DIRECTOR_ONLY]) or '无'}"
        )

    def _apply_safe_suggestion(self) -> None:
        text = self._text_input.toPlainText().strip()
        changes: list[str] = []
        if text and text[-1] not in "。！？!?…":
            self._text_input.setPlainText(f"{text}。")
            changes.append("已补充句末停顿")
        if self._emotion_combo.currentData() == EmotionTag.NEUTRAL.value and "？" in text:
            target = self._emotion_combo.findData(EmotionTag.SURPRISED.value)
            if target >= 0:
                self._emotion_combo.setCurrentIndex(target)
                changes.append("已建议为“惊讶”情绪")
        self._refresh_smart_advice()
        if changes:
            self._advice_label.setText("智能提示：" + "；".join(changes) + "。请检查后再保存。")
        else:
            self._advice_label.setText("智能提示：当前片段没有可安全自动应用的修改。")

    def _revert_current_segment(self) -> None:
        if self._current_row < 0:
            return
        origin_index = self._segment_origin_indices[self._current_row]
        original = next(
            (
                segment
                for segment in self._original_segments
                if segment.segment_index == origin_index
            ),
            None,
        )
        if original is None:
            return
        self._segments[self._current_row] = original.model_copy(
            deep=True,
            update={"segment_index": self._current_row},
        )
        self._speaker_changed_indices.discard(origin_index)
        self._load_segment(self._current_row)

    def _delete_current_segment(self) -> None:
        """Delete one staged segment and keep all index-based references valid."""
        if self._performance_only or self._current_row < 0:
            return
        if len(self._segments) <= 1:
            self._advice_label.setText("智能提示：脚本至少需要保留一个片段。")
            return
        if not self._commit_current():
            return
        segment = self._segments[self._current_row]
        if not ask_confirmation(
            self,
            "删除脚本片段",
            f"确定删除第 {segment.segment_index + 1} 段吗？",
            informative_text=(
                "删除仅在当前编辑窗口中生效；点击“保存脚本”后，"
                "系统会重排片段编号，并将旧音频与字幕标记为待重建。"
            ),
            confirm_text="删除此段",
            confirm_variant="danger",
        ):
            return

        row = self._current_row
        removed_origin = self._segment_origin_indices.pop(row)
        self._segments.pop(row)
        self._speaker_changed_indices.add(removed_origin)
        self._segments = [
            item.model_copy(update={"segment_index": index})
            for index, item in enumerate(self._segments)
        ]
        self._current_row = -1
        self._loading = True
        try:
            self._populate_segment_list()
        finally:
            self._loading = False
        self._segment_list.setCurrentRow(min(row, len(self._segments) - 1))
        self._refresh_review_state()

    def _move_segment(self, delta: int) -> None:
        target = self._current_row + delta
        if 0 <= target < len(self._segments):
            self._segment_list.setCurrentRow(target)

    def _save(self) -> None:
        if self._commit_current():
            self.accept()

    @property
    def has_changes(self) -> bool:
        return self._segments != self._original_segments

    def edited_script(self) -> DubbingScript:
        """Return the current working copy after committing the selected segment."""
        self._commit_current()
        metadata = dict(self._script.metadata)
        origin_to_current = {
            origin: current for current, origin in enumerate(self._segment_origin_indices)
        }

        def remap_reference(index: int | None) -> int | None:
            if index is None:
                return None
            remapped_origin = index
            while remapped_origin in self._merged_origin_indices:
                remapped_origin = self._merged_origin_indices[remapped_origin]
            if remapped_origin in origin_to_current:
                return origin_to_current[remapped_origin]
            surviving = sorted(origin_to_current)
            if not surviving:
                return None
            nearest_origin = next(
                (origin for origin in surviving if origin > remapped_origin),
                surviving[-1],
            )
            return origin_to_current[nearest_origin]

        adjudication = metadata.get("speaker_adjudication")
        if isinstance(adjudication, dict):
            adjudication = dict(adjudication)
            unresolved: set[int] = set()
            for value in adjudication.get("unresolved_segment_indices", []):
                try:
                    origin_index = int(value)
                except (TypeError, ValueError):
                    continue
                if origin_index in self._speaker_changed_indices:
                    continue
                remapped = remap_reference(origin_index)
                if remapped is not None:
                    unresolved.add(remapped)

            manual_reviews: list[dict[str, Any]] = []
            for review in adjudication.get("manual_reviews") or []:
                if not isinstance(review, dict):
                    continue
                raw_origin_index = review.get("segment_index")
                if not isinstance(raw_origin_index, (int, float, str)):
                    continue
                try:
                    origin_index = int(raw_origin_index)
                except (TypeError, ValueError):
                    continue
                remapped = remap_reference(origin_index)
                if remapped is not None:
                    manual_reviews.append({**review, "segment_index": remapped})
            for origin_index in sorted(self._speaker_changed_indices):
                current_index = origin_to_current.get(origin_index)
                if current_index is None:
                    continue
                segment = self._segments[current_index]
                manual_reviews.append(
                    {
                        "segment_index": current_index,
                        "segment_type": segment.segment_type.value,
                        "character_id": segment.character_id,
                        "character_name": segment.character_name,
                    }
                )
            adjudication["unresolved_segment_indices"] = sorted(unresolved)
            adjudication["manual_reviews"] = manual_reviews
            adjudication["status"] = "passed" if not unresolved else "needs_review"
            metadata["speaker_adjudication"] = adjudication

        professional_review = metadata.get("professional_script_review")
        if isinstance(professional_review, dict):
            professional_review = dict(professional_review)
            llm_review = professional_review.get("llm_review")
            if isinstance(llm_review, dict):
                llm_review = dict(llm_review)
                remapped_items: list[dict[str, Any]] = []
                for review in llm_review.get("manual_review_items") or []:
                    if not isinstance(review, dict):
                        continue
                    raw_origin_index = review.get("segment_index")
                    if not isinstance(raw_origin_index, (int, float, str)):
                        continue
                    try:
                        origin_index = int(raw_origin_index)
                    except (TypeError, ValueError):
                        continue
                    if origin_index in self._speaker_changed_indices:
                        continue
                    remapped = remap_reference(origin_index)
                    if remapped is not None:
                        remapped_items.append({**review, "segment_index": remapped})
                unresolved_review_indices = sorted(
                    {
                        int(item["segment_index"])
                        for item in remapped_items
                        if "segment_index" in item
                    }
                )
                llm_review["manual_review_items"] = remapped_items
                llm_review["manual_review_segment_indices"] = unresolved_review_indices
                if not unresolved_review_indices and llm_review.get("status") == "needs_review":
                    llm_review["status"] = (
                        "passed" if llm_review.get("coverage_complete", True) else "incomplete"
                    )
                professional_review["llm_review"] = llm_review
                metadata["professional_script_review"] = professional_review

        updated_bgm = []
        for bgm_cue in self._script.bgm_suggestions:
            start = remap_reference(bgm_cue.start_segment_index)
            end = remap_reference(bgm_cue.end_segment_index)
            if start is not None and end is not None and start > end:
                start, end = end, start
            updated_bgm.append(
                bgm_cue.model_copy(update={"start_segment_index": start, "end_segment_index": end})
            )
        updated_sfx = [
            sfx_cue.model_copy(
                update={"trigger_segment_index": remap_reference(sfx_cue.trigger_segment_index)}
            )
            for sfx_cue in self._script.sfx_cues
        ]
        updated_soundscapes = []
        for soundscape_cue in self._script.soundscapes:
            start = remap_reference(soundscape_cue.start_segment_index)
            end = remap_reference(soundscape_cue.end_segment_index)
            if start is not None and end is not None and start > end:
                start, end = end, start
            updated_soundscapes.append(
                soundscape_cue.model_copy(
                    update={"start_segment_index": start, "end_segment_index": end}
                )
            )
        total_duration = sum(
            int(len(segment.synthesis_text) * 200 / (segment.speed_override or 1.0))
            for segment in self._segments
        )
        return self._script.model_copy(
            update={
                "segments": list(self._segments),
                "bgm_suggestions": updated_bgm,
                "sfx_cues": updated_sfx,
                "soundscapes": updated_soundscapes,
                "total_estimated_duration_ms": total_duration,
                "metadata": metadata,
            }
        )
